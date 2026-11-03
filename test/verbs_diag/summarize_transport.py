"""Summarize boundary control/trace passes; no GPU or SDK required."""
import argparse
import csv
import json
from pathlib import Path
import profile_support
import transport_support

def read_jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

def collect(results):
    rows=[]
    for path in sorted(results.rglob('boundary_timings.jsonl')):
        case=path.parent
        status=json.loads((case/'status.json').read_text())
        if status['rc']!=0 or not all(status['checks'].values()):
            raise RuntimeError(f'failed case: {case}')
        setup=json.loads((case.parent/'setup.json').read_text())['fingerprint']
        timings=read_jsonl(path)
        expected=set(profile_support.plan('boundaries'))
        if len(timings)!=len(expected) or {(r['op'],r['rdma']) for r in timings}!=expected:
            raise RuntimeError(f'incomplete boundary timings: {case}')
        traces={(r['op'],r['rdma']):r for r in read_jsonl(case/'transport.jsonl')} if setup['trace_transport'] else {}
        if setup['trace_transport'] and set(traces)!=expected:
            raise RuntimeError(f'incomplete transport trace: {case}')
        for timing in timings:
            row=dict(job=setup['job'],node=case.parent.name,case=case.name,op=timing['op'],rdma=timing['rdma'],iterations=timing['iterations'],transport_trace=timing['transport_trace'],api_cuda_event_us=timing['api_cuda_event_us'])
            keys=('write_wrs_per_call','fadd_wrs_per_call','write_bytes_per_call','posted_wrs_per_call','completed_per_call','empty_poll_ratio','completions_per_nonempty_poll','max_thread_inflight','remaining_tracked','unknown_completions','post_write_sample_mean_ns','post_fadd_sample_mean_ns','poll_sample_mean_ns','write_observed_sample_mean_ns','fadd_observed_sample_mean_ns')
            row.update({key:None for key in keys})
            if timing['transport_trace']:
                data=traces[(timing['op'],timing['rdma'])]
                totals=transport_support.validate(data,data['expected_proxy_tids'])
                row.update({key+'_per_call':totals[key]/timing['iterations'] for key in ('write_wrs','fadd_wrs','write_bytes','posted_wrs','completed')})
                row.update(empty_poll_ratio=totals['empty_poll_ratio'],completions_per_nonempty_poll=totals['completions_per_nonempty_poll'],max_thread_inflight=max(t['peak_inflight'] for t in data['threads']),remaining_tracked=totals['inflight'],unknown_completions=totals['unknown_completions'])
                for name in ('post_write','post_fadd','poll','write_observed','fadd_observed'):
                    samples=sum(t[name]['samples'] for t in data['threads'])
                    ns=sum(t[name]['total_ns'] for t in data['threads'])
                    row[name+'_sample_mean_ns']=ns/samples if samples else None
            rows.append(row)
    if not rows: raise RuntimeError('no boundary timings found')
    return rows

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('results',type=Path)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    output=args.output or args.results/'boundary_summary.csv'
    rows=collect(args.results)
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('w',newline='',encoding='utf-8-sig') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    print(f'WROTE={output} rows={len(rows)}')

if __name__=='__main__': main()
