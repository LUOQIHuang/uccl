"""Validate strict serialization and report SDK-observed pair latencies."""
import argparse,csv,json,statistics as st
from pathlib import Path
LABELS={'t32_0to1':(32,0),'t64_0to1':(64,0),'t32_1to0':(32,1),'t64_1to0':(64,1)}
def read(p):return json.loads(p.read_text())
def rows(p):return [json.loads(s) for s in p.read_text().splitlines() if s.strip()]
def write(p,data):
    with p.open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(data[0]));w.writeheader();w.writerows(data)
def percentile(v,p):
    v=sorted(v);pos=(len(v)-1)*p;i=int(pos);return v[i]+(v[min(i+1,len(v)-1)]-v[i])*(pos-i)
def metrics(sample,count):
    if sample.get('version')!=1 or sample.get('clock')!='CLOCK_MONOTONIC_RAW' or sample.get('max_inflight_pairs')!=1 or sample.get('pairs_expected')!=count or len(sample['pairs'])!=count:raise ValueError('wrong serial recipe')
    result=[];previous=0
    for p in sample['pairs']:
        w,a=p['write_post_begin'],p['atomic_post_begin'];wc,ac=p['write_cqe'],p['atomic_cqe']
        if not (0<w<=p['write_post_end']<=wc and w<=a<=p['atomic_post_end']<=ac and a<=wc and w>=previous and p['atomic_id']==p['write_id']+1):raise ValueError('timestamps violate pair serialization or immediate FADD submission')
        previous=max(wc,ac)
        result.append(dict(write_us=(wc-w)/1000,atomic_us=(ac-a)/1000,pair_us=(previous-w)/1000,gap_us=(ac-wc)/1000,
                           write_post_us=(p['write_post_end']-w)/1000,atomic_post_us=(p['atomic_post_end']-a)/1000,
                           same_poll_batch=p['write_poll_seq']==p['atomic_poll_seq'],poll_calls=p['sdk_poll_calls']))
    span=(previous-sample['pairs'][0]['write_post_begin'])/1000
    if sample['loop_api_ns']/1000<span:raise ValueError('API loop does not cover SDK span')
    return result,span
def summarize(root,backend,out):
    campaign=root/backend/'cases'/(root.name+'_'+backend+'_run')
    overlays=[read(root/backend/('overlay_node%d.json'%n)) for n in (0,1)]
    if overlays[0]!=overlays[1]:raise ValueError('different source overlays')
    data=[];controls=[];per_run=[];contexts=[];plans=[]
    for rank in (0,1):
        p=campaign/('node%d'%rank);status=read(p/'status.json');request=read(p/'worker_request.json');plan=read(p/'plan.json')
        if not status.get('passed') or not status.get('correctness') or status['points']!=request['rounds']*4:raise ValueError('incomplete run')
        affinity=read(p/'affinity.json');runtime=read(p/'runtime.json');manifest=read(p/'manifest.json');path=read(p/'path.json')
        if affinity['effective']!=[affinity['cpu']] or runtime['module_sha']!=request['module_sha'] or manifest['module_sha']!=request['module_sha']:raise ValueError('affinity/module drift')
        if not path['serial'] or path.get('channels',path.get('lanes_per_thread'))!=1 or path['control_memory']!='gpu':raise ValueError('not GPU-memory one-data-queue serial mode')
        raw=rows(p/'samples.jsonl');expected={(rr,label,i) for rr in range(1,request['rounds']+1) for label in LABELS for i in range(request['samples'])}
        keys=[(r['repeat'],r['label'],r['index']) for r in raw]
        if len(keys)!=len(set(keys)) or set(keys)!=expected:raise ValueError('missing/duplicate samples')
        for r in raw:
            tokens,sender=LABELS[r['label']]
            if r['tokens']!=tokens or r['sender']!=sender or r['rank']!=rank or r['used']!=(r['index']>0) or not r['validation']:raise ValueError('sample metadata differs')
            s=r['sample']
            if rank!=sender:
                if not s.get('receiver_only') or 'pairs' in s:raise ValueError('unidirectional receiver also sent traffic')
                continue
            mm,span=metrics(s,tokens//32)
            if r['used']:
                controls.append(dict(backend=backend,node=rank,label=r['label'],repeat=r['repeat'],index=r['index'],tokens=tokens,sdk_span_us=span,loop_api_us=s['loop_api_ns']/1000))
                for j,m in enumerate(mm):data.append(dict(backend=backend,node=rank,label=r['label'],repeat=r['repeat'],index=r['index'],pair_index=j,**m))
        contexts.append(dict(node=rank,request=request,affinity=affinity,runtime=runtime,path=path,manifest=manifest));plans.append(plan)
    if plans[0]!=plans[1]:raise ValueError('two-node plans differ')
    plan=plans[0]
    for label in LABELS:
        for repeat in range(1,plan['rounds']+1):
            rr=[r for r in controls if r['label']==label and r['repeat']==repeat];v=[r['sdk_span_us'] for r in rr]
            if len(v)!=plan['samples']-1:raise ValueError('wrong valid sample count')
            mean=st.mean(v);cv=st.pstdev(v)/mean*100
            pp=[r for r in data if r['label']==label and r['repeat']==repeat]
            per_run.append(dict(backend=backend,label=label,repeat=repeat,n=len(v),mean_sdk_span_us=mean,cv_pct=cv,
                median_pair_us=st.median(r['pair_us'] for r in pp),median_gap_us=st.median(r['gap_us'] for r in pp),
                p95_pair_us=percentile([r['pair_us'] for r in pp],.95),same_batch_pairs=sum(r['same_poll_batch'] for r in pp)))
    summary=[]
    for label in LABELS:
        rr=[r for r in per_run if r['label']==label];means=[r['mean_sdk_span_us'] for r in rr];median=st.median(means)
        pp=[r for r in data if r['label']==label]
        drift=(max(means)-min(means))/median*100;cv=max(r['cv_pct'] for r in rr)
        summary.append(dict(backend=backend,label=label,rounds=len(rr),valid_iterations=sum(r['n'] for r in rr),pairs=len(pp),
            median_run_mean_sdk_span_us=median,mean_pair_us=st.mean(r['pair_us'] for r in pp),median_pair_us=st.median(r['pair_us'] for r in pp),
            p95_pair_us=percentile([r['pair_us'] for r in pp],.95),median_write_us=st.median(r['write_us'] for r in pp),
            median_gap_us=st.median(r['gap_us'] for r in pp),mean_gap_us=st.mean(r['gap_us'] for r in pp),
            median_write_post_us=st.median(r['write_post_us'] for r in pp),median_atomic_post_us=st.median(r['atomic_post_us'] for r in pp),same_batch_pairs=sum(r['same_poll_batch'] for r in pp),
            round_span_pct=drift,max_round_cv_pct=cv,stable=drift<=5 and cv<=5))
    out.mkdir(parents=True,exist_ok=False)
    for name,rs in [('pairs',data),('iterations',controls),('per_run',per_run),('summary',summary)]:write(out/(name+'.csv'),rs)
    (out/'context.json').write_text(json.dumps(dict(plan=plan,nodes=contexts,overlay=overlays[0]),indent=2))
    lines=['# 单队列串行WRITE/FADD结果','',
        '这是单向、单线程、单活跃数据队列、最多一对在途的独立传输微基准。每WRITE459264B、FADD加32；32/64表示每迭代1/2对，第二对在第一对全部完成后提交。',
        '时间为本端SDK观测，0间隔可由同次轮询同时返回造成；负间隔保留。GPU校验、TCP同步和JSON输出位于计时之外。',
        '总量取三轮（或显式轮数）迭代SDK跨度均值的中位数；单对分位数是相关样本的描述，不是独立实验置信区间。','',
        '| 点 | 总SDK跨度 μs | 单对中位 μs | 单对P95 μs | WRITE中位 μs | W→A间隔中位 μs | 同批返回对数 | 稳定 |',
        '| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |']
    for r in summary:lines.append('| %s | %.3f | %.3f | %.3f | %.3f | %.3f | %d/%d | %s |'%(r['label'],r['median_run_mean_sdk_span_us'],r['median_pair_us'],r['p95_pair_us'],r['median_write_us'],r['median_gap_us'],r['same_batch_pairs'],r['pairs'],r['stable']))
    lines+=['','| 点 | WRITE SDK提交中位 μs | FADD SDK提交中位 μs |','| --- | ---: | ---: |']
    for r in summary:lines.append('| %s | %.3f | %.3f |'%(r['label'],r['median_write_post_us'],r['median_atomic_post_us']))
    lines+=['','URMA的链式SDK提交可同时包含两条请求，上述两个提交耗时可能是同一次调用，不能相加。W→A短但整对时延长时，应检查提交和WRITE完成时延，不能只据W→A宣称无差异。']
    lines+=['','稳定要求每轮迭代SDK跨度CV≤5%，轮均值跨度/中位数≤5%；失败保留全部记录，不自动重跑。',
            '本轮绕过完整Normal GPU kernel/FIFO/Proxy循环，注册区域规模和布局也更小。差异消失不能只归因并发，仍可能依赖原Proxy/布局/持续负载。两后端在不同内核运行。']
    (out/'REPORT.zh-CN.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print('SERIAL_PAIR_ANALYSIS_PASSED output='+str(out));return summary
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True);p.add_argument('--backend',choices=('urma','verbs'),required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();summarize(a.root,a.backend,a.output)
