"""Same bytes: serial four pairs vs one/four data lanes with four outstanding."""
import faulthandler,json,os,sys,traceback
from pathlib import Path
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
    allowed=set(os.sched_getaffinity(0));pci=Path('/sys/bus/pci/devices')/r['gpu_pci']
    node=int((pci/'numa_node').read_text())
    if r['worker_cpu']=='auto':
        if node<0:raise ValueError('GPU NUMA unknown; specify --worker-cpu explicitly')
        def expand(s):
            out=set()
            for part in s.strip().split(','):
                a,b=map(int,part.split('-')) if '-' in part else (int(part),int(part))
                out.update(range(a,b+1))
            return out
        candidates=allowed & expand((Path('/sys/devices/system/node')/('node%d'%node)/'cpulist').read_text())
        if not candidates:raise ValueError('no CPU in GPU NUMA node')
        cpu=min(candidates)
    else:cpu=int(r['worker_cpu'])
    if cpu not in allowed or not 0<=cpu<1024:raise ValueError('unavailable worker CPU')
    os.sched_setaffinity(0,{cpu})
    if os.sched_getaffinity(0)!={cpu}:raise ValueError('worker pin verification failed')
    return dict(cpu=cpu,gpu_numa_node=node,allowed_before=sorted(allowed),effective=sorted(os.sched_getaffinity(0)))
peer=None;session=None
SHAPES=[('serial4',1,1),('queue4',1,4),('lanes4',4,4)]
try:
    if backend!='urma' or ep.get_urma_pair_load_version()!=1 or runtime['module_sha']!=r['module_sha']:raise RuntimeError('load module missing or changed')
    placement=choose_cpu();(case/'affinity.json').write_text(json.dumps(placement,indent=2));(case/'runtime.json').write_text(json.dumps(runtime,indent=2))
    peer=Peer(rank,r['master_addr'],r['port'])
    plan=dict(version=1,backend=backend,job=r['job'],rounds=r['rounds'],warmups=r['warmups'],samples=r['samples'],
        bytes=459264,notify_add=32,pairs_per_iteration=4,active_threads=1,shapes=SHAPES,source_id=r['source_id'])
    # JSON transport converts tuples to lists.
    plan=json.loads(json.dumps(plan));peer.agree('plan',plan);(case/'plan.json').write_text(json.dumps(plan,indent=2))
    completed=0
    for repeat in range(1,r['rounds']+1):
        points=[(name,lanes,limit,sender) for name,lanes,limit in SHAPES for sender in (0,1)]
        if repeat%2==0:points.reverse()
        for name,lanes,limit,sender in points:
            label=name+'_%dto%d'%(sender,1-sender);send=rank==sender
            peer.agree('prepare',dict(repeat=repeat,label=label))
            session=ep.UrmaMicroSession(0,1,459264,True,lanes)
            path=json.loads(session.metadata());append('paths.jsonl',dict(repeat=repeat,label=label,path=path))
            if path.get('channels')!=lanes or path['control_memory']!='gpu' or path['write_order']!='relax' or path['atomic_order']!='strong' or path['atomic_fence']!='per_request' or path['normal_completion']!='all':raise ValueError('changed load path/order')
            remote=peer.exchange('descriptors',[bytes(b).hex() for b in session.descriptors(1)])
            session.connect([bytes.fromhex(b) for b in remote])
            session.load_prepare(limit,rank);session.experiment(send,not send,False,[placement['cpu']])
            for seq in range(r['warmups']+r['samples']):
                peer.agree('ready',dict(repeat=repeat,label=label,seq=seq))
                sample=json.loads(session.load_step(seq,limit))
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
