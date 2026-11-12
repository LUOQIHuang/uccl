"""Eight sequential evidence charts, retaining intermediate and negative findings."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
import numpy as np
from plot_stage_summary import read,one,grouped,export,clean,VERBS,URMA,NODE0,NODE1,LABEL
from plot_regression_brief import rounded


def legend(ax):ax.legend(loc='lower left',bbox_to_anchor=(0,1.02),ncol=2,frameon=False)


def draw(root,output,font):
    font_manager.fontManager.addfont(str(font))
    plt.rcParams.update({'font.family':font_manager.FontProperties(fname=str(font)).get_name(),
        'axes.unicode_minus':False,'font.size':11,'svg.fonttype':'path'})
    inputs={
        'api':root/'backend_matched_comparison_01/api_comparison.csv',
        'gpu':root/'backend_matched_comparison_01/gpu_span_comparison.csv',
        'cpu':root/'backend_matched_comparison_01/cpu_stage_comparison.csv',
        'tail':root/'backend_matched_comparison_01/cpu_completion_tails.csv',
        'positions':root/'window_audit_01/payload_positions.csv',
        'tail_context':root/'window_audit_01/context.json',
        'spans':root/'urma_boundaries_01_summary/boundary_medians.csv',
        'focus':root/'urma_boundaries_01_summary/cpu_chunk6_and_last.csv',
        'control':root/'urma_boundaries_01_summary/api_controls.csv',
        'fifo':root/'urma_boundaries_01_fifo_audit/fifo_medians.csv',
        'fifo_raw':root/'urma_boundaries_01_fifo_audit/last_write_fifo.csv',
        'window':root/'urma_window_after_boundaries_01_summary/rounds.csv',
        'notify':root/'notify_completion_audit_01/notify_pair_summary.csv'}
    data={k:read(p) for k,p in inputs.items() if p.suffix=='.csv'}
    output.mkdir(parents=True,exist_ok=False)

    api=[one(data['api'],node=n,label=LABEL) for n in (0,1)]
    fig,ax=plt.subplots(figsize=(10.8,3.9))
    grouped(ax,['202端 BF16','204端 BF16'],[float(r['paced_us_verbs'])/1000 for r in api],[float(r['paced_us_urma'])/1000 for r in api]);legend(ax)
    ax.set_xlabel('Dispatch API均值 / ms（关闭采集）')
    export(fig,output,'01-overall','第1步｜先确认：哪一端的整体回退最明显？',
        '历史匹配对照；202 +0.965 ms / +13.50%，204 −1.81%。先选202 BF16，不预设CPU/GPU/网络原因。')

    gpu=one(data['gpu'],node=0,label=LABEL)
    fig,ax=plt.subplots(figsize=(10.8,4))
    keys=['sender_cta_0_to_1_median_us','first_write_to_last_visible_us']
    grouped(ax,['首token → 首次WRITE\n同Sender CTA中位数','首次WRITE → 最后远端可见\n本机事件包络中位数'],*[ [float(gpu[k+'_'+backend])/1000 for k in keys] for backend in ('verbs','urma')]);legend(ax)
    ax.set_xlabel('202 BF16 / ms')
    export(fig,output,'02-region','第2步｜再划区间：首次WRITE之前，还是之后？',
        '首批跨度增加18.6 μs，后续包络增加682.5 μs；下一步查后续推进。两种口径不相加。')

    cpu=one(data['cpu'],node=1,label=LABEL)
    fig,ax=plt.subplots(figsize=(10.8,4))
    keys=['decoded_to_prepare_us','prepare_to_post_begin_us','post_begin_to_post_end_us']
    grouped(ax,['命令解码 → 准备','准备 → SDK提交','SDK调用本身'],*[[float(cpu[k+'_'+b]) for k in keys] for b in ('verbs','urma')],digits=2);legend(ax)
    ax.set_xlabel('204 BF16，各段请求中位数 / μs')
    export(fig,output,'03-cpu-handling','第3步｜先查CPU：读到命令后的处理是否明显变慢？',
        '提交前中位差异较小；尚未覆盖命令读取前等待，也不能排除长尾。下一步按请求类别与分位数检查。')

    tails=one(data['tail'],node=1,label=LABEL,operation='payload_write')
    fig,ax=plt.subplots(figsize=(10.8,4.2))
    grouped(ax,['大WRITE P50','大WRITE P95','大WRITE P99'],*[[float(tails[k+'_us_'+b])/1000 for k in ('p50','p95','p99')] for b in ('verbs','urma')]);legend(ax)
    ax.set_xlabel('204 BF16，post_end → 软件CQE观测 / ms')
    context=json.loads(inputs['tail_context'].read_text())
    record=next(r for r in context['summaries'] if r['backend']=='urma' and r['node']==1)
    assert record['tail_positions']['6']==59 and record['tail_requests']==60
    positions=[one(data['positions'],backend=b,node=1,payload_position=6) for b in ('verbs','urma')]
    assert round(float(positions[0]['p50_us'])/1000,3)==2.224
    export(fig,output,'04-tail','第4步｜再查尾部：哪些请求拖慢了后续推进？',
        'URMA P50反而较短，P95/P99更长；最慢60条中59条是每通道第6块，其位置中位数2.224→3.492 ms。')

    assert all(one(data['control'],node=n,label=LABEL)['representative']=='True' for n in (0,1))
    def span(n,name):return float(one(data['spans'],node=n,label=LABEL,span=name)['median_us'])
    fifo=[one(data['fifo'],node=n,label=LABEL) for n in (0,1)]
    def sixth(n):return statistics.median(float(r['post_end_to_cqe_us']) for r in data['focus'] if r['node']==str(n) and r['label']==LABEL and r['payload_ordinal']=='6')
    fig,(ax,ax2)=plt.subplots(1,2,figsize=(14,4.9),gridspec_kw={'width_ratios':[1.2,1]})
    large=[[span(n,'sender_first_to_last_ready')/1000,float(fifo[n]['published_to_poll_us'])/1000,sixth(n)/1000] for n in (0,1)]
    grouped(ax,['首WRITE → 末payload就绪','末块已发布 → CPU读出','第6块提交后 → CQE'],*large,names=('202','204'),colors=(NODE0,NODE1));legend(ax);ax.set_xlabel('本轮URMA BF16 / ms')
    grouped(ax2,['末块命令发布','最后tail检查','最后epoch检查','最后可见 → Receiver结束'],*[[span(n,k) for k in ('last_command_publish','final_tail_check_span','final_epoch_check','last_remote_to_receiver_done')] for n in (0,1)],names=('202','204'),colors=(NODE0,NODE1),digits=2)
    ax2.set_xlabel('本轮URMA BF16 / μs')
    export(fig,output,'05-boundaries','第5步｜细分长区间：命令发布后，CPU是否仍未读到？',
        'BF16采集影响202 +2.60%、204 +4.38%，通过5%门槛；新边界目前是URMA观测，重叠区间不相加。')

    assert len(data['fifo_raw'])==216 and all(r['pending_at_publication']=='8' for r in data['fifo_raw'])
    fig,ax=plt.subplots(figsize=(11.8,4.2))
    grouped(ax,['202端','204端'],[float(r['full_pct']) for r in fifo],[float(r['atomic_head_no_observed_cqe_pct']) for r in fifo],
        names=('8个软件名额占满','满额且队首ATOMIC尚无CQE'),colors=('#6e7f90',URMA),digits=2)
    ax.set_xlim(0,114);legend(ax);ax.set_xlabel('末块发布→CPU读取区间内，时间占比的中位数 / %')
    export(fig,output,'06-fifo','第6步｜重放全部命令：CPU为什么迟迟不读下一条？',
        '216/216条观察在发布时pending=8；全部观察满额覆盖>99%。这是软件回收额度，不是硬件SQ占用。')

    rounds=[one(data['window'],repeat=n,label=LABEL) for n in (1,2)]
    fig,ax=plt.subplots(figsize=(11.5,4.2))
    grouped(ax,['第一配对 A→B\n最大CV 7.55%，超限','第二配对 B→A\n最大CV 0.67%，通过'],
        [float(r['default_max_mean_us'])/1000 for r in rounds],[float(r['peer4_max_mean_us'])/1000 for r in rounds],
        names=('A：202/204窗口8/8','B：202/204窗口8/4'),colors=('#6e7f90',NODE1));legend(ax)
    ax.set_xlabel('BF16：对应样本两端最大值的均值 / ms')
    export(fig,output,'07-window','第7步｜做因果干预：窗口8→4能否改善整体？',
        '两配对B相对A +4.24%（不稳定）、+0.72%（稳定）；未获净收益，保留8，不能把满额等同于窗口过大。')

    fig,ax=plt.subplots(figsize=(11.6,4.9));y=np.arange(2);h=.22
    for offset,campaign,name,color,hatch in [(-h,'verbs_historical','verbs历史',VERBS,None),(0,'urma_historical','URMA历史',URMA,None),(h,'urma_boundaries','URMA本轮',URMA,'//')]:
        rr=[one(data['notify'],campaign=campaign,node=n) for n in (0,1)]
        assert all(r['pairs']==r['atomic_preposted']=='1188' for r in rr)
        values=[float(r['completion_gap_median_us']) for r in rr]
        artists=ax.barh(y+offset,values,height=h*.90,label=name,color=color,hatch=hatch)
        ax.bar_label(artists,labels=[rounded(v)+' μs' for v in values],padding=5,fontsize=11)
    ax.set_yticks(y,['202端','204端']);ax.invert_yaxis();ax.set_xlim(0,850);clean(ax);legend(ax)
    ax.set_xlabel('WRITE完成观测 → 相邻通知FADD完成观测，中位间隔 / μs')
    export(fig,output,'08-notify','第8步｜回到队首ATOMIC：WRITE之后，通知何时完成？',
        '每组1188对；FADD均已在WRITE完成前提交。历史verbs约4 μs、URMA约550～660 μs；不是纯硬件atomic延迟。')
    (output/'figure_sources.json').write_text(json.dumps(dict(
        sha256={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs.values()},
        note='Investigation order, not additive time decomposition. Historical matched comparisons in steps1-4; current URMA observation in steps5-6; negative window intervention in step7; matched historical plus current notify replication in step8. Kernel/device differences retained.'),indent=2),encoding='utf-8')
    print('EVIDENCE_STEPS_EXPORTED',output)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=Path('test/reference_events/results'))
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--font',type=Path,default=Path('C:/Windows/Fonts/msyh.ttc'))
    a=p.parse_args();draw(a.root,a.output,a.font)
