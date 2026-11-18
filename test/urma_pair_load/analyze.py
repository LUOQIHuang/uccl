"""Compare equal-byte four-pair batches; preserve ordering and paired rounds."""
import argparse,csv,json,statistics as st
from pathlib import Path
SHAPES={'serial4':(1,1),'queue4':(1,4),'lanes4':(4,4)}
SUITES={'basic4':(SHAPES,4),'depth12':({'lane3_w1':(3,3),'lane3_w4':(3,12)},12)}
LABELS={name+'_%dto%d'%(sender,1-sender):(name,lanes,limit,sender) for name,(lanes,limit) in SHAPES.items() for sender in (0,1)}
def read(p):return json.loads(p.read_text())
def rows(p):return [json.loads(s) for s in p.read_text().splitlines() if s.strip()]
def write(p,data):
    with p.open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(data[0]));w.writeheader();w.writerows(data)
def percentile(v,p):
    v=sorted(v);pos=(len(v)-1)*p;i=int(pos);return v[i]+(v[min(i+1,len(v)-1)]-v[i])*(pos-i)

def metrics(s,lanes,limit,count=4):
    if (lanes,limit,count) not in [(l,m,c) for shapes,c in SUITES.values() for l,m in shapes.values()] or s.get('version')!=1 or s.get('clock')!='CLOCK_MONOTONIC_RAW' or s.get('data_lanes')!=lanes or s.get('max_inflight_pairs')!=limit or s.get('observed_max_inflight_pairs')!=limit or s.get('pairs_expected')!=count or len(s['pairs'])!=count:raise ValueError('wrong load recipe')
    out=[];previous=0;ids=set()
    for j,p in enumerate(s['pairs']):
        w,a,wc,ac=(p[k] for k in ('write_post_begin','atomic_post_begin','write_cqe','atomic_cqe'))
        if not (0<w<=p['write_post_end']<=wc and w<=a<=p['atomic_post_end']<=ac and a<=wc and p['atomic_id']==p['write_id']+1 and p['write_id']>>32==j%lanes and p['write_poll_seq']>0 and p['atomic_poll_seq']>0):raise ValueError('invalid request identity/timestamps')
        if p['write_id'] in ids or p['atomic_id'] in ids:raise ValueError('duplicate identity')
        ids.update((p['write_id'],p['atomic_id']))
        if limit==1 and w<previous:raise ValueError('serial load overlaps')
        previous=max(wc,ac)
        out.append(dict(pair_index=j,lane=j%lanes,write_us=(wc-w)/1000,atomic_us=(ac-a)/1000,pair_us=(max(wc,ac)-w)/1000,
            gap_us=(ac-wc)/1000,write_post_us=(p['write_post_end']-w)/1000,atomic_post_us=(p['atomic_post_end']-a)/1000,
            same_poll_batch=p['write_poll_seq']==p['atomic_poll_seq']))
    pp=s['pairs'];span=(max(max(p['write_cqe'],p['atomic_cqe']) for p in pp)-min(p['write_post_begin'] for p in pp))/1000
    if s['loop_api_ns']/1000<span or s['sdk_poll_calls']<max(max(p['write_poll_seq'],p['atomic_poll_seq']) for p in pp):raise ValueError('API/poll count does not cover batch')
    # Every wave is submitted before polling, and fully retired before next.
    previous=0
    for first in range(0,count,limit):
        cohort=pp[first:first+limit]
        if min(p['write_post_begin'] for p in cohort)<previous:raise ValueError('load waves overlap')
        if max(p['atomic_post_end'] for p in cohort)>min(min(p['write_cqe'],p['atomic_cqe']) for p in cohort):raise ValueError('wave not submitted before polling')
        for lane in range(lanes):
            chain=[p for j,p in enumerate(pp) if first<=j<first+limit and j%lanes==lane]
            if chain and len({p[k] for p in chain for k in ('write_post_begin','atomic_post_begin')})!=1:raise ValueError('lane batch not chained')
        last=max(p['write_cqe'] for p in cohort)
        for j,p in enumerate(cohort,first):
            out[j].update(wave=first//limit,until_wave_last_write_us=(last-p['write_cqe'])/1000,after_wave_last_write_us=(p['atomic_cqe']-last)/1000)
        previous=max(max(p['write_cqe'],p['atomic_cqe']) for p in cohort)
    return out,span

def summarize(root,out):
    folder=root/'urma';campaign=folder/'cases'/(root.name+'_urma_run')
    overlays=[read(folder/('overlay_node%d.json'%n)) for n in (0,1)]
    if overlays[0]!=overlays[1]:raise ValueError('different source overlays')
    data=[];iterations=[];contexts=[];plans=[]
    for rank in (0,1):
        p=campaign/('node%d'%rank);status=read(p/'status.json');req=read(p/'worker_request.json');plan=read(p/'plan.json')
        shapes,count=SUITES[plan.get('suite','basic4')]
        labels={name+'_%dto%d'%(sender,1-sender):(name,lanes,limit,sender) for name,(lanes,limit) in shapes.items() for sender in (0,1)}
        if not status.get('passed') or not status.get('correctness') or status['points']!=req['rounds']*len(labels):raise ValueError('incomplete run')
        affinity=read(p/'affinity.json');runtime=read(p/'runtime.json');manifest=read(p/'manifest.json')
        if affinity['effective']!=[affinity['cpu']] or runtime['module_sha']!=req['module_sha'] or manifest['module_sha']!=req['module_sha']:raise ValueError('affinity/module drift')
        paths=rows(p/'paths.jsonl');expected_paths={(rr,label) for rr in range(1,req['rounds']+1) for label in labels}
        if len(paths)!=len(expected_paths) or {(x['repeat'],x['label']) for x in paths}!=expected_paths:raise ValueError('missing/duplicate path')
        for x in paths:
            path=x['path'];lanes=labels[x['label']][1]
            if not path['serial'] or path['channels']!=lanes or path['control_memory']!='gpu' or path['write_order']!='relax' or path['atomic_order']!='strong' or path['atomic_fence']!='per_request' or path['normal_completion']!='all':raise ValueError('changed load path/order')
        raw=rows(p/'samples.jsonl');expected={(rr,label,i) for rr in range(1,req['rounds']+1) for label in labels for i in range(req['samples'])}
        keys=[(r['repeat'],r['label'],r['index']) for r in raw]
        if len(keys)!=len(set(keys)) or set(keys)!=expected:raise ValueError('missing/duplicate samples')
        for r in raw:
            shape,lanes,limit,sender=labels[r['label']]
            if (r['shape'],r['lanes'],r['limit'],r['sender'],r['rank'],r['used'],r['validation'])!=(shape,lanes,limit,sender,rank,r['index']>0,True):raise ValueError('sample metadata differs')
            s=r['sample']
            if rank!=sender:
                if not s.get('receiver_only') or 'pairs' in s:raise ValueError('receiver also sent traffic')
                continue
            mm,span=metrics(s,lanes,limit,count)
            if r['used']:
                tags=dict(node=rank,label=r['label'],repeat=r['repeat'],index=r['index'],shape=shape)
                iterations.append(dict(tags,sdk_span_us=span,loop_api_us=s['loop_api_ns']/1000,sdk_poll_calls=s['sdk_poll_calls'],write_bytes=count*459264))
                for m in mm:data.append(dict(tags,**m))
        contexts.append(dict(node=rank,request=req,affinity=affinity,runtime=runtime,manifest=manifest,paths=paths));plans.append(plan)
    if plans[0]!=plans[1]:raise ValueError('two-node plans differ')
    plan=plans[0]
    if any(plan[k]!=v for k,v in [('bytes',459264),('notify_add',32),('pairs_per_iteration',count),('active_threads',1)]):raise ValueError('changed byte/thread plan')
    per_run=[];summary=[];positions=[]
    for label in labels:
        for rr in range(1,plan['rounds']+1):
            v=[r['sdk_span_us'] for r in iterations if r['label']==label and r['repeat']==rr]
            if len(v)!=plan['samples']-1:raise ValueError('wrong valid sample count')
            per_run.append(dict(label=label,repeat=rr,n=len(v),mean_sdk_span_us=st.mean(v),cv_pct=st.pstdev(v)/st.mean(v)*100))
        runs=[r for r in per_run if r['label']==label];means=[r['mean_sdk_span_us'] for r in runs];median=st.median(means)
        pp=[r for r in data if r['label']==label];drift=(max(means)-min(means))/median*100;cv=max(r['cv_pct'] for r in runs)
        summary.append(dict(label=label,rounds=len(runs),valid_iterations=sum(r['n'] for r in runs),pairs=len(pp),median_run_mean_sdk_span_us=median,
            median_pair_us=st.median(r['pair_us'] for r in pp),p95_pair_us=percentile([r['pair_us'] for r in pp],.95),median_gap_us=st.median(r['gap_us'] for r in pp),
            same_batch_pairs=sum(r['same_poll_batch'] for r in pp),round_span_pct=drift,max_round_cv_pct=cv,stable=drift<=5 and cv<=5))
        for j in range(count):
            v=[r for r in pp if r['pair_index']==j]
            positions.append(dict(label=label,pair_index=j,lane=j%labels[label][1],n=len(v),median_pair_us=st.median(r['pair_us'] for r in v),
                median_write_us=st.median(r['write_us'] for r in v),median_atomic_us=st.median(r['atomic_us'] for r in v),median_gap_us=st.median(r['gap_us'] for r in v)))
    contrasts=[]
    for sender in (0,1):
        for old,new in ([('serial4','queue4'),('queue4','lanes4')] if count==4 else [('lane3_w1','lane3_w4')]):
            before=old+'_%dto%d'%(sender,1-sender);after=new+'_%dto%d'%(sender,1-sender)
            for rr in range(1,plan['rounds']+1):
                x=next(r['mean_sdk_span_us'] for r in per_run if r['label']==before and r['repeat']==rr)
                y=next(r['mean_sdk_span_us'] for r in per_run if r['label']==after and r['repeat']==rr)
                contrasts.append(dict(sender=sender,repeat=rr,before=old,after=new,before_us=x,after_us=y,delta_us=y-x,change_pct=(y/x-1)*100))
    out.mkdir(parents=True,exist_ok=False)
    for name,rs in [('pairs',data),('iterations',iterations),('per_run',per_run),('summary',summary),('positions',positions),('contrasts',contrasts)]:write(out/(name+'.csv'),rs)
    (out/'context.json').write_text(json.dumps(dict(plan=plan,nodes=contexts,overlay=overlays[0]),indent=2))
    lines=['# URMA等量负载对照','',
        '套件：%s；每迭代%d对、%dB，单线程。'%(plan.get('suite','basic4'),count,count*459264),
        'basic4比较串行/单队列4对/四队列各1对；depth12固定三队列，比较每队列深度1（四轮）与深度4（一轮）。',
        '先判断等量整批SDK跨度，再看对应请求W→A间隔。queue4按原实现先W0..W3后A0..A3，间隔增长本身不能判定退化。',
        '时间来自本端SDK观测；同批返回可为0，负间隔保留。GPU校验与TCP同步不在计时内。','',
        '| 点 | 整批SDK跨度 μs | 单对中位 μs | 单对P95 μs | W→A中位 μs | 同批完成对数 | 稳定 |',
        '| --- | ---: | ---: | ---: | ---: | ---: | --- |']
    for r in summary:lines.append('| %s | %.3f | %.3f | %.3f | %.3f | %d/%d | %s |'%(r['label'],r['median_run_mean_sdk_span_us'],r['median_pair_us'],r['p95_pair_us'],r['median_gap_us'],r['same_batch_pairs'],r['pairs'],r['stable']))
    lines+=['','整批值为各轮迭代均值的中位数。contrasts.csv保留每轮成对差值；positions.csv按请求位置分别统计，避免混合掩盖排队。',
        '稳定门槛：各轮整批CV≤5%，轮均值跨度/中位数≤5%。失败保留日志，不自动重跑。',
        'SDK提交耗时可能属于同一次链式调用，不能相加。单对在途时延在并发情况下重叠，也不能相加当整批时延。',
        '本实验不是完整Normal：没有原FIFO/Proxy循环、head反馈、双向同时发送或计时内GPU消费。未复现不能排除更大负载或多线程触发。']
    (out/'REPORT.zh-CN.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print('URMA_PAIR_LOAD_ANALYSIS_PASSED output='+str(out));return summary
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();summarize(a.root,a.output)
