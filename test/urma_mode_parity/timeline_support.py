"""Local CPU request chains; no cross-machine clock subtraction."""
import json
import collections
def rows(p):return [json.loads(x) for x in p.read_text().splitlines() if x.strip()]

def timeline_rows(path,node):
    windows=rows(path);out=[];coverage=[]
    if len(windows)!=2 or {w['label'] for w in windows}!={'dispatch_fp8_chunk32','dispatch_bf16_chunk32'}:raise ValueError('need FP8/BF16 timelines')
    stages=['collect','prepare','post_begin','post_end','cqe','ack_slot']
    for w in windows:
        if w['dropped'] or w['index']!=0:raise ValueError('truncated/unexpected timeline window')
        by=collections.defaultdict(lambda:collections.defaultdict(list));tails=collections.defaultdict(list)
        for e in w['events']:
            if e['ns']<w['origin_ns'] or e['proxy']<0:raise ValueError('timeline identity/clock mismatch')
            by[(e['proxy'],e['wr_id'])][e['stage']].append(e)
            if e['stage']=='tail_published':tails[(e['proxy'],e['wr_id']>>32)].append(e)
        matched=posted=0
        for (proxy,wrid),events in by.items():
            if 'post_begin' not in events:continue
            posted+=1
            if not all(k in events for k in stages):continue
            qp={e['qp'] for e in events['post_begin']}
            if len(qp)!=1:continue
            ns=[min(e['ns'] for e in events[k]) for k in stages]
            ns[3]=max(e['ns'] for e in events['post_end']);ns[4]=max(e['ns'] for e in events['cqe'])
            if ns!=sorted(ns):raise ValueError('non-monotonic matched request')
            if any(e['value'] for k in ('post_end','cqe') for e in events[k]):raise ValueError('timeline post/CQE error')
            matched+=1
            row=dict(node=node,label=w['label'],proxy=proxy,wr_id=wrid,qp=next(iter(qp)),opcode=events['post_begin'][0]['value'],physical_posts=len(events['post_begin']),collect_offset_us=(ns[0]-w['origin_ns'])/1000)
            for i in range(1,len(stages)):row[stages[i-1]+'_to_'+stages[i]+'_us']=(ns[i]-ns[i-1])/1000
            row['collect_to_ack_slot_us']=(ns[-1]-ns[0])/1000
            released=[e['ns'] for e in tails[(proxy,wrid>>32)] if e['ns']>=ns[-1] and e['value']>(wrid&0xffffffff)]
            row['ack_slot_to_tail_published_us']=(min(released)-ns[-1])/1000 if released else ''
            out.append(row)
        if not matched:raise ValueError('no complete request chains in '+str(path))
        coverage.append(dict(node=node,label=w['label'],posted_commands=posted,matched_commands=matched,match_fraction=matched/posted,events=len(w['events'])))
    return out,coverage
