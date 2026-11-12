from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from test_matched import fixtures
import summarize_window as window
from audit_window import requests


class WindowTests(unittest.TestCase):
    def test_pairs_and_wrong_node_window_rejected(self):
        with tempfile.TemporaryDirectory() as tmp,redirect_stdout(io.StringIO()):
            for backend in ('urma','verbs'):
                root=Path(tmp)/backend;_,template,_=fixtures.fixtures.DispatchTests().fixture(root/'template',backend)
                for repeat in (1,2):
                    for plan in ('default','peer4'):
                        job=root/('ab_r%02d_%s'%(repeat,plan));shutil.copytree(template,job)
                        for rank in (0,1):
                            node=job/f'node{rank}';case=node/('normal' if backend=='urma' else 'r01_baseline/normal')
                            d=json.loads((node/'setup.json').read_text());d['fingerprint'].update(job=job.name,gpu_stages=False,reference_events='paced',normal_window_plan=plan);fixtures.dump(node/'setup.json',d)
                            d=json.loads((case/'runtime.json').read_text());d.update(gpu_stage_version=0,reference_events_version=1,normal_max_inflight=4 if plan=='peer4' and rank==1 else 8);fixtures.dump(case/'runtime.json',d)
                            manifest=node/('build_manifest.json' if backend=='urma' else 'module_manifest.json');d=json.loads(manifest.read_text());d['flags'].update(UCCL_GPU_STAGE_TRACE='0',UCCL_REFERENCE_EVENTS='1');fixtures.dump(manifest,d)
                            (case/'gpu_stages.jsonl').unlink()
                            d=json.loads((case/'status.json').read_text());d['checks']['reference_control']=True;fixtures.dump(case/'status.json',d)
                            for label in fixtures.LABELS:fixtures.control_marker(case,label,'paced',5,10)
                result=window.summarize(root,'ab',root/'summary')
                self.assertTrue(all(r['decision']=='no_material_change_keep_default' for r in result))
                path=root/'ab_r02_peer4/node0'/('normal/runtime.json' if backend=='urma' else 'r01_baseline/normal/runtime.json')
                d=json.loads(path.read_text());d['normal_max_inflight']=4;fixtures.dump(path,d)
                with self.assertRaisesRegex(ValueError,'window runtime mismatch'):window.summarize(root,'ab',root/'bad')

    def test_pending_reconstruction_uses_same_proxy_ring_and_observed_completions(self):
        events=[]
        for wid,base,size in [(1,100,8),(2,200,8192)]:
            for stage,offset in [('poll',0),('post_begin',10),('post_end',20),('cqe',300),('ack_slot',350)]:
                events.append(dict(proxy=0,wr_id=wid,stage=stage,ns=base+offset,value=0))
            events.append(dict(proxy=0,wr_id=wid,stage='payload_bytes',ns=base+5,value=size))
        result=requests(dict(index=1,cpu=dict(events=events)))
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['prior_observed_pending'],1)
        self.assertEqual(result[0]['payload_position'],1)
        self.assertAlmostEqual(result[0]['post_to_cqe_us'],.28)


if __name__=='__main__':unittest.main()
