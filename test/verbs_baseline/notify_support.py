"""One payload and one notification per lane; fixed four threads/eight lanes."""
MODES=[('write',0,False),('fadd',1,False),('write_fadd',2,False),('gpu_flag',2,True)]
def points(geometries,repeat=1):
    order=MODES[repeat-1:]+MODES[:repeat-1]
    return [dict(label=g['label']+'_'+name,geometry=g,operation=operation,visible=visible)
            for g in geometries for name,operation,visible in order]
