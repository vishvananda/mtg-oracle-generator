"""Publish aggregate quantization diagnostics without private card descriptions."""
import argparse
from collections import defaultdict
import json
from math import comb
from pathlib import Path
import random

from rl_gpu import save, sha


def paired(rows, baseline, candidate, panel=None):
    selected = {arm: [r for r in rows if r['arm'] == arm and
                     (panel is None or r['panel'] == panel)] for arm in (baseline, candidate)}
    arms = {arm: {r['case_id']: r for r in selected[arm]} for arm in selected}
    if any(len(arms[arm]) != len(selected[arm]) for arm in arms):
        raise ValueError('Duplicate scored case')
    if set(arms[baseline]) != set(arms[candidate]) or not arms[baseline]:
        raise ValueError('Unpaired comparison')
    groups = defaultdict(list)
    gains = losses = 0
    for key in sorted(arms[baseline]):
        first, second = arms[baseline][key], arms[candidate][key]
        if first['family'] != second['family'] or first['panel'] != second['panel']:
            raise ValueError('Paired case metadata changed')
        difference = int(second['faithful_and_parsed'])-int(first['faithful_and_parsed'])
        groups[first['family']].append(difference)
        gains += difference == 1
        losses += difference == -1
    groups = list(groups.values()); rng = random.Random(43); samples = []
    for _ in range(5000):
        sample = [groups[rng.randrange(len(groups))] for _ in groups]
        samples.append(100*sum(map(sum, sample))/sum(map(len, sample)))
    samples.sort()
    # A bootstrap can collapse when all observed differences are zero, or be
    # overconfident at a boundary with very few discordant pairs. For panels
    # with one observation per family, also report the exact paired sign test.
    discordant = gains+losses
    exact_p = (min(1.0, 2*sum(comb(discordant, i) for i in range(min(gains, losses)+1))/2**discordant)
               if len(groups) == len(arms[baseline]) else None)
    return {'cases': len(arms[baseline]), 'families': len(groups), 'gained': gains, 'lost': losses,
            'difference_pp': 100*sum(map(sum, groups))/sum(map(len, groups)),
            'exact_paired_discordance_p_value': exact_p,
            'family_bootstrap_95_percent_interval_pp': [samples[124], samples[4874]]}


def summarize(merged, adapters, joint, output):
    cases = {c['id']: c for c in map(json.loads, (merged/'cases.jsonl').read_text().splitlines())}
    report = json.loads((joint/'evaluation/comparison.json').read_text())
    if report['cases_sha256'] != sha(merged/'cases.jsonl'):
        raise ValueError('Scored cases differ from the frozen comparison')
    rows = []
    for source in map(json.loads, (joint/'evaluation/cases.jsonl').read_text().splitlines()):
        case = cases[source['case_id']]; parsed = source['checks'].get('mtgish', {}).get('parse_complete') is True
        rows.append({'arm': source['arm'], 'case_id': source['case_id'], 'family': case['source_group_id'],
            'panel': case['panel'], 'schema_valid': bool(source['checks']['schema_valid']),
            'intent_faithful': bool(source['intent_faithful']), 'mtgish_accepted': parsed,
            'parser_status': source['checks'].get('mtgish', {}).get('status'),
            'faithful_and_parsed': bool(source['intent_faithful'] and parsed)})
    sizes = {}; weights = {}
    for root in (merged, adapters):
        for arm, setup in json.loads((root/'experiment.json').read_text())['arms'].items():
            sizes[arm] = Path(setup['model']).stat().st_size
            weights[arm] = json.loads((root/'predictions'/(arm+'-identity.json')).read_text())
            for key in ('cases_sha256', 'system_sha256', 'model_sha256', 'adapter_sha256'):
                if weights[arm].get(key) != report['predictions'][arm].get(key):
                    raise ValueError('Scored model identity differs: '+arm)
            if setup.get('adapter'):
                sizes[arm] += Path(setup['adapter']).stat().st_size
    fields = ('schema_valid', 'intent_faithful', 'mtgish_accepted', 'faithful_and_parsed')
    if set(report['metrics']) != set(sizes) or {r['arm'] for r in rows} != set(sizes):
        raise ValueError('Scored arms differ from the experiment')
    for arm in sizes:
        selected = [r for r in rows if r['arm'] == arm]
        if len(selected) != len(cases) or {r['case_id'] for r in selected} != set(cases):
            raise ValueError('Missing or duplicate scored cases: '+arm)
        if any(sum(r[field] for r in selected) != report['metrics'][arm][field] for field in fields):
            raise ValueError('Published counts differ from scored outputs: '+arm)
    variants = ['q4_0', 'q4_k_m', 'q5_k_m', 'q8_0', 'q4_k_m-lora']
    intervals = {'custom_minus_dpo': {}, 'versus_same_model_q4_0': {}}
    for quant in variants:
        intervals['custom_minus_dpo'][quant] = {
            panel or 'all': paired(rows, 'dpo-'+quant, 'custom-'+quant, panel)
            for panel in (None, 'custom_holdout', 'existing_development')}
        if quant != 'q4_0':
            for model in ('dpo', 'custom'):
                intervals['versus_same_model_q4_0'][model+'-'+quant] = paired(rows, model+'-q4_0', model+'-'+quant)
    panel_counts = {}
    for panel in ('custom_holdout', 'existing_development'):
        panel_counts[panel] = {}
        for arm in sizes:
            selected = [r for r in rows if r['arm'] == arm and r['panel'] == panel]
            panel_counts[panel][arm] = {'cases': len(selected), **{f: sum(r[f] for r in selected) for f in fields}}
    output.mkdir(parents=True, exist_ok=True)
    save(output/'comparison.json', {'metrics': report['metrics'], 'panels': panel_counts,
        'paired': intervals, 'download_bytes': sizes, 'identities': weights,
        'cases_sha256': sha(merged/'cases.jsonl'), 'cases': len(cases),
        'families': len({c['source_group_id'] for c in cases.values()}),
        'judge_model': report['judge_model'], 'judge_effort': report['judge_effort'],
        'rubric_sha256': report['rubric_sha256'], 'parser': report['parser'],
        'scored_outputs_sha256': sha(joint/'evaluation/cases.jsonl'),
        'limitations': ['Exploratory development screen, not an untouched final test.',
            '32 independent families per panel, selected before outcomes; one prompt per family.',
            'Intervals are descriptive, unadjusted for multiple comparisons and model-judge dependence.',
            'Bootstrap intervals can degenerate at boundaries; zero observed differences do not establish equivalence.',
            'Native CPU inference; browser GPU output, speed and memory are not measured.',
            'Separate LoRA uses a different quantized base from training NF4; it is not numerically equivalent.',
            'Parser acceptance does not establish semantic fidelity, balance or official rules legality.'],
        'automatic_promotion': False})
    (output/'paired-outcomes.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
    import matplotlib
    matplotlib.use('Agg')
    matplotlib.rcParams['svg.hashsalt'] = 'serving-precision-screen-v1'
    import matplotlib.pyplot as plt
    labels = ['Q4_0', 'Q4_K_M', 'Q5_K_M', 'Q8_0', 'Q4_K_M + F16 LoRA']
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5), sharex=True)
    for ax, model, color in zip(axes, ('dpo', 'custom'), ('#497fac', '#ac7632')):
        values = [report['metrics'][model+'-'+q]['faithful_and_parsed'] for q in variants]
        ax.barh(labels, [100*v/len(cases) for v in values], color=color)
        for i, v in enumerate(values):
            ax.text(100*v/len(cases)+1, i, f'{v}/{len(cases)}', va='center', fontsize=9)
        ax.set_title('Deployed DPO2' if model == 'dpo' else 'Custom SFT candidate')
        ax.set_xlim(0, 100); ax.invert_yaxis(); ax.set_xlabel('Faithful and mtgish-accepted (%)')
        ax.spines[['top', 'right']].set_visible(False)
    fig.suptitle('Serving precision screen: identical prompts and native runtime')
    fig.tight_layout()
    for suffix in ('png', 'svg'):
        destination = output/('serving-comparison.'+suffix)
        fig.savefig(destination, dpi=170, bbox_inches='tight',
                    metadata={'Date': None} if suffix == 'svg' else None)
        if suffix == 'svg':
            destination.write_text('\n'.join(line.rstrip() for line in destination.read_text().splitlines())+'\n')
    plt.close(fig)
    lines = ['| Model / representation | Size (GB) | Schema | Intent | mtgish | Both |',
             '| --- | ---: | ---: | ---: | ---: | ---: |']
    for model in ('dpo', 'custom'):
        for quant, label in zip(variants, labels):
            arm = model+'-'+quant; m = report['metrics'][arm]
            lines.append(f'| {model} / {label} | {sizes[arm]/1e9:.2f} | '+
                         ' | '.join(f'{m[k]}/{len(cases)}' for k in fields)+' |')
    (output/'results-table.md').write_text('\n'.join(lines)+'\n')
    return report['metrics']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('merged', 'adapters', 'joint', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    print(json.dumps(summarize(**vars(parser.parse_args())), indent=2))
