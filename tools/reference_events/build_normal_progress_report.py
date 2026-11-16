"""Build reproducible static Normal progress figures from recorded experiments."""
import argparse,csv,hashlib,json,statistics
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

LABEL='dispatch_bf16_chunk32'


def build(root,out):
    out.mkdir(parents=True,exist_ok=True);sources={}
    def read(path):
        path=root/path;sources[str(path.relative_to(root))]=hashlib.sha256(path.read_bytes()).hexdigest();return path
    def csvs(path):
        with read(path).open(newline='') as f:return list(csv.DictReader(f))
    def save(name):
        for extension in ('png','svg'):plt.savefig(out/(name+'.'+extension),dpi=180,bbox_inches='tight',facecolor='white')
        svg=out/(name+'.svg');svg.write_text('\n'.join(line.rstrip() for line in svg.read_text(encoding='utf-8').splitlines())+'\n',encoding='utf-8')
        plt.close()
    plt.rcParams.update({'font.family':'sans-serif','font.sans-serif':['Microsoft YaHei','SimHei','DejaVu Sans'],'axes.unicode_minus':False,'svg.fonttype':'none','font.size':10,'axes.spines.top':False,'axes.spines.right':False})
    api=csvs(Path('backend_matched_comparison_01/api_comparison.csv'))
    decomposition=[];means=[]
    for backend,name in [('verbs','verbs_ref_4096_matched_on_01'),('URMA','urma_ref_4096_matched_on_01')]:
        for node in (0,1):
            path=Path(name)/('node%d'%node)/('r01_baseline/normal' if backend=='verbs' else 'normal')/'event_trace.jsonl'
            selected=[]
            for line in read(path).open():
                window=json.loads(line)
                if window['label']!=LABEL or not window['index']:continue
                records=window['gpu']['records']
                t0=min(r['host_ns'] for r in records if r['event']==0)
                t1=min(r['host_ns'] for r in records if r['event']==1)
                t2=max(r['host_ns'] for r in records if r['event']==2)
                pre=(t1-t0)/1000;progress=(t2-t1)/1000;residual=window['us']-pre-progress
                if min(pre,progress,residual)<0:raise ValueError('cannot form nonnegative observation coverage partition')
                row=dict(backend=backend,node=node,index=window['index'],api_us=window['us'],first_token_to_first_write_us=pre,first_write_to_last_visible_us=progress,uncovered_residual_us=residual)
                selected.append(row);decomposition.append(row)
            if len(selected)!=9:raise ValueError('expected nine windows')
            means.append(dict(backend=backend,node=node,**{k:statistics.mean(r[k] for r in selected) for k in ('api_us','first_token_to_first_write_us','first_write_to_last_visible_us','uncovered_residual_us')}))
    for name,rows in [('coverage_samples',decomposition),('coverage_means',means)]:
        with (out/(name+'.csv')).open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    order=[('verbs',0),('URMA',0),('verbs',1),('URMA',1)];means=sorted(means,key=lambda r:order.index((r['backend'],r['node'])))
    labels=['202\nverbs','202\nURMA','204\nverbs','204\nURMA']
    fig,axes=plt.subplots(1,2,figsize=(13,6));fig.suptitle('图1  Normal BF16 Dispatch：整体差距与观测覆盖分解',fontsize=16,y=1.02)
    values=[]
    for backend,node in order:
        row=next(r for r in api if r['label']==LABEL and int(r['node'])==node);values.append(float(row['paced_us_verbs' if backend=='verbs' else 'paced_us_urma'])/1000)
    axes[0].bar(range(4),values,color=['#64748b','#db5c4a']*2,width=.6)
    for i,v in enumerate(values):axes[0].text(i,v+.09,'%.3f'%v,ha='center')
    axes[0].set_title('关闭采集：整体API均值\n202回退13.50%；204并非回退');axes[0].set_ylabel('毫秒');axes[0].set_ylim(0,9.5)
    bottom=[0.]*4
    for key,color,label in [('first_token_to_first_write_us','#4e91c7','首token→首次WRITE包络'),('first_write_to_last_visible_us','#df7859','首次WRITE→最后远端token可见包络'),('uncovered_residual_us','#cbd5e1','未覆盖余量（算术差，非独立阶段）')]:
        vals=[r[key]/1000 for r in means];axes[1].bar(range(4),vals,bottom=bottom,color=color,width=.6,label=label)
        for i,v in enumerate(vals):axes[1].text(i,bottom[i]+v/2,'%.3f'%v,ha='center',va='center',fontsize=9)
        bottom=[a+b for a,b in zip(bottom,vals)]
    for i,v in enumerate(bottom):axes[1].text(i,v+.09,'%.3f'%v,ha='center')
    axes[1].set_title('开启采集：同一窗口内计算后取均值\n整根柱为该组trace API，不混用左侧总量');axes[1].set_ylim(0,9.5)
    axes[1].legend(loc='upper left',bbox_to_anchor=(0,-.14),fontsize=9,frameon=False)
    for ax in axes:ax.set_xticks(range(4),labels);ax.grid(axis='y',alpha=.15);ax.set_axisbelow(True)
    fig.text(.01,-.13,'范围：4096 tokens / hidden7168 / topk8；历史匹配组，每组9有效样本。\n右图是部分观测覆盖，不是完整关键路径：灰色余量可分布在观测前后，灰色位置仅为堆叠展示；橙色包含并行准备、排队、传输与通知。',fontsize=9)
    fig.tight_layout();save('01_dispatch_coverage')

    submit=csvs(Path('notify_submission_audit_01/submission_summary.csv'));retire=csvs(Path('normal_retire_evidence_20261116/software_retire_wait.csv'))
    fig,axes=plt.subplots(1,2,figsize=(12,5.3));fig.suptitle('图2  发送侧推进链路：两种不同的等待，不能相加到API',fontsize=15)
    for i,campaign in enumerate(('verbs_historical','urma_boundaries')):
        selected={r['metric']:float(r['mean_us']) for r in submit if r['campaign']==campaign and r['node']=='1'}
        before=selected['write_post_end_to_atomic_poll_us'];after=selected['post_end_gap_us']-before
        axes[0].bar(i,before,color='#df7859',label='WRITE已提交→CPU取到通知' if i==0 else None)
        axes[0].bar(i,after,bottom=before,color='#4e91c7',label='CPU取到通知→完成提交' if i==0 else None)
        axes[0].text(i,before+after+8,'%.2f + %.2f μs'%(before,after),ha='center',fontsize=10)
    axes[0].set_ylim(0,460);axes[0].set_xticks([0,1],['verbs','URMA']);axes[0].set_ylabel('微秒，均值');axes[0].set_title('204：WRITE与相邻通知的提交间隔');axes[0].legend(fontsize=9,frameon=False,loc='upper left')
    rows=[next(r for r in retire if r['backend']==b and r['node']=='1' and r['operation']=='payload_write') for b in ('verbs','urma')]
    vals=[float(r['p95_us']) for r in rows];axes[1].bar([0,1],vals,color=['#64748b','#df7859'])
    for i,v in enumerate(vals):axes[1].text(i,v+8,'%.2f μs'%v,ha='center')
    axes[1].set_ylim(0,500);axes[1].set_xticks([0,1],['verbs','URMA']);axes[1].set_title('204：大WRITE已有CQE→软件回收额度');axes[1].set_ylabel('微秒，P95')
    fig.text(.01,-.03,'左：通知取出前含GPU发布时间、预算与服务等待；不是CPU计算时间。右：已完成WRITE被按序回收规则挡住的观测。\n两图来源为此前验收BF16 trace，不是最新无采集实验的阶段计时，也不是独立重复次数。',fontsize=9);fig.tight_layout();save('02_queue_waits')

    fence=csvs(Path('urma_fadd_fence_01_summary/rounds.csv'));latest=csvs(Path('urma_retire_01_review_20261116/rounds.csv'))
    data=[float(r['peer_off_change_pct']) for r in fence if r['label']==LABEL]+[float(r['B_change_pct']) for r in latest if r['label']==LABEL]
    fig,ax=plt.subplots(figsize=(10,5));bars=ax.bar(range(4),data,color=['#428c79','#428c79','#b6c4cc','#64748b'],width=.58)
    bars[2].set_hatch('//');ax.axhline(0,color='#334155',linewidth=.8);ax.set_xticks(range(4),['关闭204数据fence\n配对1','关闭204数据fence\n配对2','提前回收已完成名额\n配对1：诊断指纹变化','提前回收已完成名额\n配对2：指纹一致']);ax.set_ylabel('相对各自基线的变化（%，负值更快）');ax.set_ylim(-4.4,.5)
    for i,v in enumerate(data):ax.text(i,v-.17,'%.2f%%'%v,ha='center',va='top',fontsize=12)
    ax.set_title('图3  干预结果：顺序约束有部分净收益；提前回收未获主要改善',fontsize=14)
    fig.text(.01,-.04,'不同实验各自内部对照，不跨实验相减。最新回收配对1含诊断脚本指纹漂移，不能当严格对照。\n回收配对2：8.134→8.023 ms（-1.37%，最大CV0.60%）；不采用该策略，不继续扫描。',fontsize=9);fig.tight_layout();save('03_interventions')

    fig,ax=plt.subplots(figsize=(13,4.8));ax.set_xlim(0,13);ax.set_ylim(0,4.5);ax.axis('off')
    def box(x,y,w,text,color):
        ax.add_patch(FancyBboxPatch((x,y),w,.8,boxstyle='round,pad=.05',facecolor=color,edgecolor='#94a3b8'));ax.text(x+w/2,y+.4,text,ha='center',va='center',fontsize=10)
    def arrow(a,b,**kw):ax.annotate('',xy=b,xytext=a,arrowprops=dict(arrowstyle='->',color='#475569',**kw))
    boxes=[(.1,1.55,'GPU准备/发布'),(2.2,1.55,'CPU读取命令'),(4.3,1.55,'提交WRITE/FADD'),(6.6,1.55,'设备/远端推进'),(8.9,1.55,'CPU观测CQE'),(11,1.55,'软件额度回收')]
    for i,(x,y,text) in enumerate(boxes):box(x,y,1.7,text,'#ffdfd4' if i in (1,3,5) else '#dceaf4')
    for i in range(5):arrow((boxes[i][0]+1.7,1.95),(boxes[i+1][0],1.95))
    box(6.2,.15,3.8,'接收GPU见tail且epoch有效 → 消费数据','#e1eee7');arrow((7.45,1.55),(8.1,.95))
    arrow((11.85,2.35),(3.05,2.35),connectionstyle='arc3,rad=.25',linestyle='--')
    ax.text(6.3,3.6,'回收名额影响下一批命令读取；队首未完成会形成背压',ha='center',fontsize=11,color='#a3432d')
    ax.text(6.5,4.05,'图4  目前定位到的链路位置（不是已证明的唯一根因）',ha='center',fontsize=15)
    ax.text(.1,-.32,'接收GPU不必等待发送CPU收到CQE才消费本批；但后续发送可能受额度回收影响。\nfence放宽约3.4%收益已复现；“已完成条目提前回收”本轮没有主要收益。',fontsize=10);save('04_pipeline_location')
    (out/'figure_sources.json').write_text(json.dumps(dict(sources=sources,coverage_note='Per-window API and sampled RAW GPU envelope, then arithmetic residual and means; no sum of independent medians, no attribution of residual, no use of inactive API as trace total.'),indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();build(a.root,a.output)
