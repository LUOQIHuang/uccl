"""One sender, one thread, one active data queue, at most one pair outstanding."""
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
try:
    if ep.get_serial_pair_version()!=1 or runtime['module_sha']!=r['module_sha']:raise RuntimeError('serial module missing or changed')
    placement=choose_cpu();(case/'affinity.json').write_text(json.dumps(placement,indent=2));(case/'runtime.json').write_text(json.dumps(runtime,indent=2))
    peer=Peer(rank,r['master_addr'],r['port'])
    plan=dict(version=1,backend=backend,job=r['job'],rounds=r['rounds'],warmups=r['warmups'],samples=r['samples'],
              bytes=459264,notify_add=32,active_threads=1,active_data_queues=1,max_inflight_pairs=1,source_id=r['source_id'])
    peer.agree('plan',plan);(case/'plan.json').write_text(json.dumps(plan,indent=2))
    session=ep.UrmaMicroSession(0,1,459264,True) if backend=='urma' else ep.VerbsWriteSession(r['hca'],r['gid_index'],1,459264,True,True)
    path=json.loads(session.metadata());(case/'path.json').write_text(json.dumps(path,indent=2))
    if not path.get('serial') or path['control_memory']!='gpu' or path.get('channels',path.get('lanes_per_thread'))!=1:raise ValueError('not one-lane GPU-control serial path')
    if backend=='urma':
        if path['write_order']!='relax' or path['atomic_order']!='strong' or path['atomic_fence']!='per_request':raise ValueError('changed URMA order')
        local=[bytes(b).hex() for b in session.descriptors(1)]
    else:
        if path['registration']!='dmabuf' or path['fadd_send_fence']:raise ValueError('changed verbs path')
        local=[bytes(session.descriptor(0)).hex()]
    remote=peer.exchange('descriptors',local);session.connect([bytes.fromhex(b) for b in remote])
    completed=0
    for repeat in range(1,r['rounds']+1):
        points=[(32,0),(64,0),(32,1),(64,1)]
        if repeat%2==0:points.reverse()
        for tokens,sender in points:
            label='t%d_%dto%d'%(tokens,sender,1-sender);pairs=tokens//32;send=rank==sender
            peer.agree('prepare',dict(repeat=repeat,label=label))
            session.serial_prepare(pairs,rank)
            if backend=='urma':session.experiment(send,not send,False,[placement['cpu']])
            else:session.experiment(send,not send,[placement['cpu']])
            for seq in range(r['warmups']+r['samples']):
                peer.agree('ready',dict(repeat=repeat,label=label,seq=seq))
                sample=json.loads(session.serial_step(seq,pairs))
                peer.agree('done',dict(repeat=repeat,label=label,seq=seq))
                error=None
                try:session.serial_validate(seq,pairs)
                except Exception as exc:error=str(exc)
                remote_error=peer.exchange('validate',error)
                if error or remote_error:raise RuntimeError('serial GPU validation failed: '+str([error,remote_error]))
                if seq>=r['warmups']:
                    index=seq-r['warmups'];append('samples.jsonl',dict(repeat=repeat,label=label,tokens=tokens,sender=sender,
                          index=index,used=index>0,rank=rank,validation=True,sample=sample))
            completed+=1;print('SERIAL_PAIR_POINT_PASSED '+label+' round='+str(repeat),flush=True)
    peer.agree('teardown',completed);session.close();session=None;peer.agree('closed',0)
    (case/'status.json').write_text(json.dumps(dict(passed=True,points=completed,rounds=r['rounds'],correctness=True)))
    print('SERIAL_PAIR_ALL_PASSED',flush=True)
except BaseException as exc:
    (case/'status.json').write_text(json.dumps(dict(passed=False,error=str(exc),traceback=traceback.format_exc()),indent=2));raise
finally:
    if session is not None:session.close()
    if peer is not None:peer.close()
