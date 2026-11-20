"""Associate pre-poll CQ owner observations with the FADD later returned by SDK."""
import argparse
import json
from pathlib import Path
import statistics

import analyze
import analyze_tail_scope as tail

base=analyze.base


def pair_rows(window):
    sources={s['proxy']:s for s in window['streams']}
    snapshots={s['proxy']:s for s in window['cq_ready']}
    if set(sources)!=set(range(4)) or set(snapshots)!=set(sources):
        raise ValueError('missing Proxy or CQ readiness stream')
    output=[]
    for proxy,source in sources.items():
        if source['errors'] or source['dropped'] or len(source['pairs'])!=1:
            raise ValueError('invalid FADD stream')
        pair=source['pairs'][0];snap=snapshots[proxy]
        if snap['dropped'] or snap['lookahead']!=16 or snap['depth']<16 or snap['stride'] not in (64,128,256):
            raise ValueError('wrong or truncated CQ layout')
        metrics=analyze.pair_metrics(pair,source['polls'],'urma',1,expected_fence=0)
        polls=snap['polls']
        if [p['seq'] for p in polls]!=list(range(1,len(polls)+1)):
            raise ValueError('missing CQ snapshots')
        sdk={p['seq']:p for p in source['polls']}
        matches=[]
        previous_ci=None
        for p in polls:
            observed=sdk.get(p['seq'])
            if observed is None or observed['count']!=p['count'] or observed['end']!=p['sdk_return']:
                raise ValueError('CQ snapshot/SDK polling mismatch')
            if not observed['begin']<=p['begin']<=p['end']<=p['sdk_return'] or p['count']<0:
                raise ValueError('invalid CQ snapshot clock')
            if p['after_ci']-p['ci']!=p['count'] or (previous_ci is not None and previous_ci!=p['ci']):
                raise ValueError('CQ consumer index/SDK completion count mismatch')
            previous_ci=p['after_ci']
            if len(p['completed'])!=p['count'] or not 0<=p['ready_mask']<1<<16:
                raise ValueError('missing completion identities or invalid ready mask')
            for i,c in enumerate(p['completed']):
                if c['position']!=p['ci']+i:raise ValueError('wrong consumed CQ position')
                if c['wr_id']==pair['atomic_id']:matches.append((p,c))
        if len(matches)!=1:raise ValueError('ambiguous target FADD CQ position')
        completed,identity=matches[0]
        if completed['seq']!=pair['atomic_poll_seq'] or completed['sdk_return']!=pair['atomic_sdk_return']:
            raise ValueError('target FADD mapping disagrees with existing probe')
        position=identity['position']
        views=[p for p in polls if p['ci']<=position<p['ci']+16]
        ready=[p for p in views if p['ready_mask']&(1<<(position-p['ci']))]
        # Owner bits are inspected before the SDK consumes any CQEs in this
        # call. A positive count can follow an empty snapshot if DMA progresses.
        for p in views:
            state=bool(p['ready_mask']&(1<<(position-p['ci'])))
            if ready and p['seq']>ready[0]['seq'] and not state:
                raise ValueError('target CQE owner became invalid before consumption')
        first=ready[0] if ready else None
        prior=[p for p in views if first is None or p['seq']<first['seq']]
        invalid=prior[-1] if prior else None
        blocked=[p for p in ready if p['ci']<position and not p['ready_mask']&1]
        at_head=[p for p in ready if p['ci']==position]
        after_result=[p for p in polls if pair['result_upper'] and p['begin']>=pair['result_upper']
                      and p['sdk_return']<=pair['atomic_sdk_return']]
        output.append(dict(index=window['index'],proxy=proxy,atomic_id=pair['atomic_id'],
            token=identity['token'],cq_position=position,lookahead=16,
            return_seen_before_cqe=pair['result_upper']>0 and pair['result_upper']<=pair['atomic_sdk_return'],
            first_valid_begin_ns=first['begin'] if first else None,
            first_valid_end_ns=first['end'] if first else None,
            last_invalid_begin_ns=invalid['begin'] if invalid else None,
            last_invalid_end_ns=invalid['end'] if invalid else None,
            valid_to_sdk_lower_us=(pair['atomic_sdk_return']-first['end'])/1000 if first else None,
            valid_to_sdk_upper_us=(pair['atomic_sdk_return']-invalid['begin'])/1000 if invalid and first else None,
            result_to_last_invalid_lower_us=max(0,invalid['begin']-pair['result_upper'])/1000 if invalid and pair['result_upper'] else None,
            target_valid_head_invalid_polls=len(blocked),target_valid_at_head_polls=len(at_head),
            polls_after_result=len(after_result),head_invalid_polls_after_result=sum(not p['ready_mask']&1 for p in after_result),
            target_observed_polls=len(views),target_observed_valid=bool(first),
            gap_us=metrics['gap_us'],**analyze.tail_result_bounds(pair,source['polls'])))
    return output


def summarize(root,output):
    overlays=[base.read(root/'urma'/f'overlay_node{r}.json') for r in (0,1)]
    if overlays[0]!=overlays[1] or not overlays[0].get('cq_ready'):raise ValueError('wrong/different source overlay')
    data={};identities={};controls=[]
    for stage in ('pre','probe','post'):
        location=root/'urma/cases'/f'{root.name}_urma_{stage}'
        for rank in (0,1):
            d=base.case_data(location,rank,False,reference_events='paced',bf16_only=True)
            if not d['c'].get('cq_ready_campaign') or d['c']['normal_fadd_fence_plan']!='peer-tail-off':
                raise ValueError('wrong CQ campaign or fence configuration')
            identity=dict(d['identity'],module_sha=d['runtime']['module_sha'])
            if identity!=identities.setdefault(rank,identity):raise ValueError('source/module/boot/runtime drift')
            if (d['case']/'tail_receiver.jsonl').exists():raise ValueError('unexpected GPU observer')
            if (d['case']/'fadd_path.jsonl').exists()!=(rank==1 and stage=='probe'):
                raise ValueError('wrong CPU probe ownership')
            if d['c']['fadd_path_capture']!=(stage=='probe'):raise ValueError('wrong probe phase')
            data[stage,rank]=d
    for rank in (0,1):
        controls.append(tail.control_row('peer-tail-off',rank,*(data[s,rank]['means'][analyze.LABEL] for s in ('pre','probe','post'))))
    paired=[];paired_stats={}
    for stage in ('pre','probe','post'):
        samples={r:{v['index']:v['us'] for v in base.rows(data[stage,r]['case']/'api_samples.jsonl') if v['used']} for r in (0,1)}
        if samples[0].keys()!=samples[1].keys():raise ValueError('unpaired API windows')
        values=[max(samples[0][i],samples[1][i]) for i in samples[0]]
        paired.extend(dict(stage=stage,index=i,node202_us=samples[0][i],node204_us=samples[1][i],paired_max_us=max(samples[0][i],samples[1][i])) for i in samples[0])
        paired_stats[stage]=tail.value_stats(values)
    controls.append(tail.control_row('peer-tail-off','paired_max',*(paired_stats[s] for s in ('pre','probe','post'))))
    windows=base.rows(data['probe',1]['case']/'fadd_path.jsonl')
    if [w['index'] for w in windows]!=list(range(10)):raise ValueError('expected ten probe windows')
    api={r['index']:r['us'] for r in base.rows(data['probe',1]['case']/'api_samples.jsonl')}
    pairs=[]
    for w in windows:
        if (w['version'],w['target_payload'],w['ring'],w['poll_stride'],w['label'],w['clock'])!=(1,6,0,1,analyze.LABEL,'CLOCK_MONOTONIC_RAW'):
            raise ValueError('wrong capture recipe')
        if w['us']!=api[w['index']]:raise ValueError('CQ window/API measurement mismatch')
        rows=pair_rows(w)
        if w['index']:pairs.extend(rows)
    accepted=all(r['representative'] for r in controls)
    observed=[r for r in pairs if r['target_observed_valid']]
    decision=dict(mechanism_usable=accepted,target_pairs=len(pairs),valid_observed_pairs=len(observed),
                  pairs_with_target_valid_behind_invalid_head=sum(r['target_valid_head_invalid_polls']>0 for r in pairs),
                  no_valid_snapshot_pairs=len(pairs)-len(observed),physical_publication_timestamp=False,
                  note='Pre-poll owner observations provide software visibility bounds. Unobserved target entries and gaps between snapshots remain censored. Completion position is verified against actual SDK count and consumer advancement. Local waits overlap across requests.')
    output.mkdir(parents=True,exist_ok=False)
    for name,rows in [('controls',controls),('paired_calls',paired),('pairs',pairs)]:base.write(output/(name+'.csv'),rows)
    (output/'decision.json').write_text(json.dumps(decision,indent=2)+'\n',encoding='utf-8')
    (output/'context.json').write_text(json.dumps(dict(overlay=overlays[0],nodes=identities),indent=2)+'\n',encoding='utf-8')
    def median(key):
        values=[r[key] for r in pairs if r[key] is not None]
        return '—' if not values else '%.3f'%statistics.median(values)
    lines=['# tail-off 的 CQE 就绪观测','',f'阶段代表性通过 {accepted}。有效配对 {len(pairs)}。',
           f'在 SDK 前观测到目标 CQE 有效的配对 {len(observed)}。',
           f"目标已有效而队首未有效的配对 {decision['pairs_with_target_valid_behind_invalid_head']}。",'',
           '首次观测目标有效之后到 SDK 交付的等待中位下界 μs '+median('valid_to_sdk_lower_us'),
           'FADD 返回值可读之后到最后一次目标无效观测的中位下界 μs '+median('result_to_last_invalid_lower_us'),'',
           '目标不在16条前视范围内时不判断其有效性。没有抓到有效瞬间时保留删失，不能认定设备此前没有发布。',
           '目标有效但队首无效是完成队列消费受阻的直接观察，还需完整 Dispatch 的隔离 JFC 干预验证性能影响。',
           '目标持续无效说明应用尚不可读取该 CQE，其原因仍可能在设备排队、完成发布及内存可见性路径。',
           '门槛失败时只保留带探针现象。时间区间均在204本机，不相加为整体 Dispatch 的贡献。','']
    (output/'REPORT.zh-CN.md').write_text('\n'.join(lines),encoding='utf-8')
    print('CQ_READY_ANALYSIS_PASSED mechanism_usable='+str(accepted))
    return decision


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();summarize(a.root,a.output)
