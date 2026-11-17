"""Bound observable FADD response/poll intervals; never invent device timestamps."""
import argparse
import json
from pathlib import Path
import statistics
import sys

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parents[1]/'tools/reference_events'))
import summarize_matched
base=summarize_matched.base
LABEL='dispatch_bf16_chunk32'


def dense_metrics(pair,polls,marker_status):
    """Partition a LOCAL observed interval into SDK calls and service gaps."""
    w,a=pair['write_sdk_return'],pair['atomic_sdk_return']
    calls=sorted((r for r in polls if r['cq']==pair['cq'] and
                  pair['write_poll_seq']<r['seq']<=pair['atomic_poll_seq']),key=lambda r:r['seq'])
    if [r['seq'] for r in calls]!=list(range(pair['write_poll_seq']+1,pair['atomic_poll_seq']+1)):
        raise ValueError('dense capture missing SDK calls')
    segments=[];previous=w
    for r in calls:
        if not previous<=r['begin']<=r['end']<=a:
            raise ValueError('dense SDK timestamps overlap or escape pair interval')
        segments.extend([('between',previous,r['begin']),('sdk',r['begin'],r['end'])])
        previous=r['end']
    if previous!=a:raise ValueError('dense capture does not end at FADD return')
    def partition(start,prefix):
        totals={'sdk':0,'between':0};maximum={'sdk':0,'between':0}
        for kind,left,right in segments:
            duration=max(0,right-max(left,start))
            totals[kind]+=duration;maximum[kind]=max(maximum[kind],duration)
        if sum(totals.values())!=a-start:raise ValueError('dense partition does not cover interval')
        return {prefix+'elapsed_us':(a-start)/1000,
                prefix+'sdk_us':totals['sdk']/1000,prefix+'between_sdk_us':totals['between']/1000,
                prefix+'max_sdk_us':maximum['sdk']/1000,prefix+'max_between_sdk_us':maximum['between']/1000}
    result=partition(w,'dense_')
    result.update({k:None for k in ('after_seen_elapsed_us','after_seen_sdk_us','after_seen_between_sdk_us',
                                  'after_seen_max_sdk_us','after_seen_max_between_sdk_us')})
    if marker_status=='bounded':result.update(partition(max(w,pair['result_upper']),'after_seen_'))
    return result


def pair_metrics(pair,polls,backend,poll_stride=64):
    p=pair
    if p['atomic_id']!=p['write_id']+1 or p['write_id']>>32 or p['bytes']!=p['operand']*14352 or not 0<p['operand']<=32:
        raise ValueError('not an adjacent BF16 payload/tail pair')
    if not p['finished'] or p['status']:
        raise ValueError('incomplete/error pair')
    w,a=p['write_sdk_return'],p['atomic_sdk_return']
    if not 0<p['write_post_begin']<=p['write_post_end']<=w or not 0<p['atomic_post_begin']<=p['atomic_post_end']<=a:
        raise ValueError('invalid posting/completion chronology')
    if not (p['write_handled']>=w and p['atomic_handled']>=a):raise ValueError('invalid CPU handling timestamp')
    if backend=='urma' and (p['fence']!=1 or p['order']!=2):raise ValueError('changed URMA fence/STRONG')
    if backend=='verbs' and p['fence']!=0:raise ValueError('changed verbs FADD flags')
    selected=[r for r in polls if r['cq']==p['cq']]
    if len({r['seq'] for r in selected})!=len(selected) or [r['seq'] for r in selected]!=sorted(r['seq'] for r in selected):
        raise ValueError('duplicate/out-of-order SDK poll records')
    if any(r['count']<0 or r['end']<=0 or (r['begin'] and r['end']<r['begin']) for r in selected):
        raise ValueError('invalid SDK poll record')
    for seq,t in ((p['write_poll_seq'],w),(p['atomic_poll_seq'],a)):
        matches=[r for r in selected if r['seq']==seq and r['end']==t and r['count']>0]
        if len(matches)!=1:raise ValueError('completion not tied to an actual SDK poll return')
    row=dict(write_id=p['write_id'],atomic_id=p['atomic_id'],bytes=p['bytes'],
             gap_us=(a-w)/1000,atomic_preposted=p['atomic_post_end']<=w,
             write_sdk_to_handled_us=(p['write_handled']-w)/1000,
             atomic_sdk_to_handled_us=(p['atomic_handled']-a)/1000,
             return_value=p['result_at_completion'],marker_status='not_seen_before_cqe',
             before_return_lower_us=None,before_return_upper_us=None,
             after_return_lower_us=None,after_return_upper_us=None,
             return_bracket_width_us=None,empty_polls_after_return=0,
             sdk_poll_calls_between=None,sdk_empty_calls_between=None,
             last_empty_prefix_us=None,last_empty_tail_upper_us=None,max_poll_checkpoint_gap_us=None)
    if poll_stride==1:
        row.update({prefix+key:None for prefix in ('dense_','after_seen_')
                    for key in ('elapsed_us','sdk_us','between_sdk_us','max_sdk_us','max_between_sdk_us')})
    if a<w:
        row['marker_status']='negative_completion_gap';return row
    calls=p['atomic_poll_seq']-p['write_poll_seq']
    positive=sum(r['count']>0 and p['write_poll_seq']<r['seq']<=p['atomic_poll_seq'] for r in selected)
    if calls<positive:raise ValueError('invalid SDK poll sequence accounting')
    row.update(sdk_poll_calls_between=calls,sdk_empty_calls_between=calls-positive)
    checkpoints={w,a}
    for poll in selected:
        for t in (poll['begin'],poll['end']):
            if w<=t<=a:checkpoints.add(t)
    points=sorted(checkpoints)
    row['max_poll_checkpoint_gap_us']=max((y-x for x,y in zip(points,points[1:])),default=0)/1000
    empties=[r for r in selected if r['count']==0 and w<=r['begin']<=r['end']<=a]
    if empties:
        last=max(r['begin'] for r in empties)
        row['last_empty_prefix_us']=(last-w)/1000
        row['last_empty_tail_upper_us']=(a-last)/1000
    if backend=='verbs':
        row['marker_status']='shared_verbs_scratch_not_sampled'
    elif not p['initial_zero']:
        row['marker_status']='initial_value_not_zero_unusable'
    elif not p['result_at_completion']:
        row['marker_status']='return_value_zero_unusable'
    elif p['result_upper'] and p['result_value']!=p['result_at_completion']:
        row['marker_status']='return_value_changed_again_unusable'
    elif p['result_upper'] and p['result_upper']<=a:
        low,high=p['result_lower'],p['result_upper']
        if not p['atomic_post_begin']<=low<=high:raise ValueError('invalid local result observation bracket')
        low,high=max(w,low),max(w,high)
        row.update(marker_status='bounded',before_return_lower_us=(low-w)/1000,before_return_upper_us=(high-w)/1000,
                   after_return_lower_us=(a-high)/1000,after_return_upper_us=(a-low)/1000,
                   return_bracket_width_us=(p['result_upper']-p['result_lower'])/1000,
                   empty_polls_after_return=sum(r['count']==0 and r['begin']>=p['result_upper'] and r['end']<=a for r in selected))
    if poll_stride==1:row.update(dense_metrics(p,selected,row['marker_status']))
    return row


def capture_rows(case,backend,poll_stride=64):
    windows=base.rows(case/'fadd_path.jsonl')
    if len(windows)!=10 or [w['index'] for w in windows]!=list(range(10)):
        raise ValueError('expected all ten BF16 capture windows')
    result=[]
    for w in windows:
        if (w['version'],w['clock'],w['target_payload'],w['ring'],w['poll_stride'],w['label'])!=(1,'CLOCK_MONOTONIC_RAW',6,0,poll_stride,LABEL):
            raise ValueError('wrong completion-path capture recipe')
        streams=w['streams']
        if len(streams)!=4 or {s['proxy'] for s in streams}!={0,1,2,3}:raise ValueError('missing/ambiguous Proxy coverage')
        for stream in streams:
            if stream['dropped'] or stream['errors'] or len(stream['pairs'])!=1:raise ValueError('dropped/error/ambiguous completion path capture')
            metrics=pair_metrics(stream['pairs'][0],stream['polls'],backend,poll_stride)
            if w['index']>0:result.append(dict(index=w['index'],proxy=stream['proxy'],**metrics))
    return result


def summarize(root,backend,output,poll_stride=64):
    campaign=root/backend/'cases';contexts=[];api=[];pairs=[];bounds=[]
    overlays=[base.read(root/backend/('overlay_node%d.json'%r)) for r in (0,1)]
    if overlays[0]!=overlays[1]:raise ValueError('two-node isolated source overlays differ')
    if overlays[0].get('poll_stride',64)!=poll_stride:raise ValueError('overlay polling stride differs from analysis recipe')
    for rank in (0,1):
        runs={stage:base.case_data(campaign/('%s_%s_%s'%(root.name,backend,stage)),rank,False,reference_events='paced')
              for stage in ('pre','probe','post')}
        first=runs['pre'];identity=base.sig(first['identity'])
        for stage,data in runs.items():
            if base.sig(data['identity'])!=identity or data['runtime']['module_sha']!=first['runtime']['module_sha']:
                raise ValueError('source/module/boot/library drift across pre/probe/post')
            if data['runtime'].get('fadd_path_version')!=1:raise ValueError('not the isolated completion-probe module')
            exists=(data['case']/'fadd_path.jsonl').exists()
            if exists!=(stage=='probe') or data['c'].get('fadd_path_capture')!=(stage=='probe'):raise ValueError('wrong active/inactive completion probe')
        contexts.append(dict(node=rank,**first['identity'],module_sha=first['runtime']['module_sha']))
        for label in sorted(base.ALL_LABELS):
            pre,on,post=(runs[s]['means'][label] for s in ('pre','probe','post'))
            reference=(pre['mean_us']+post['mean_us'])/2
            change=(on['mean_us']/reference-1)*100;drift=(post['mean_us']/pre['mean_us']-1)*100
            cv=max(r['cv_pct'] for r in (pre,on,post))
            api.append(dict(node=rank,label=label,pre_us=pre['mean_us'],probe_us=on['mean_us'],post_us=post['mean_us'],
                            probe_change_pct=change,control_drift_pct=drift,max_cv_pct=cv,
                            representative=abs(change)<=5 and abs(drift)<=5 and cv<=5))
        pp=[dict(node=rank,**r) for r in capture_rows(runs['probe']['case'],backend,poll_stride)];pairs.extend(pp)
        visible=[r for r in pp if r['marker_status']=='bounded']
        numeric=('gap_us','max_poll_checkpoint_gap_us','last_empty_prefix_us','last_empty_tail_upper_us',
                 'before_return_lower_us','before_return_upper_us','after_return_lower_us','after_return_upper_us','return_bracket_width_us')
        if poll_stride==1:
            numeric+=('dense_elapsed_us','dense_sdk_us','dense_between_sdk_us','dense_max_sdk_us','dense_max_between_sdk_us',
                      'after_seen_elapsed_us','after_seen_sdk_us','after_seen_between_sdk_us','after_seen_max_sdk_us','after_seen_max_between_sdk_us')
        for key in numeric:
            values=[r[key] for r in pp if r.get(key) is not None]
            bounds.append(dict(node=rank,metric=key,observations=len(values),median_us=statistics.median(values) if values else None))
        bounds.extend([dict(node=rank,metric='marker_bounded_pairs',observations=len(visible),median_us=None),
                       dict(node=rank,metric='bounded_pairs_with_empty_after_return',observations=sum(r['empty_polls_after_return']>0 for r in visible),median_us=None),
                       dict(node=rank,metric='all_pairs',observations=len(pp),median_us=None)])
    accepted=all(r['representative'] for r in api if r['label']==LABEL)
    decision=dict(mechanism_usable=accepted,reason='BF16 5% impact/drift/CV gate passed' if accepted else 'observer_not_representative_stop_no_automatic_rerun',
        direct_device_stage_timestamps=False,
        return_marker_supported=backend=='urma',
        return_marker_bounded_pairs_by_node={str(rank):sum(p['node']==rank and p['marker_status']=='bounded' for p in pairs) for rank in (0,1)},
        note='A local FADD return-data observation is not a remote GPU counter timestamp. CPU no-poll gaps are bounded by checkpoints; CQE hardware readiness is not timestamped. Before-return spans retain dependency/remote/response ambiguity. Bounds and censored counts are preserved; no medians are added to an API critical path.')
    output.mkdir(parents=True,exist_ok=False)
    for name,data in [('api_controls',api),('pairs',pairs),('bounds',bounds)]:base.write(output/(name+'.csv'),data)
    (output/'decision.json').write_text(json.dumps(decision,indent=2))
    (output/'context.json').write_text(json.dumps(dict(nodes=contexts,overlay=overlays[0]),indent=2))
    lines=['# FADD完成推进：本轮观测','',
           'BF16代表性门槛：'+str(accepted)+'。'+decision['reason'],'',
           '总间隔为WRITE所在SDK poll返回→对应FADD所在SDK poll返回；与旧逐WR处理事件略有边界差异。',
           '每端9个有效窗口×4个Proxy，每Proxy选ring0的第6块，共36对；不是36次独立benchmark。','',
           '| 节点 | 指标 | 有效观测数 | 中位数μs |','| --- | --- | ---: | ---: |']
    names={'gap_us':'WRITE→FADD完成观测总间隔','max_poll_checkpoint_gap_us':'相邻SDK轮询检查点最大间隔（不轮询间隔上界）',
           'last_empty_prefix_us':'WRITE观测→最后一次采样空轮询开始','last_empty_tail_upper_us':'最后采样空轮询开始→FADD返回（保守尾部上界）',
           'before_return_lower_us':'WRITE观测→返回值可见：下界','before_return_upper_us':'WRITE观测→返回值可见：上界',
           'after_return_lower_us':'返回值可见→FADD返回：下界','after_return_upper_us':'返回值可见→FADD返回：上界',
           'return_bracket_width_us':'返回值采样时间不确定区间','marker_bounded_pairs':'返回值在FADD CQE之前已观测到的配对数',
           'bounded_pairs_with_empty_after_return':'返回值已见、之后仍有SDK返回0的配对数','all_pairs':'全部配对数'}
    names.update(dense_elapsed_us='密集：WRITE→FADD完成观测总间隔',dense_sdk_us='密集：区间内SDK调用累计耗时',
                 dense_between_sdk_us='密集：区间内SDK调用之间累计耗时',dense_max_sdk_us='密集：最长单次SDK调用',
                 dense_max_between_sdk_us='密集：最长SDK调用间隙',after_seen_elapsed_us='返回值已见后的观测等待',
                 after_seen_sdk_us='返回值已见后：SDK调用累计耗时',after_seen_between_sdk_us='返回值已见后：SDK之间累计耗时',
                 after_seen_max_sdk_us='返回值已见后：最长单次SDK调用',after_seen_max_between_sdk_us='返回值已见后：最长调用间隙')
    for r in bounds:lines.append('| %s | %s | %s | %s |'%('202' if r['node']==0 else '204',names[r['metric']],r['observations'],'—' if r['median_us'] is None else '%.3f'%r['median_us']))
    if poll_stride==1:
        lines+=['','密集分解严格覆盖同一条本端观测间隔。SDK调用内耗时不是设备执行时间；调用间隙包含Proxy其他工作、调度及探针开销，不等于CPU空闲。',
                '返回值已见后取首次非0观测上界作为起点；它是后续实测等待，不代替原子结果物理到达时刻。','',
                '| 节点 | 有效返回分界配对数 | 返回值已见后SDK时间占比（累计比） | 最长查询间隙中位数μs |',
                '| --- | ---: | ---: | ---: |']
        for rank in (0,1):
            rr=[r for r in pairs if r['node']==rank and r.get('after_seen_elapsed_us') is not None]
            total=sum(r['after_seen_elapsed_us'] for r in rr)
            fraction='—' if not total else '%.2f%%'%(sum(r['after_seen_sdk_us'] for r in rr)/total*100)
            maximum='—' if not rr else '%.3f'%statistics.median(r['after_seen_max_between_sdk_us'] for r in rr)
            lines.append('| %s | %d | %s | %s |'%('202' if rank==0 else '204',len(rr),fraction,maximum))
    lines+=['','| 节点 | W→A之间SDK poll次数中位数 | 其中空返回次数中位数 |','| --- | ---: | ---: |']
    for rank in (0,1):
        selected=[r for r in pairs if r['node']==rank and r['sdk_poll_calls_between'] is not None]
        values=[('%.1f'%statistics.median(r[k] for r in selected)) if selected else '—' for k in ('sdk_poll_calls_between','sdk_empty_calls_between')]
        lines.append('| %s | %s | %s |'%('202' if rank==0 else '204',*values))
    lines+=['','返回值标记仅用于URMA独立请求槽；verbs共享scratch不作单WR返回标记，仅对照完成与轮询记录。',
            '若返回值较早可见、之后仍有空轮询，差异至少涉及返回数据到本端之后的完成交付/SDK路径；不能继续全部归到远端原子执行。',
            '若相邻轮询检查点间隔远小于总间隔，可限制“CPU长时间没轮询”的解释；不能单凭SDK空返回断言物理CQE尚不存在。',
            '若返回值很晚或只在CQE处理时才读到，仍保留设备依赖、远端执行与响应返回的合并区间；不强填四段占比。',
            '门槛失败时仅保留带观察器现象，不作正常程序的瓶颈归因。','']
    (output/'REPORT.zh-CN.md').write_text('\n'.join(lines),encoding='utf-8')
    print('FADD_PATH_ANALYSIS_PASSED mechanism_usable='+str(accepted))
    return decision


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--backend',choices=('urma','verbs'),required=True)
    p.add_argument('--poll-stride',type=int,choices=(1,64),default=64)
    a=p.parse_args();summarize(a.root,a.backend,a.output,a.poll_stride)
