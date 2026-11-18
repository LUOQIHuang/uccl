"""Compare two completed serial campaigns without treating zero gaps as ratios."""
import argparse,csv,json
from pathlib import Path
from analyze import LABELS,read,write
def compare(root,out):
    contexts={};rows={}
    for backend in ('verbs','urma'):
        folder=root/('summary_'+backend)
        contexts[backend]=read(folder/'context.json')
        with (folder/'summary.csv').open() as f:rows[backend]={r['label']:r for r in csv.DictReader(f)}
        if set(rows[backend])!=set(LABELS):raise ValueError('missing serial comparison points')
    v,u=contexts['verbs'],contexts['urma']
    if v['overlay']!=u['overlay']:raise ValueError('backend overlays differ')
    for key in ('version','rounds','warmups','samples','bytes','notify_add','active_threads','active_data_queues','max_inflight_pairs'):
        if v['plan'][key]!=u['plan'][key]:raise ValueError('different serial plans: '+key)
    if v['plan']['source_id']['sources']!=u['plan']['source_id']['sources']:raise ValueError('different production sources')
    result=[]
    for label in LABELS:
        vr,ur=rows['verbs'][label],rows['urma'][label]
        vt=float(vr['median_run_mean_sdk_span_us']);ut=float(ur['median_run_mean_sdk_span_us'])
        result.append(dict(label=label,verbs_sdk_span_us=vt,urma_sdk_span_us=ut,urma_change_pct=(ut/vt-1)*100,
            verbs_pair_median_us=vr['median_pair_us'],urma_pair_median_us=ur['median_pair_us'],
            verbs_gap_median_us=vr['median_gap_us'],urma_gap_median_us=ur['median_gap_us'],
            both_stable=vr['stable']=='True' and ur['stable']=='True'))
    out.mkdir(parents=True,exist_ok=False);write(out/'comparison.csv',result)
    lines=['# RDMA/URMA最小串行对照','',
        '总SDK跨度是1对（32）或2对（64）严格串行请求的本端观测跨度；不是完整Normal Dispatch。后端内核/设备/SDK不同。','',
        '| 点 | RDMA总跨度 μs | URMA总跨度 μs | URMA变化 | RDMA W→A中位 μs | URMA W→A中位 μs | 两者稳定 |',
        '| --- | ---: | ---: | ---: | ---: | ---: | --- |']
    for r in result:lines.append('| %s | %.3f | %.3f | %+.2f%% | %.3f | %.3f | %s |'%(r['label'],r['verbs_sdk_span_us'],r['urma_sdk_span_us'],r['urma_change_pct'],float(r['verbs_gap_median_us']),float(r['urma_gap_median_us']),r['both_stable']))
    lines+=['','0μs间隔可能是同次SDK轮询同时返回，不计算以0为分母的倍数。稳定标记失败时只保留原始对比，不作稳定后端优势结论。',
        '若URMA在此仍有数百μs间隔，说明多通道/原Proxy并非复现的必要条件；若消失，则问题依赖原并行负载、Proxy服务或注册布局等环境，不能仅据此认定并发是唯一原因。']
    (out/'REPORT.zh-CN.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    (out/'context.json').write_text(json.dumps(contexts,indent=2));print('SERIAL_PAIR_COMPARISON_PASSED')
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();compare(a.root,a.output)
