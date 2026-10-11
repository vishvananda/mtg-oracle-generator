"""Plot paired repair outcomes without conflating parser support and correctness."""
import argparse
import json
from pathlib import Path


def plot(report_path, output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    report = json.loads(report_path.read_text())
    labels = {'synthetic': 'Wording repairs', 'edit': 'Requested edits',
              'noop': 'Valid-card controls', 'real': 'Unsupported controls',
              'generation': 'Normal generation'}
    names = [name for name in labels if name in report['panels']]
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8), layout='constrained')
    for arm, offset, color, label in [('dpo', -.18, '#386f9b', 'Deployed DPO2'),
                                     ('repair', .18, '#3b916b', 'Repair continuation')]:
        values = [100*report['panels'][p]['counts'][arm]['successful']/report['panels'][p]['cases'] for p in names]
        bars = axes[0].barh([i+offset for i in range(len(names))], values, .34, color=color, label=label)
        for name, bar in zip(names, bars):
            panel = report['panels'][name]
            axes[0].text(bar.get_width()+1, bar.get_y()+bar.get_height()/2,
                         f"{panel['counts'][arm]['successful']}/{panel['cases']}", va='center', fontsize=8)
    axes[0].set(yticks=range(len(names)), yticklabels=[labels[n] for n in names],
                xlabel='Successful outputs (%)', xlim=(0, 120), title='Same Q4_0 quantization and prompts')
    axes[0].set_xticks([0, 25, 50, 75, 100]); axes[0].invert_yaxis(); axes[0].legend(frameon=False, fontsize=8)
    for i, name in enumerate(names):
        panel = report['panels'][name]
        value = panel['success_difference_pp']; low, high = panel['family_bootstrap_95_percent_interval_pp']
        axes[1].plot([low, high], [i, i], color='#386f9b', linewidth=2)
        axes[1].scatter([value], [i], color='#244b69', s=35, zorder=3)
    axes[1].axvline(0, color='#666', linewidth=.8, linestyle='--')
    axes[1].set(yticks=range(len(names)), yticklabels=[labels[n] for n in names],
                xlabel='Repair minus DPO2 (percentage points)', title='Paired family-bootstrap 95% intervals')
    axes[1].invert_yaxis()
    for ax in axes:
        ax.spines[['top', 'right']].set_visible(False); ax.grid(axis='x', alpha=.15); ax.set_axisbelow(True)
    fig.suptitle('Oracle repair pilot · frozen development comparison', fontweight='bold')
    fig.supxlabel('Success requires preserved meaning and parser acceptance, except unsupported controls require unchanged output.\n'
                  'Model-judged semantics; diagnostic panels; intervals not adjusted for multiple comparisons.', fontsize=8)
    output.mkdir(parents=True, exist_ok=True)
    for suffix in ('png', 'svg'):
        fig.savefig(output/('repair-comparison.'+suffix), dpi=180)
    path = output/'repair-comparison.svg'
    path.write_text('\n'.join(line.rstrip() for line in path.read_text().splitlines())+'\n')
    plt.close(fig)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    plot(args.report, args.output)
