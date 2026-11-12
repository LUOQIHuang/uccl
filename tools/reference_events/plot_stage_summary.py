"""Export the Normal BF16 investigation's evidence as five static figures.

Reads committed CSV/JSON observations. Does not run benchmarks, subtract
cross-host clocks, or turn overlapping spans into an additive critical path.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import statistics
from decimal import Decimal,ROUND_HALF_UP

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
import numpy as np

LABEL='dispatch_bf16_chunk32'
VERBS,URMA,NODE0,NODE1='#3478b8','#d47829','#12817c','#8b5cb4'


def read(path):
    with path.open(encoding='utf-8',newline='') as stream:return list(csv.DictReader(stream))


def one(rows,**where):
    found=[r for r in rows if all(r[k]==str(v) for k,v in where.items())]
    if len(found)!=1:raise ValueError('non-unique source row: '+str(where))
    return found[0]


def clean(ax):
    ax.spines[['top','right']].set_visible(False)
    ax.set_axisbelow(True)
    ax.grid(axis='x',alpha=.17)


def grouped(ax,labels,a,b,names=('verbs','URMA'),colors=(VERBS,URMA),digits=3):
    y=np.arange(len(labels));height=.30
    for values,offset,name,color in [(a,-height/2,names[0],colors[0]),(b,height/2,names[1],colors[1])]:
        bars=ax.barh(y+offset,values,height=height*.93,label=name,color=color)
        value_labels=[str(Decimal(str(v)).quantize(Decimal('1').scaleb(-digits),rounding=ROUND_HALF_UP)) for v in values]
        ax.bar_label(bars,labels=value_labels,padding=5,fontsize=10)
    ax.set_yticks(y,labels);ax.invert_yaxis()
    ax.set_xlim(0,max(a+b)*1.24)
    clean(ax)


def export(fig,output,name,title,note):
    fig.suptitle(title,x=.03,ha='left',fontsize=16,fontweight='medium')
    fig.text(.03,.025,note,fontsize=10,color='#4b5563',va='bottom')
    fig.subplots_adjust(left=.20,right=.96,top=.78,bottom=.22)
    if len(fig.axes)>1:fig.subplots_adjust(wspace=.85)
    fig.savefig(output/(name+'.png'),dpi=170,facecolor='white')
    fig.savefig(output/(name+'.svg'),facecolor='white')
    plt.close(fig)


def build(root,output,font):
    font_manager.fontManager.addfont(str(font))
    plt.rcParams.update({'font.family':font_manager.FontProperties(fname=str(font)).get_name(),
        'axes.unicode_minus':False,'font.size':11,'axes.labelsize':11,
        'axes.spines.top':False,'axes.spines.right':False,'svg.fonttype':'path'})
    output.mkdir(parents=True,exist_ok=False)
    inputs={
        'api':root/'backend_matched_comparison_01/api_comparison.csv',
        'gpu':root/'backend_matched_comparison_01/gpu_span_comparison.csv',
        'tails':root/'backend_matched_comparison_01/cpu_completion_tails.csv',
        'positions':root/'window_audit_01/payload_positions.csv',
        'window_context':root/'window_audit_01/context.json',
        'current_spans':root/'urma_boundaries_01_summary/boundary_medians.csv',
        'current_focus':root/'urma_boundaries_01_summary/cpu_chunk6_and_last.csv',
        'controls':root/'urma_boundaries_01_summary/api_controls.csv',
        'fifo':root/'urma_boundaries_01_fifo_audit/last_write_fifo.csv',
        'fifo_medians':root/'urma_boundaries_01_fifo_audit/fifo_medians.csv'}
    d={k:read(p) for k,p in inputs.items() if p.suffix=='.csv'}
    controls=[one(d['controls'],node=n,label=LABEL) for n in (0,1)]
    assert all(r['representative']=='True' for r in controls)
    api=[one(d['api'],node=n,label=LABEL) for n in (0,1)]
    fig,ax=plt.subplots(figsize=(10.8,3.9))
    grouped(ax,['202 / 接收慢端','204'],[float(r['paced_us_verbs'])/1000 for r in api],[float(r['paced_us_urma'])/1000 for r in api])
    ax.set_xlabel('Dispatch API均值 / ms（关闭采集）')
    ax.legend(loc='lower left',bbox_to_anchor=(0,1.02),ncol=2,frameon=False)
    export(fig,output,'01-overall','① 确认整体差距：202端BF16 +0.965 ms（+13.50%）',
        '历史同口径对照；每组9个有效样本。204端 −1.81%，回退并非两端均匀发生。')

    g=one(d['gpu'],node=0,label=LABEL)
    fig,ax=plt.subplots(figsize=(10.8,3.9))
    grouped(ax,['首token → 首次WRITE\n同Sender CTA中位数','首次WRITE → 最后远端可见\n本机事件包络中位数'],
        [float(g[k+'_verbs'])/1000 for k in ('sender_cta_0_to_1_median_us','first_write_to_last_visible_us')],
        [float(g[k+'_urma'])/1000 for k in ('sender_cta_0_to_1_median_us','first_write_to_last_visible_us')])
    ax.set_xlabel('202端 BF16 / ms');ax.legend(loc='lower left',bbox_to_anchor=(0,1.02),ncol=2,frameon=False)
    export(fig,output,'02-region','② 收窄候选区间：差异主要出现在首次WRITE之后',
        '首批跨度仅增加18.6 μs；后段包络增加682.5 μs。口径不同且含并行角色，不相加、不计算解释比例。')

    tail=one(d['tails'],node=1,label=LABEL,operation='payload_write')
    positions=[one(d['positions'],backend=backend,node=1,payload_position=6) for backend in ('verbs','urma')]
    focus=[r for r in d['current_focus'] if r['node']=='1' and r['label']==LABEL and r['payload_ordinal']=='6']
    current6=statistics.median(float(r['post_end_to_cqe_us']) for r in focus)
    wc=json.loads(inputs['window_context'].read_text())
    ws=next(r for r in wc['summaries'] if r['backend']=='urma' and r['node']==1)
    assert ws['tail_positions']['6']==59 and ws['tail_requests']==60 and len(focus)==108
    fig,(ax,ax2)=plt.subplots(1,2,figsize=(12.4,4.5),gridspec_kw={'width_ratios':[1.35,1]})
    grouped(ax,['大WRITE P50','大WRITE P95','大WRITE P99'],[float(tail[k+'_us_verbs'])/1000 for k in ('p50','p95','p99')],[float(tail[k+'_us_urma'])/1000 for k in ('p50','p95','p99')])
    ax.set_xlabel('历史204端 post_end → CQE观测 / ms');ax.legend(loc='lower left',bbox_to_anchor=(0,1.02),ncol=2,frameon=False)
    values=[float(r['p50_us'])/1000 for r in positions]+[current6/1000]
    bars=ax2.barh(np.arange(3),values,color=[VERBS,URMA,URMA],height=.53)
    bars[-1].set_hatch('//')
    ax2.bar_label(bars,fmt='%.3f',padding=5,fontsize=10)
    ax2.set_yticks(np.arange(3),['verbs历史','URMA历史','URMA本次'])
    ax2.invert_yaxis();ax2.set_xlim(0,4.3);ax2.set_xlabel('每通道第6块WRITE中位数 / ms');clean(ax2)
    export(fig,output,'03-tail-position','③ 从平均值转向长尾：204端第6块WRITE成为重点',
        '历史URMA最慢5%的60条payload中，59条位于每通道第6块。新旧采集不同，本次3.516 ms仅作复现参照。')
    spans=d['current_spans'];fifo=d['fifo_medians']
    def span(node,name):return float(one(spans,node=node,label=LABEL,span=name)['median_us'])
    def sixth(node):return statistics.median(float(r['post_end_to_cqe_us']) for r in d['current_focus'] if r['node']==str(node) and r['label']==LABEL and r['payload_ordinal']=='6')
    fig,(ax,ax2)=plt.subplots(1,2,figsize=(14,4.9),gridspec_kw={'width_ratios':[1.2,1]})
    large=[[span(n,'sender_first_to_last_ready')/1000,float(one(fifo,node=n,label=LABEL)['published_to_poll_us'])/1000,sixth(n)/1000] for n in (0,1)]
    grouped(ax,['首WRITE → 末payload就绪','末块已发布 → CPU读出','第6块提交后 → CQE'],*large,names=('202','204'),colors=(NODE0,NODE1))
    ax.set_xlabel('较长区间 / ms');ax.legend(loc='lower left',bbox_to_anchor=(0,1.02),ncol=2,frameon=False)
    names=['last_command_publish','final_tail_check_span','final_epoch_check','last_remote_to_receiver_done']
    small=[[span(n,key) for key in names] for n in (0,1)]
    grouped(ax2,['末块命令发布','最后tail检查','最后epoch检查','最后可见 → Receiver结束'],*small,names=('202','204'),colors=(NODE0,NODE1),digits=2)
    ax2.set_xlabel('末尾区间 / μs')
    export(fig,output,'04-boundary-detail','④ 新边界把毫秒级命令排队与微秒级末尾检查分开',
        '本次4096-token BF16；每端9窗×12通道。采集影响202 +2.60%、204 +4.38%，通过5%门槛；区间重叠，不可相加。')

    rr=d['fifo'];assert len(rr)==216 and all(r['pending_at_publication']=='8' for r in rr)
    fig,(ax,ax2)=plt.subplots(1,2,figsize=(12.6,4.8))
    for rank,color,marker in [(0,NODE0,'o'),(1,NODE1,'^')]:
        rows=[r for r in rr if r['node']==str(rank)]
        assert len(rows)==108 and min(float(r['full_pct']) for r in rows)>99
        x=[float(r['published_to_poll_us'])/1000 for r in rows]
        for axis,key in [(ax,'full_pct'),(ax2,'atomic_head_no_observed_cqe_pct')]:
            axis.scatter(x,[float(r[key]) for r in rows],s=19,c=color,marker=marker,alpha=.64,label=str(202+rank*2),edgecolors='none')
    for axis in (ax,ax2):
        axis.set_xlim(1.5,4.05);axis.set_ylim(98.8,100)
        axis.set_xlabel('末块发布 → CPU读出 / ms');axis.set_ylabel('该等待区间内的时间占比 / %')
        axis.grid(alpha=.16);axis.set_axisbelow(True)
    ax.set_title('软件pending=8，占满名额',fontsize=12,pad=13)
    ax2.set_title('占满且队首ATOMIC尚无CQE观测',fontsize=12,pad=13)
    fig.legend(*ax.get_legend_handles_labels(),frameon=False,loc='lower center',bbox_to_anchor=(.57,.095),ncol=2)
    export(fig,output,'05-fifo-proof','⑤ 用全部命令重放FIFO：216条末块等待均超过99%处于满额状态',
        '每点为一个窗口/通道；包含控制命令，按collect入队、ack_slot顺序回收。比例仅对应末块等待区间，不是整个Dispatch占比。')

    metadata=dict(shape=dict(tokens=4096,hidden=7168,experts=256,topk=8,sm=24,dispatch_chunk=32,nvl_chunk=4),
        input_sha256={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs.values()},
        notes='Figures 1-3 contain historical backend comparison; figure3 separately labels the latest URMA trace. Figures4-5 use current accepted BF16 only. Request medians are across nine windows and twelve channels, not independent repeated experiments. All CQEs are software observations. No additive critical-path claim.')
    (output/'figure_sources.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
    print('STAGE_SUMMARY_FIGURES_EXPORTED',output)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path('test/reference_events/results'))
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--font',type=Path,default=Path('C:/Windows/Fonts/msyh.ttc'))
    args=parser.parse_args();build(args.root,args.output,args.font)
