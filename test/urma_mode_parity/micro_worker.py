"""Real production transport microbench; CPU TCP is outside timed intervals."""
import faulthandler
import json
import os
from pathlib import Path
import subprocess
import time
import support
from micro_support import Peer,select_geometries,points
from phase_support import distribution

faulthandler.enable()
case=Path(os.environ['MICRO_CASE']);request=json.loads((case/'request.json').read_text())
ep,runtime=support.load_extension(Path(request['module']))
if runtime['module_sha']!=request['module_sha']:raise RuntimeError('micro module changed')
ep.urma_phase_enable(False)
(case/'runtime.json').write_text(json.dumps(runtime,indent=2))
rank=request['rank'];peer=Peer(rank,request['master_addr'],request['port'])
session=None


def append(name,row):
    with (case/name).open('a') as stream:stream.write(json.dumps(row)+'\n')


try:
    peer.agree('fingerprint',request['fingerprint'])
    geometries=select_geometries(request['layout']) if rank==0 else None
    remote=peer.exchange('plan',geometries)
    if rank==1:geometries=remote
    plan=points(geometries)
    (case/'plan.json').write_text(json.dumps(dict(geometries=geometries,points=plan,
        geometry_source='node0 actual Normal WRITE capture; not full trace replay'),indent=2))
    session=ep.UrmaMicroSession(0,request['threads'],max(g['bytes'] for g in geometries))
    descriptors=[bytes(b).hex() for b in session.descriptors(request['threads'])]
    remote=peer.exchange('descriptors',descriptors)
    session.connect([bytes.fromhex(b) for b in remote])
    for point in plan:
        label=point['label'];g=point['geometry']
        peer.agree('prepare_'+label,dict(label=label))
        session.prepare(g['bytes'],g['src_mod'],g['dst_mod'],point['window'],rank)
        raw=[]
        for sequence in range(request['warmups']+request['samples']):
            session.arm(sequence,point['visible'],point['fadd'],point['messages'])
            # Both target buffers/observers are ready; previous iteration
            # consumption finishes on both nodes before any slot is reused.
            peer.agree(f'ready_{label}_{sequence}',0)
            # Validate imported MVA alignment too, outside recorded samples.
            if sequence==0:ep.urma_layout_begin()
            try:sample=json.loads(session.step(sequence,point['fadd'],point['messages']))
            finally:
                if sequence==0:observed=json.loads(ep.urma_layout_end())
            if sequence==0:
                append('micro_layout_verified.jsonl',dict(label=label,**observed))
                if (observed['dropped'] or observed['modulus']!=4096 or
                    sum(r['count'] for r in observed['writes'])!=request['threads']*point['messages'] or
                    any((r['bytes'],r['src_mod'],r['dst_mod'])!=(g['bytes'],g['src_mod'],g['dst_mod']) for r in observed['writes'])):
                    raise RuntimeError('micro actual WRITE/MVA geometry differs from selected Normal geometry')
            peer.agree(f'done_{label}_{sequence}',0)
            if sequence>=request['warmups']:
                index=sequence-request['warmups'];sample.update(label=label,index=index,used=index>0,
                    us=sample['span_ns']/1000,bytes=g['bytes'],window=point['window'],messages=point['messages'],
                    fadd=point['fadd'],gpu_observed=point['visible'])
                append('micro_samples.jsonl',sample);raw.append(sample)
        error=None
        try:session.validate(sequence,point['fadd'],point['messages'])
        except Exception as exc:error=str(exc)
        both=peer.exchange('validate_'+label,error)
        if error or both:raise RuntimeError('micro payload/control failure: '+str([error,both]))
        stats=distribution([s['us'] for s in raw if s['used']])
        payload=request['threads']*point['messages']*g['bytes']
        summary=dict(label=label,bytes=g['bytes'],src_mod=g['src_mod'],dst_mod=g['dst_mod'],
            window=point['window'],messages=point['messages'],threads=request['threads'],fadd=point['fadd'],
            gpu_observed=point['visible'],warmups=request['warmups'],samples=request['samples'],
            tx_bytes_per_step=payload,tx_GBps_at_mean=payload/stats['mean_us']/1000,validation=True,**stats)
        append('micro_summary.jsonl',summary)
        print('URMA_MICRO_POINT '+json.dumps(summary),flush=True)
    before=time.time_ns()
    snapshot=subprocess.run(['journalctl','-b','-k','-n','400','--no-pager'],capture_output=True,text=True,timeout=20)
    (case/'kernel_after_measurements.txt').write_text(snapshot.stdout+snapshot.stderr)
    (case/'measurement_boundary.json').write_text(json.dumps(dict(begin_realtime_ns=before,end_realtime_ns=time.time_ns(),rc=snapshot.returncode)))
    peer.agree('before_teardown',0)
    session.close();session=None
    peer.agree('exit',0)
    print('URMA_MICRO_ALL_PASSED',flush=True)
finally:
    if session is not None:session.close()
    peer.close()
