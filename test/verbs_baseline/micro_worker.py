"""Matched WRITE-only protocol; TCP and GPU validation are outside native timing."""
import faulthandler
import json
import os
from pathlib import Path
import subprocess
import time
import common
from micro_support import Peer,select_geometries
from split_support import roles,points
faulthandler.enable()
case=Path(os.environ['VM_CASE']);r=json.loads((case/'request.json').read_text());rank=r['rank']
ep,runtime=common.load_extension(Path(r['module']))
if runtime['module_sha']!=r['module_sha']:raise RuntimeError('micro module SHA changed')
os.sched_setaffinity(0,{r['placement']['controller']});peer=Peer(rank,r['master_addr'],r['port']);session=None
def append(name,row):
    with (case/name).open('a') as f:f.write(json.dumps(row)+'\n')
try:
    peer.agree('fingerprint',r['fingerprint'])
    g=select_geometries(r['layout']) if rank==0 else None;other=peer.exchange('geometry',g)
    if rank==1:g=other
    plan=[p for p in points(g,r['threads']) if '_write_' in p['label']]
    (case/'plan.json').write_text(json.dumps(dict(geometries=g,points=plan),indent=2))
    placement=peer.exchange('placement',r['placement'])
    (case/'affinity_pair.json').write_text(json.dumps({str(rank):r['placement'],str(1-rank):placement},indent=2))
    session=ep.VerbsWriteSession(r['hca'],r['gid_index'],r['threads'],max(x['bytes'] for x in g))
    (case/'verbs_path.json').write_text(session.metadata())
    descriptors=[bytes(session.descriptor(i)).hex() for i in range(r['threads'])]
    remote=peer.exchange('descriptors',descriptors);session.connect([bytes.fromhex(b) for b in remote])
    (case/'descriptors.json').write_text(json.dumps(dict(local=descriptors,remote=remote)))
    for p in plan:
        label=p['label'];g=p['geometry'];send,receive=roles(p,rank)
        peer.agree('prepare',label);session.prepare(g['bytes'],g['src_mod'],g['dst_mod'],rank)
        session.experiment(send,receive,r['placement']['workers'][:r['threads']]);raw=[]
        for seq in range(r['warmups']+r['samples']):
            peer.agree('ready',dict(label=label,seq=seq));sample=json.loads(session.step(seq))
            peer.agree('done',dict(label=label,seq=seq));error=None
            try:session.validate(seq)
            except Exception as exc:error=str(exc)
            remote_error=peer.exchange('validate',error)
            if error or remote_error:raise RuntimeError('GPU payload check failed: '+str([error,remote_error]))
            if seq>=r['warmups']:
                index=seq-r['warmups'];sample.update(label=label,index=index,used=index>0,us=sample['span_ns']/1000,
                    bytes=g['bytes'],src_mod=g['src_mod'],dst_mod=g['dst_mod'],threads=r['threads'],direction=p['direction'],send=send,receive=receive)
                append('micro_samples.jsonl',sample);raw.append(sample)
        summary=dict(label=label,validation=True,send=send,receive=receive,direction=p['direction'],bytes=g['bytes'],
            src_mod=g['src_mod'],dst_mod=g['dst_mod'],threads=r['threads'],warmups=r['warmups'],samples=r['samples'],
            **common.phase_support.distribution([x['us'] for x in raw if x['used']]))
        summary['tx_GBps']=128*g['bytes']/summary['mean_us']/1000 if send else None
        append('micro_summary.jsonl',summary);print('VERBS_MICRO_POINT '+json.dumps(summary),flush=True)
    begin=time.time_ns();snapshot=subprocess.run(['journalctl','-b','-k','-n','400','--no-pager'],capture_output=True,text=True,timeout=20)
    (case/'kernel_after_measurements.txt').write_text(snapshot.stdout+snapshot.stderr)
    (case/'measurement_boundary.json').write_text(json.dumps(dict(begin_realtime_ns=begin,end_realtime_ns=time.time_ns(),rc=snapshot.returncode)))
    peer.agree('teardown',0);session.close();session=None;peer.agree('exit',0)
    print('VERBS_MICRO_ALL_PASSED',flush=True)
finally:
    if session is not None:session.close()
    peer.close()
