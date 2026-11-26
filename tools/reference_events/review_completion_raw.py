"""Use uploaded raw completion data without inventing missing provenance."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import statistics

REPO=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('raw_completion_analysis',REPO/'test/normal_completion/analyze.py')
completion=importlib.util.module_from_spec(spec);spec.loader.exec_module(completion)
base=completion.base


def fadd_readability_bounds(pair, write, atomic):
    posted = pair['atomic_post_end']
    delivered = pair['atomic_sdk_return']
    result_early = pair['result_lower']
    result_late = pair['result_upper']
    last_invalid = atomic['last_invalid_begin_ns']
    cqe_latest = atomic['first_valid_end_ns'] or delivered
    bounded = (pair['initial_zero'] and pair['result_value'] != 0 and
               pair['result_value'] == pair['result_at_completion'] and
               posted <= result_early <= result_late <= delivered and
               last_invalid is not None and result_early <= cqe_latest)
    row = dict(node=atomic['node'], index=atomic['index'], proxy=atomic['proxy'],
               write_id=write['wr_id'], fadd_id=atomic['wr_id'], fence=pair['fence'],
               same_sdk_poll=pair['write_poll_seq'] == pair['atomic_poll_seq'],
               result_bracket_usable=bool(bounded),
               post_to_result_lower_us=None, post_to_result_upper_us=None,
               result_to_cqe_lower_us=None, result_to_cqe_upper_us=None,
               cqe_to_sdk_upper_us=atomic['readable_to_sdk_upper_us'],
               write_still_invalid_after_result=None)
    if bounded:
        write_invalid = write['last_invalid_begin_ns']
        row.update(post_to_result_lower_us=(result_early - posted) / 1000,
                   post_to_result_upper_us=(result_late - posted) / 1000,
                   result_to_cqe_lower_us=max(0, last_invalid - result_late) / 1000,
                   result_to_cqe_upper_us=(cqe_latest - result_early) / 1000,
                   write_still_invalid_after_result=(write_invalid >= result_late
                                                     if write_invalid is not None else None))
    return row


def review(root,output):
    sources={};raw={};operations=[];drains=[];missing=[];fadd_bounds=[]
    for rank in (0,1):
        for name in ('overlay','setup','request'):
            path=root/('%s_node%d.json'%(name,rank))
            if not path.exists():missing.append(path.as_posix())
        for stage in ('pre','probe','post'):
            node=root/'cases'/(root.name+'_'+stage)/('node%d'%rank)
            case=node/'normal'
            for path in [node/'setup.json',node/'host.json',node/'build_manifest.json',case/'runtime.json',case/'status.json',case/'bench.log']:
                if not path.exists():missing.append(path.as_posix())
            path=case/'api_samples.jsonl';rows=base.rows(path)
            if [r['index'] for r in rows]!=list(range(10)) or any(r['label']!=completion.LABEL or r['used']!=(r['index']>0) or r['us']<=0 for r in rows):
                raise ValueError('invalid API sample plan')
            sources[path.as_posix()]=hashlib.sha256(path.read_bytes()).hexdigest()
            raw[stage,rank]={r['index']:r['us'] for r in rows if r['used']}
        path=root/'cases'/(root.name+'_probe')/('node%d'%rank)/'normal/fadd_path.jsonl'
        sources[path.as_posix()]=hashlib.sha256(path.read_bytes()).hexdigest()
        windows=base.rows(path)
        if [w['index'] for w in windows]!=list(range(10)):raise ValueError('missing CPU windows')
        for window in windows:
            recipe = (window['version'], window['target_payload'], window['ring'], window['poll_stride'], window['label'], window['clock'])
            if recipe != (1, 6, 0, 1, completion.LABEL, 'CLOCK_MONOTONIC_RAW'):
                raise ValueError('unexpected completion window recipe')
            rows,controls=completion.operation_rows(window,rank)
            if not window['index']:continue
            if window['us'] != raw['probe',rank][window['index']]:
                raise ValueError('completion window does not match API sample')
            pair_by_proxy={s['proxy']:s['pairs'][0] for s in window['streams']}
            by_request = {}
            for row in rows:
                pair=pair_by_proxy[row['proxy']]
                returned=pair[row['operation']+'_sdk_return']
                invalid=row['last_invalid_begin_ns']
                row['readable_to_sdk_upper_us']=(returned-invalid)/1000 if invalid is not None else None
                row['post_to_last_invalid_share_lower_pct']=100*row['post_to_last_invalid_lower_us']/row['post_to_sdk_us'] if invalid is not None else None
                by_request[row['proxy'], row['operation']] = row
            for proxy, pair in pair_by_proxy.items():
                fadd_bounds.append(fadd_readability_bounds(pair, by_request[proxy,'write'], by_request[proxy,'atomic']))
            operations.extend(rows);drains.extend(controls)
    controls=[]
    for who in (0,1,'paired_max'):
        stats={}
        for stage in ('pre','probe','post'):
            values=list(raw[stage,who].values()) if who!='paired_max' else [max(raw[stage,0][i],raw[stage,1][i]) for i in range(1,10)]
            stats[stage]=dict(mean=statistics.mean(values),cv=100*statistics.pstdev(values)/statistics.mean(values))
        a,p,b=[stats[s]['mean'] for s in ('pre','probe','post')]
        effect=100*(p/((a+b)/2)-1);drift=100*(b/a-1);cv=max(r['cv'] for r in stats.values())
        controls.append(dict(node=who,pre_us=a,probe_us=p,post_us=b,probe_change_pct=effect,drift_pct=drift,max_cv_pct=cv,numeric_gate_passed=max(abs(effect),abs(drift),cv)<=5))
    summary=[]
    for rank in (0,1):
        for operation in ('write','atomic'):
            selected=[r for r in operations if r['node']==rank and r['operation']==operation]
            upper=[r['readable_to_sdk_upper_us'] for r in selected if r['readable_to_sdk_upper_us'] is not None]
            summary.append(dict(node=rank,operation=operation,targets=len(selected),valid_observed=sum(r['observed_valid'] for r in selected),upper_bound_observations=len(upper),
                post_to_sdk_median_us=statistics.median(r['post_to_sdk_us'] for r in selected),
                readable_to_sdk_upper_median_us=statistics.median(upper) if upper else None,readable_to_sdk_upper_max_us=max(upper,default=None),
                pre_last_invalid_share_lower_median_pct=statistics.median(r['post_to_last_invalid_share_lower_pct'] for r in selected if r['post_to_last_invalid_share_lower_pct'] is not None) if upper else None,
                pre_last_invalid_share_lower_min_pct=min(r['post_to_last_invalid_share_lower_pct'] for r in selected if r['post_to_last_invalid_share_lower_pct'] is not None) if upper else None,
                targets_valid_behind_invalid_head=sum(r['target_valid_head_invalid_polls']>0 for r in selected)))
    quiet=[]
    for rank in (0,1):
        selected=[r for r in drains if r['node']==rank and r['kind']=='quiet']
        sums=[sum(r['cpu_processing_us'] for r in selected if r['index']==index and r['proxy']==proxy) for index in range(1,10) for proxy in range(4)]
        quiet.append(dict(node=rank,observations=len(selected),single_cpu_median_us=statistics.median(r['cpu_processing_us'] for r in selected),
            single_cpu_max_us=max(r['cpu_processing_us'] for r in selected),max_call_proxy_cpu_sum_us=max(sums)))
    fadd_summary = []
    for rank in (0, 1):
        selected = [r for r in fadd_bounds if r['node'] == rank]
        bounded = [r for r in selected if r['result_bracket_usable']]
        row = dict(node=rank, targets=len(selected), bounded=len(bounded),
                   same_sdk_poll=sum(r['same_sdk_poll'] for r in selected),
                   fadd_still_invalid_after_result=sum(r['result_to_cqe_lower_us'] > 0 for r in bounded),
                   write_still_invalid_after_result=sum(r['write_still_invalid_after_result'] is True for r in bounded))
        for field in ('post_to_result_lower_us', 'post_to_result_upper_us', 'result_to_cqe_lower_us', 'result_to_cqe_upper_us', 'cqe_to_sdk_upper_us'):
            samples = [r[field] for r in bounded]
            row[field[:-3] + '_median_us'] = statistics.median(samples) if samples else None
        row['result_to_cqe_lower_min_us'] = min((r['result_to_cqe_lower_us'] for r in bounded), default=None)
        row['result_to_cqe_lower_max_us'] = max((r['result_to_cqe_lower_us'] for r in bounded), default=None)
        fadd_summary.append(row)
    output.mkdir(parents=True,exist_ok=False)
    for name,rows in [('api_controls',controls),('operations',operations),('operation_summary',summary),('quiet_cpu_summary',quiet),
                      ('fadd_readability_bounds',fadd_bounds),('fadd_readability_summary',fadd_summary)]:base.write(output/(name+'.csv'),rows)
    context=dict(sources_sha256=sources,missing_provenance_files=missing,provenance_complete=not missing,
        provenance_verified=False,full_acceptance=False,
        note='Raw numeric controls and request/CQ identities independently checked. No missing metadata inferred. '
        'The completion-readability-to-SDK upper bound uses actual SDK return minus the BEGIN of the last target-invalid snapshot, '
        'assuming the recorded provider owner-generation protocol and one consumption of this CQ position. '
        'Bounds and percentages concern selected single-request lifetimes, never Dispatch contributions. '
        'CPU QUIET processing excludes GPU publication/wait. Run the strict analyzer after restoring existing ignored metadata; no GPU rerun required.')
    (output/'context.json').write_text(json.dumps(context,indent=2),encoding='utf-8')
    print('RAW_COMPLETION_REVIEW_WRITTEN numeric_gate='+str(all(r['numeric_gate_passed'] for r in controls))+' missing_metadata='+str(len(missing)))
    return context


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();review(a.root,a.output)
