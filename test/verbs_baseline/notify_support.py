"""One payload and one notification per lane; fixed four threads/eight lanes."""
MODES=[('write',0,False),('fadd',1,False),('write_fadd',2,False),('gpu_flag',2,True)]

def matched_geometries(path):
    import json
    data=json.loads(path.read_text());geometries=data['geometries']
    if len(geometries)!=2 or {g['label'] for g in geometries}!={'dispatch_fp8_chunk32','dispatch_bf16_chunk32'}:raise ValueError('need FP8/BF16 matched plan')
    for g in geometries:
        if not isinstance(g['bytes'],int) or not 0<g['bytes']<=16*1024*1024 or any(not isinstance(g[k],int) or not 0<=g[k]<4096 for k in ('src_mod','dst_mod')):raise ValueError('invalid matched geometry')
    if not all(p.get('messages')==8 and p.get('window')==1 and p.get('direction')=='bidir' and p.get('local') is False for p in data['points']):raise ValueError('not the matched URMA notification experiment')
    if len(data['points'])!=8:raise ValueError('incomplete matched plan')
    expected={g['label']+'_'+name:(g,op,visible) for g in geometries for name,op,visible in MODES}
    if {p['label'] for p in data['points']}!=set(expected):raise ValueError('matched plan labels differ')
    for p in data['points']:
        if (p['geometry'],p['operation'],p['visible'])!=expected[p['label']]:raise ValueError('matched plan operation/geometry differs')
    return geometries
def points(geometries,repeat=1):
    order=MODES[repeat-1:]+MODES[:repeat-1]
    return [dict(label=g['label']+'_'+name,geometry=g,operation=operation,visible=visible)
            for g in geometries for name,operation,visible in order]
