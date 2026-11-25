"""Count SDK-chain coverage in an extra call outside timed API samples."""
import collections
import json
from pathlib import Path


def inspect(path, schedule):
    data=json.loads(Path(path).read_text())
    if (data.get('version')!=1 or data.get('label')!='dispatch_bf16_chunk32' or
        data.get('after_timed_samples') is not True or data.get('purpose')!='submission_coverage_only'):
        raise ValueError('wrong submission witness boundary')
    cpu=data['cpu']
    if cpu['version']!=1 or cpu['clock']!='CLOCK_MONOTONIC_RAW' or cpu['dropped'] or cpu.get('heartbeat_stride',0):
        raise ValueError('wrong/truncated CPU-only witness')
    streams=collections.defaultdict(list);sizes={}
    for event in cpu['events']:
        streams[event['proxy']].append(event)
        if event['stage']=='payload_bytes':sizes[event['proxy'],event['wr_id']]=event['value']
    rows=[]
    for proxy in range(4):
        pending=[];ended=0;chains=[]
        for e in sorted(streams[proxy],key=lambda e:e['ns']):
            if e['stage']=='post_begin':
                if ended:raise ValueError('nested/incomplete SDK chain')
                pending.append(e)
            elif e['stage']=='post_end':
                if not pending or ended>=len(pending) or pending[ended]['wr_id']!=e['wr_id'] or e['value']:
                    raise ValueError('SDK chain post/end mismatch or failure')
                ended+=1
                if ended==len(pending):chains.append(pending);pending=[];ended=0
        if pending:raise ValueError('truncated SDK submission')
        payload=[];mixed=[];mixed_calls=0
        for chain in chains:
            if len({e['qp'] for e in chain})!=1:raise ValueError('SDK chain spans multiple lanes')
            kinds={e['value'] for e in chain}
            if not kinds <= {0,34}:raise ValueError('unknown URMA data opcode')
            writes=[sizes.get((proxy,e['wr_id']),0) for e in chain if e['value']==0 and e['wr_id']!=2**64-1 and sizes.get((proxy,e['wr_id']),0)>=4096]
            payload.extend(writes)
            if kinds=={0,34}:mixed_calls+=1;mixed.extend(writes)
        if not payload:raise ValueError('missing payload stream')
        rows.append(dict(proxy=proxy,sdk_calls=len(chains),mixed_sdk_calls=mixed_calls,
            payload_posts=len(payload),payload_bytes=sum(payload),mixed_payload_posts=len(mixed),
            mixed_payload_bytes=sum(mixed),mixed_payload_pct=100*sum(mixed)/sum(payload)))
    if schedule=='writes-first' and any(r['mixed_payload_bytes'] for r in rows):
        raise ValueError('writes-first still mixed payload/FADD: intervention not verified')
    return rows
