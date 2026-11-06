import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from phase_runtime import Measurements
from summarize_dispatch_trial import summarize
from summarize_normal_ab import LABELS


class DispatchTrialTests(unittest.TestCase):
    def test_real_measurement_adapter_includes_delay_and_disables_for_combine(self):
        with tempfile.TemporaryDirectory() as tmp:
            clock=[0];pending=[0]
            class Event:
                def __init__(self,**kwargs):pass
                def record(self):self.at=clock[0]
                def elapsed_time(self,other):return (other.at-self.at)/1000
            def begin(us,detailed):pending[0]=us
            def end():pending[0]=0;return '{"submits":[],"first_payload":[]}'
            ep=SimpleNamespace(urma_phase_snapshot=lambda:'{"records":[]}',urma_phase_enable=Mock(),
                urma_dispatch_trial_begin=Mock(side_effect=begin),urma_dispatch_trial_end=Mock(side_effect=end))
            torch=SimpleNamespace(cuda=SimpleNamespace(synchronize=Mock(),Event=Event))
            dist=SimpleNamespace(barrier=Mock());m=Measurements(torch,dist,ep,Path(tmp),'normal',True,2,3,False,True,200)
            fn=lambda:clock.__setitem__(0,clock[0]+100+pending[0])
            result=m.api(fn,'dispatch_fp8_chunk32');self.assertAlmostEqual(result[0],.0003)
            self.assertEqual(ep.urma_dispatch_trial_begin.call_count,5)
            self.assertEqual([c.args for c in ep.urma_dispatch_trial_begin.call_args_list],[(200,False),(200,False),(200,True),(200,False),(200,False)])
            result=m.api(fn,'combine_chunk16');self.assertAlmostEqual(result[0],.0001)
            self.assertEqual(ep.urma_dispatch_trial_begin.call_count,5)
            self.assertEqual(ep.urma_dispatch_trial_end.call_count,5)
            trace=[json.loads(s) for s in (Path(tmp)/'dispatch_trial_trace.jsonl').read_text().splitlines()]
            self.assertEqual(len(trace),1);self.assertFalse(trace[0]['used']);self.assertEqual(trace[0]['index'],0)
            def fail():raise RuntimeError('GPU failure')
            with self.assertRaises(RuntimeError):m.api(fail,'dispatch_bf16_chunk32')
            self.assertEqual(pending[0],0)

    def fixture(self,root):
        for tag,on in [('diagnostic',True),('r01',False),('r02',False),('r03',False)]:
            for delay in (0,200):
                for rank in (0,1):
                    job=f'trial_{tag}_d{delay}';node=root/job/f'node{rank}';case=node/'normal';case.mkdir(parents=True)
                    n=5 if on else 50
                    c=dict(job=job,dispatch_trial=True,dispatch_delay_us=delay,delay_node=1,mode='normal',backend='urma',api_only=True,normal_completion='all',stage_profile=on,capture_layout=on,samples=n,warmups=5 if on else 20,kernel='k',build_hash='b',diagnostics_hash='d',libraries={},torch='t',shape=[4096,128,7168,256,8])
                    (node/'setup.json').write_text(json.dumps(dict(error=None,fingerprint=c)))
                    (node/'host.json').write_text(json.dumps(dict(boot_id=f'boot{rank}',parent_affinity=[0,1])))
                    (case/'status.json').write_text('{"rc":0,"checks":{"done":true}}')
                    raw=[];api=[];trace=[]
                    for label in sorted(LABELS):
                        for i in range(n):
                            local=delay if rank==1 and label.startswith('dispatch_') else 0
                            trial=None
                            if label.startswith('dispatch_'):
                                first=[dict(proxy=p,ready_ns=1100,released_ns=1100+local*1000) for p in range(4)]
                                submits=[dict(proxy=p,lane=1,ns=1200+local*1000,bytes=8192,writes=1,fadds=1) for p in range(4)] if on and i==0 else []
                                trial=dict(version=1,delay_us=local,origin_ns=1000,dropped=0,first_payload=first,submits=submits)
                            us=(1000 if rank==0 else 1200) if not delay else (900 if rank==0 else 1500)
                            row=dict(label=label,mode='normal',index=i,used=i>0,us=us,host_us=us+400,requested_delay_us=local,trial=trial)
                            raw.append(row);api.append({k:row[k] for k in ('label','mode','index','used','us')})
                            if trial and on and i==0:trace.append(row)
                    for name,data in [('dispatch_trial_samples.jsonl',raw),('api_samples.jsonl',api),('dispatch_trial_trace.jsonl',trace)]:
                        (case/name).write_text('\n'.join(json.dumps(r) for r in data))

    def test_summary_checks_both_nodes_and_does_not_hide_slower_peer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.fixture(root)
            runs,comparison,diag=summarize(root,root/'out')
            self.assertEqual((len(runs),len(comparison),len(diag)),(30,5,8))
            self.assertEqual(comparison[0]['change_pct'],25)
            self.assertLess(comparison[0]['delay_node0_us'],comparison[0]['baseline_node0_us'])
            path=root/'trial_r01_d200/node1/normal/dispatch_trial_samples.jsonl';raw=[json.loads(s) for s in path.read_text().splitlines()]
            next(r for r in raw if r['trial'])['trial']['first_payload'][0]['released_ns']=1100
            path.write_text('\n'.join(json.dumps(r) for r in raw))
            with self.assertRaisesRegex(ValueError,'actual injected delay'):summarize(root,root/'out')


if __name__=='__main__':unittest.main()
