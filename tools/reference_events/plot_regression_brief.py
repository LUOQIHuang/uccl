"""Create source-linked report figures for the Normal BF16 regression finding."""
import argparse
import hashlib
import json
from pathlib import Path
from decimal import Decimal,ROUND_HALF_UP

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyBboxPatch,FancyArrowPatch
import numpy as np
from plot_stage_summary import read,one,VERBS,URMA,NODE0,NODE1,LABEL


def rounded(value,digits=2):
    return str(Decimal(str(value)).quantize(Decimal('1').scaleb(-digits),rounding=ROUND_HALF_UP))


def bars(ax,names,values,colors,unit,limit,digits=3):
    artists=ax.barh(np.arange(len(names)),values,height=.53,color=colors)
    ax.bar_label(artists,labels=[rounded(v,digits)+' '+unit for v in values],padding=6,fontsize=12)
    ax.set_yticks(np.arange(len(names)),names);ax.invert_yaxis();ax.set_xlim(0,limit)
    ax.set_axisbelow(True);ax.grid(axis='x',alpha=.14)
    ax.spines[['top','right','left']].set_visible(False)
    ax.tick_params(axis='y',length=0,pad=8)
    return artists


def save(fig,output,name):
    fig.savefig(output/(name+'.png'),dpi=180,facecolor='white')
    fig.savefig(output/(name+'.svg'),facecolor='white')
    plt.close(fig)


def build(root,output,font):
    font_manager.fontManager.addfont(str(font))
    plt.rcParams.update({'font.family':font_manager.FontProperties(fname=str(font)).get_name(),
        'axes.unicode_minus':False,'font.size':12,'svg.fonttype':'path'})
    sources={
        'api':root/'backend_matched_comparison_01/api_comparison.csv',
        'notify':root/'notify_completion_audit_01/notify_pair_summary.csv',
        'fifo':root/'urma_boundaries_01_fifo_audit/fifo_medians.csv',
        'window':root/'urma_window_after_boundaries_01_summary/rounds.csv',
        'controls':root/'urma_boundaries_01_summary/api_controls.csv'}
    data={k:read(v) for k,v in sources.items()}
    api=one(data['api'],node=0,label=LABEL)
    notify={name:[one(data['notify'],campaign=name,node=n) for n in (0,1)] for name in ('verbs_historical','urma_historical','urma_boundaries')}
    fifo=[one(data['fifo'],node=n,label=LABEL) for n in (0,1)]
    rounds=[one(data['window'],repeat=n,label=LABEL) for n in (1,2)]
    assert all(one(data['controls'],node=n,label=LABEL)['representative']=='True' for n in (0,1))
    assert all(r['pairs']==r['atomic_preposted']=='1188' for rr in notify.values() for r in rr)
    assert all(float(r['peer4_change_pct'])>0 for r in rounds)
    output.mkdir(parents=True,exist_ok=False)

    fig=plt.figure(figsize=(9.6,13.8))
    fig.text(.045,.970,'URMA Normal BF16：性能回退的阶段性发现',fontsize=20,weight='medium')
    fig.text(.045,.942,'初步定位到 ATOMIC完成推进 → FIFO回收阻塞 → 后续WRITE排队',fontsize=13)
    fig.text(.045,.921,'4096 tokens · hidden7168 · topk8 · 两节点各1张MetaX C500',fontsize=11,color='#59616b')

    fig.text(.045,.888,'① 整体回退：202端 Dispatch 慢 '+rounded(api['paced_us_urma_change_pct'])+'%',fontsize=16,weight='medium')
    ax=fig.add_axes([.255,.758,.67,.105])
    bars(ax,['verbs','URMA'],[float(api[k])/1000 for k in ('paced_us_verbs','paced_us_urma')],[VERBS,URMA],'ms',10)
    ax.set_xlabel('Dispatch API均值 / ms（关闭采集）',fontsize=11)
    fig.text(.045,.714,'历史匹配对照：增加 '+rounded(float(api['paced_us_urma_minus_verbs'])/1000,3)+' ms；每组9个有效样本。',fontsize=11,color='#59616b')

    fig.text(.045,.675,'② 具体差异：WRITE完成观测后，通知FADD仍较晚完成观测',fontsize=15.5,weight='medium')
    ax=fig.add_axes([.255,.507,.67,.14])
    values=[float(notify[c][1]['completion_gap_median_us']) for c in ('verbs_historical','urma_historical','urma_boundaries')]
    artists=bars(ax,['verbs 历史','URMA 历史','URMA 本轮'],values,[VERBS,URMA,URMA],'μs',830,2)
    artists[-1].set_hatch('//')
    ax.set_xlabel('204端：WRITE CQE → 对应FADD CQE，中位间隔 / μs',fontsize=10.5)
    fig.text(.045,.463,'每组1188对；FADD全部已在WRITE完成观测前提交。',fontsize=11,color='#59616b')

    fig.text(.045,.428,'③ 阻塞证据：末块命令已发布，CPU仍在等软件名额释放',fontsize=15.5,weight='medium')
    ax=fig.add_axes([.255,.298,.67,.103])
    labels=[str(202+n*2)+'端\n满额占比 '+rounded(fifo[n]['full_pct'])+'%' for n in (0,1)]
    bars(ax,labels,[float(r['published_to_poll_us'])/1000 for r in fifo],[NODE0,NODE1],'ms',4.55)
    ax.set_xlabel('末块命令发布 → CPU读出，中位间隔 / ms',fontsize=11)
    fig.text(.045,.250,'每端108条观察；软件pending=8占满。比例仅对应这段等待，不是整个Dispatch。',fontsize=10.5,color='#59616b')

    fig.text(.045,.216,'已观察到的等待链',fontsize=15.5,weight='medium')
    labels=['ATOMIC完成\n观测较迟','队首未完成\n无法按序回收','8个软件\n名额占满','后续WRITE\n晚读取、晚提交']
    xs=[.055,.285,.515,.745]
    for x,label in zip(xs,labels):
        fig.add_artist(FancyBboxPatch((x,.155),.195,.043,boxstyle='round,pad=0.004',transform=fig.transFigure,facecolor='#eef1f4',edgecolor='none'))
        fig.text(x+.0975,.1765,label,ha='center',va='center',fontsize=11.5)
    for x in xs[:-1]:fig.add_artist(FancyArrowPatch((x+.202,.1765),(x+.224,.1765),transform=fig.transFigure,arrowstyle='-|>',mutation_scale=13,color='#66717c',linewidth=1.2))
    fig.text(.045,.128,'满额且队首ATOMIC尚无CQE观测：202 '+rounded(fifo[0]['atomic_head_no_observed_cqe_pct'])+'% / 204 '+rounded(fifo[1]['atomic_head_no_observed_cqe_pct'])+'%。',fontsize=11)

    fig.text(.045,.095,'因果干预：204窗口8→4未获净收益，保留8',fontsize=14,weight='medium')
    fig.text(.045,.075,'ABBA两轮变化：+'+rounded(rounds[0]['peer4_change_pct'])+'%（CV '+rounded(rounds[0]['max_cv_pct'])+'%，超限）；+'
        +rounded(rounds[1]['peer4_change_pct'])+'%（CV '+rounded(rounds[1]['max_cv_pct'])+'%）。',fontsize=11)
    fig.text(.045,.043,'已定位阻塞机制；顺序/fence/设备及远端atomic访问等底层原因仍待验证。',fontsize=11.5)
    fig.text(.045,.022,'CQE为软件观测，非纯硬件atomic时延；后端内核/设备差异保留；各区间不可相加。',fontsize=10,color='#59616b')
    save(fig,output,'01-report-overview')

    fig,ax=plt.subplots(figsize=(10.4,4.8))
    y=np.arange(2);height=.22
    for offset,campaign,name,color,hatch in [(-height,'verbs_historical','verbs 历史匹配采集',VERBS,None),
        (0,'urma_historical','URMA 历史匹配采集',URMA,None),(height,'urma_boundaries','URMA 本轮复现',URMA,'//')]:
        values=[float(r['completion_gap_median_us']) for r in notify[campaign]]
        artist=ax.barh(y+offset,values,height=height*.90,label=name,color=color,hatch=hatch)
        ax.bar_label(artist,labels=[rounded(v)+' μs' for v in values],padding=5,fontsize=12)
    ax.set_yticks(y,['202端','204端']);ax.invert_yaxis();ax.set_xlim(0,850)
    ax.set_axisbelow(True);ax.grid(axis='x',alpha=.15);ax.spines[['top','right']].set_visible(False)
    ax.set_xlabel('WRITE完成观测 → 对应通知FADD完成观测，中位间隔 / μs')
    ax.legend(frameon=False,ncol=2,loc='lower left',bbox_to_anchor=(0,1.01),fontsize=10)
    fig.suptitle('通知FADD完成推进：两端均有同量级差异，URMA再次复现',x=.03,ha='left',fontsize=17,weight='medium')
    fig.text(.03,.035,'每组1188对；全部FADD已提前提交。两个CQE的间隔不等同于纯硬件atomic时延，也不可累计到API时延。',fontsize=10)
    fig.subplots_adjust(left=.14,right=.96,bottom=.23,top=.73)
    save(fig,output,'02-notify-detail')

    (output/'figure_sources.json').write_text(json.dumps(dict(
        source_sha256={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources.values()},
        notes='Normal BF16 only. Historical overall API means; historical matched notify pairs plus separately labeled current URMA replication; latest URMA FIFO channel medians. Full percentages describe each publication-to-poll interval, not whole API time. No additive critical-path or unique root-cause claim. Window intervention failed to show benefit.'),indent=2),encoding='utf-8')
    print('REGRESSION_BRIEF_EXPORTED',output)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path('test/reference_events/results'))
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--font',type=Path,default=Path('C:/Windows/Fonts/msyh.ttc'))
    args=parser.parse_args();build(args.root,args.output,args.font)
