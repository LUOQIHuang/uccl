"""Execute equal-byte WRITE/FADD load shapes on the selected backend."""
import faulthandler,json,os,sys,traceback
from pathlib import Path
from analyze import SUITES, validate_path
HERE=Path(__file__).resolve().parent;REPO=HERE.parents[1]
r=json.loads(Path(os.environ['SERIAL_PAIR_REQUEST']).read_text());case=Path(r['case']);backend=r['backend'];rank=r['node_rank']
sys.path.insert(0,str(REPO/'test'/('urma_mode_parity' if backend=='urma' else 'verbs_baseline')))
if backend=='urma':
    import support
    ep,runtime=support.load_extension(Path(r['module']))
else:
    import common
    ep,runtime=common.load_extension(Path(r['module']))
sys.path.insert(0,str(REPO/'test/urma_mode_parity'))
from micro_support import Peer
faulthandler.enable()
def append(name,row):
    with (case/name).open('a') as f:f.write(json.dumps(row)+'\n')
def choose_cpu():
    allowed=set(os.sched_getaffinity(0))
    pci=Path('/sys/bus/pci/devices')/r['gpu_pci']
    node=int((pci/'numa_node').read_text())
    local=set()
    if node>=0:
        cpulist=Path('/sys/devices/system/node')/('node%d'%node)/'cpulist'
        for part in cpulist.read_text().strip().split(','):
            first, _, last=part.partition('-')
            local.update(range(int(first),int(last or first)+1))
    if r['worker_cpu']=='auto':
        candidates=allowed & local
        if not candidates:raise ValueError('no CPU in GPU NUMA node')
        cpu=min(candidates)
    else:cpu=int(r['worker_cpu'])
    if cpu not in allowed or not 0<=cpu<1024:raise ValueError('unavailable worker CPU')
    if node>=0 and cpu not in local:
        raise ValueError('CPU %d is outside GPU NUMA node %d'%(cpu,node))
    os.sched_setaffinity(0,{cpu})
    if os.sched_getaffinity(0)!={cpu}:raise ValueError('worker pin verification failed')
    return dict(cpu=cpu,gpu_numa_node=node,gpu_local_cpu=(cpu in local if node>=0 else None),
                allowed_before=sorted(allowed),effective=sorted(os.sched_getaffinity(0)))
peer=None;session=None
suite=r.get('suite','basic4')
shapes,count=SUITES[suite]
SHAPES=[(name,lanes,limit) for name,(lanes,limit) in shapes.items()]
try:
    session_type=ep.UrmaMicroSession if backend=='urma' else ep.VerbsWriteSession
    if ep.get_urma_pair_load_version()!=2 or not hasattr(session_type,'load_prepare') or runtime['module_sha']!=r['module_sha']:
        raise RuntimeError('load module missing or changed')
    placement=choose_cpu();(case/'affinity.json').write_text(json.dumps(placement,indent=2));(case/'runtime.json').write_text(json.dumps(runtime,indent=2))
    peer=Peer(rank,r['master_addr'],r['port'])
    plan=dict(version=2,suite=suite,backend=backend,job=r['job'],rounds=r['rounds'],warmups=r['warmups'],samples=r['samples'],
        bytes=459264,notify_add=32,pairs_per_iteration=count,active_threads=1,shapes=SHAPES,source_id=r['source_id'])
    # JSON transport converts tuples to lists.
    plan=json.loads(json.dumps(plan));peer.agree('plan',plan);(case/'plan.json').write_text(json.dumps(plan,indent=2))
    completed=0
    for repeat in range(1,r['rounds']+1):
        points=[(name,lanes,limit,sender) for name,lanes,limit in SHAPES for sender in (0,1)]
        if repeat%2==0:points.reverse()
        for name,lanes,limit,sender in points:
            label=name+'_%dto%d'%(sender,1-sender);send=rank==sender
            peer.agree('prepare',dict(repeat=repeat,label=label))
            if backend=='urma':
                session=session_type(0,1,459264,True,lanes)
                local=session.descriptors(1)
            else:
                session=session_type(r['hca'],r['gid_index'],1,459264,True,True,lanes)
                local=[session.descriptor(0)]
            path=json.loads(session.metadata());append('paths.jsonl',dict(repeat=repeat,label=label,path=path))
            validate_path(path,backend,lanes)
            remote=peer.exchange('descriptors',[bytes(b).hex() for b in local])
            session.connect([bytes.fromhex(b) for b in remote])
            session.load_prepare(limit,rank,count)
            if backend=='urma':session.experiment(send,not send,False,[placement['cpu']])
            else:session.experiment(send,not send,[placement['cpu']])
            for seq in range(r['warmups']+r['samples']):
                peer.agree('ready',dict(repeat=repeat,label=label,seq=seq))
                sample=json.loads(session.load_step(seq,limit,count))
                peer.agree('done',dict(repeat=repeat,label=label,seq=seq))
                error=None
                try:session.load_validate(seq)
                except Exception as exc:error=str(exc)
                remote_error=peer.exchange('validate',error)
                if error or remote_error:raise RuntimeError('load GPU validation failed: '+str([error,remote_error]))
                if seq>=r['warmups']:
                    index=seq-r['warmups'];append('samples.jsonl',dict(repeat=repeat,label=label,shape=name,lanes=lanes,limit=limit,sender=sender,
                        index=index,used=index>0,rank=rank,validation=True,sample=sample))
            peer.agree('close_point',dict(repeat=repeat,label=label));session.close();session=None
            peer.agree('point_closed',0);completed+=1
            print('URMA_PAIR_LOAD_POINT_PASSED '+label+' round='+str(repeat),flush=True)
    peer.agree('complete',completed)
    (case/'status.json').write_text(json.dumps(dict(passed=True,points=completed,rounds=r['rounds'],correctness=True)))
    print('URMA_PAIR_LOAD_ALL_PASSED',flush=True)
except BaseException as exc:
    (case/'status.json').write_text(json.dumps(dict(passed=False,error=str(exc),traceback=traceback.format_exc()),indent=2));raise
finally:
    if session is not None:session.close()
    if peer is not None:peer.close()
