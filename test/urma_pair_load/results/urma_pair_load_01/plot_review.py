"""Offline reproducible figure from the uploaded CSVs; requires matplotlib."""
import csv
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
HERE=Path(__file__).resolve().parent
plt.rcParams['font.family']=['Microsoft YaHei','DejaVu Sans']
plt.rcParams['axes.unicode_minus']=False
with (HERE/'summary_urma/summary.csv').open() as f:summary={r['label']:r for r in csv.DictReader(f)}
with (HERE/'summary_urma/positions.csv').open() as f:positions=list(csv.DictReader(f))
fig,axes=plt.subplots(1,2,figsize=(12,4.6),layout='constrained')
colors=['#738096','#e79135','#267fc1']
for k,(shape,name) in enumerate([('serial4','1队列逐对串行'),('queue4','1队列4对'),('lanes4','4队列各1对')]):
    v=[float(summary[shape+'_%dto%d'%(n,1-n)]['median_run_mean_sdk_span_us']) for n in (0,1)]
    bars=axes[0].bar([n+(k-1)*.24 for n in (0,1)],v,width=.22,color=colors[k],label=name)
    axes[0].bar_label(bars,fmt='%.2f',padding=3,fontsize=9)
axes[0].set(xticks=[0,1],xticklabels=['202 → 204','204 → 202'],ylim=(0,190),ylabel='整批SDK跨度（μs）',title='相同4对：整批没有变慢')
axes[0].legend(loc='upper right',fontsize=9)
for n in (0,1):
    v=[float(next(r['median_gap_us'] for r in positions if r['label']=='queue4_%dto%d'%(n,1-n) and int(r['pair_index'])==j)) for j in range(4)]
    bars=axes[1].bar([j+(n-.5)*.34 for j in range(4)],v,width=.32,color=['#267fc1','#e79135'][n],label=['202 → 204','204 → 202'][n])
    axes[1].bar_label(bars,fmt='%.2f',padding=3,fontsize=9)
axes[1].set(xticks=range(4),xticklabels=['第0对','第1对','第2对','第3对'],ylim=(0,95),ylabel='对应W→A观测间隔中位数（μs）',title='同队列：靠前请求间隔更长')
axes[1].legend(loc='upper right',fontsize=9)
for ax in axes:
    ax.spines[['top','right']].set_visible(False)
    ax.grid(axis='y',alpha=.16);ax.set_axisbelow(True)
fig.suptitle('URMA四对负载：完成间隔增大 ≠ 整批退化',fontsize=15)
fig.savefig(HERE/'pair_load_review.png',dpi=170)
