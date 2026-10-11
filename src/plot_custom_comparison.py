"""Plot the audited NF4 and deployment-quantization panels from frozen reports."""
import argparse
import json
from pathlib import Path


def plot(audit, quantized, output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    reports = [('NF4 · displayed-name audit', json.loads(audit.read_text())),
               ('Q4_0 · native serving comparison', json.loads(quantized.read_text()))]
    metrics = [('mtgish_accepted', 'Parser accepted'),
               ('faithful_and_parsed', 'Faithful + parsed'),
               ('faithful_clean_oracle', 'Faithful + clean style')]
    fig, axes = plt.subplots(2, 2, figsize=(12, 7.5), sharey=True)
    for column, (title, report) in enumerate(reports):
        for row, panel in enumerate(['custom_holdout', 'existing_development']):
            axis = axes[row, column]
            values = report['panels'][panel]
            for offset, arm, label, color in [(-.18, 'dpo', 'DPO2', '#3979a3'),
                                             (.18, 'custom', 'Custom SFT', '#bc6f18')]:
                counts = [values['counts'][arm][metric] for metric, _ in metrics]
                bars = axis.bar([i + offset for i in range(len(metrics))],
                                [100 * count / values['cases'] for count in counts],
                                width=.34, label=label, color=color)
                axis.bar_label(bars, labels=[f"{n}/{values['cases']}" for n in counts], fontsize=8, padding=3)
            axis.set_title(f"{title}\n{panel.replace('_', ' ')} · {values['families']} families", fontsize=11)
            axis.set_xticks(range(len(metrics)), [label for _, label in metrics], fontsize=9)
            axis.set_ylim(0, 111)
            axis.set_yticks([0, 25, 50, 75, 100])
            axis.grid(axis='y', alpha=.2)
            axis.set_axisbelow(True)
            if column == 0:
                axis.set_ylabel('Requests passing (%)')
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(handles, labels, loc='upper center', ncol=2, frameon=False)
    fig.tight_layout(rect=(0, .04, 1, .95))
    fig.text(.5, .02, 'All failures included. Sol intent/style labels; parser acceptance is not rules correctness. See paired intervals in the report.', ha='center', fontsize=9)
    output.parent.mkdir(parents=True, exist_ok=True)
    for extension in ['png', 'svg']:
        fig.savefig(output.with_suffix('.' + extension), dpi=160)
    plt.close(fig)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audit', type=Path, required=True)
    parser.add_argument('--quantized', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    plot(**vars(parser.parse_args()))
