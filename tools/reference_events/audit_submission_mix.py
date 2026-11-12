"""Audit SDK-chain mixing and observational payload subsets; no causal claim."""
import argparse
import collections
import csv
import json
from pathlib import Path
import tempfile

import summarize_matched as matched
from compare_matched import completion_tails


def chains(events):
    streams=collections.defaultdict(list)
    for event in events:streams[event['proxy']].append(event)
    for proxy,stream in streams.items():
        i=0
        while i<len(stream):
            if stream[i]['stage']!='post_begin':
                i+=1
                continue
            begin=i
            while i<len(stream) and stream[i]['stage']=='post_begin':i+=1
            posted=stream[begin:i]
            ended=stream[i:i+len(posted)]
            if len(ended)!=len(posted) or any(b['stage']!='post_end' or a['wr_id']!=b['wr_id'] or b['value'] for a,b in zip(posted,ended)):
                raise ValueError('unsupported/truncated SDK chain boundaries')
            yield proxy,posted
            i+=len(posted)


def audit(verbs_control,verbs_trace,urma_control,urma_trace,output):
    totals=[];windows=[];subsets=[]
    with tempfile.TemporaryDirectory() as temporary:
        for backend,control,trace in [('verbs',verbs_control,verbs_trace),('urma',urma_control,urma_trace)]:
            summary=Path(temporary)/backend
            accepted=matched.summarize(control,trace,summary)
            if not all(r['representative'] for r in accepted if r['label'].startswith('dispatch_')):
                raise ValueError('unrepresentative Dispatch trace')
            with (summary/'cpu_requests.csv').open(newline='') as stream:requests=list(csv.DictReader(stream))
            lookup={(r['node'],r['label'],r['index'],r['proxy'],r['wr_id']):r for r in requests}
            opcodes=collections.defaultdict(set)
            for r in requests:
                if int(r['physical_posts'])!=1:raise ValueError('audit requires one physical post per complete business request')
                opcodes[int(r['native_opcode'])].add('write' if r['operation'] in ('payload_write','small_write') else r['operation'])
            if any(len(kinds)!=1 for kinds in opcodes.values()):raise ValueError('ambiguous native opcode classification')
            counts=collections.defaultdict(collections.Counter);payloads=collections.defaultdict(list)
            for rank in (0,1):
                case=trace/f'node{rank}'/('normal' if backend=='urma' else 'r01_baseline/normal')
                for window in matched.base.rows(case/'event_trace.jsonl'):
                    if not window['index']:continue
                    key=(rank,window['label'],window['index'])
                    for proxy,chain in chains(window['cpu']['events']):
                        kinds=set().union(*(opcodes[r['value']] for r in chain))
                        if not kinds.issubset({'write','atomic'}):raise ValueError('unexpected SDK operation')
                        mixed=kinds=={'write','atomic'}
                        counts[key]['sdk_calls']+=1
                        counts[key]['mixed_calls']+=int(mixed)
                        for event in chain:
                            r=lookup.get((str(rank),window['label'],str(window['index']),str(proxy),str(event['wr_id'])))
                            if r and r['operation']=='payload_write':payloads[rank,window['label']].append(dict(r,mixed=mixed))
            for (rank,label,index),counter in sorted(counts.items()):
                windows.append(dict(backend=backend,node=rank,label=label,index=index,**counter))
            for (rank,label),payload in sorted(payloads.items()):
                cc=collections.Counter()
                for (r,l,_),counter in counts.items():
                    if (r,l)==(rank,label):cc.update(counter)
                mixed_payload=sum(r['mixed'] for r in payload)
                totals.append(dict(backend=backend,node=rank,label=label,**cc,
                    mixed_sdk_pct=100*cc['mixed_calls']/cc['sdk_calls'],payload_requests=len(payload),
                    mixed_payload_requests=mixed_payload,mixed_payload_pct=100*mixed_payload/len(payload)))
                if 'bf16' not in label:continue
                dominant=collections.Counter(int(r['payload_bytes']) for r in payload).most_common(1)[0][0]
                for size in (None,dominant):
                    for mixed in (False,True):
                        chosen=[r for r in payload if r['mixed']==mixed and (size is None or int(r['payload_bytes'])==size)]
                        if not chosen:continue
                        row=completion_tails(chosen)[0]
                        subsets.append(dict(backend=backend,node=rank,label=label,mixed=mixed,
                            payload_bytes='all' if size is None else size,
                            **{k:v for k,v in row.items() if k not in ('node','label','operation')}))
    output.mkdir(parents=True,exist_ok=False)
    for name,rows in [('submission_mix',totals),('mix_windows',windows),('bf16_payload_subsets',subsets)]:
        matched.base.write(output/(name+'.csv'),rows)
    (output/'context.json').write_text(json.dumps(dict(inputs={
        'verbs_control':str(verbs_control),'verbs_trace':str(verbs_trace),
        'urma_control':str(urma_control),'urma_trace':str(urma_trace)},
        note='Original paced/on validation repeated. SDK calls reconstructed per Proxy from consecutive post_begin records followed by matching post_end records. Mixing requires WRITE and FADD opcodes, mapped from validated complete business requests. Subset observations are NOT randomized treatment groups: lane, position, submission time and previous traffic differ even at equal size. No causal benefit or harm of coalescing follows from these tables.'),indent=2))
    print('SUBMISSION_MIX_AUDIT_PASSED '+str(output))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ('verbs-control','verbs-trace','urma-control','urma-trace','output'):
        parser.add_argument('--'+key,type=Path,required=True)
    args=parser.parse_args()
    audit(args.verbs_control,args.verbs_trace,args.urma_control,args.urma_trace,args.output)
