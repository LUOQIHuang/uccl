"""Matched four-thread/eight-lane Normal notification plan."""
MODES=[('write',0,False),('fadd',1,False),('write_fadd',2,False),('gpu_flag',2,True)]
def points(geometries,repeat=1):
    order=MODES[repeat-1:]+MODES[:repeat-1]
    return [dict(label=g['label']+'_'+name,geometry=g,operation=op,visible=visible,
                 window=1,messages=8,fadd=op!=0,observation='flag' if visible else 'cqe',direction='bidir',local=False)
            for g in geometries for name,op,visible in order]
