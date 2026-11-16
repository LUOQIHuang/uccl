from contextlib import redirect_stdout
import io,json,shutil,tempfile,unittest
from pathlib import Path
from test_matched import fixtures
import summarize_retire as summary


class RetireSummaryTests(unittest.TestCase):
    def test_policy_intervention_and_fence_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary,redirect_stdout(io.StringIO()):
            root=Path(temporary);_,template,_=fixtures.fixtures.DispatchTests().fixture(root/'template','urma')
            for repeat in (1,2):
                for plan,option in [('ordered','ordered'),('ready','peer-ready')]:
                    target=root/('ab_r%02d_%s'%(repeat,plan));shutil.copytree(template,target)
                    for rank in (0,1):
                        node=target/('node%d'%rank);case=node/'normal'
                        d=json.loads((node/'setup.json').read_text());d['fingerprint'].update(job=target.name,gpu_stages=False,reference_events='paced',normal_fadd_fence_plan='all-on',normal_retire_plan=option);fixtures.dump(node/'setup.json',d)
                        d=json.loads((case/'runtime.json').read_text());d.update(gpu_stage_version=0,reference_events_version=1,normal_fifo_retire_version=1,normal_fifo_retire='ready' if rank and plan=='ready' else 'ordered',normal_fadd_fence_version=1,normal_fadd_fence='on');fixtures.dump(case/'runtime.json',d)
                        d=json.loads((node/'build_manifest.json').read_text());d['flags'].update(UCCL_GPU_STAGE_TRACE='0',UCCL_REFERENCE_EVENTS='1');fixtures.dump(node/'build_manifest.json',d)
                        (case/'gpu_stages.jsonl').unlink()
                        d=json.loads((case/'status.json').read_text());d['checks'].update(reference_control=True,normal_fifo_retire=True,normal_fadd_fence=True);fixtures.dump(case/'status.json',d)
                        for label in fixtures.LABELS:fixtures.control_marker(case,label,'paced',5,10)
            result=summary.summarize(root,'ab',root/'summary');self.assertTrue(all(r['decision']=='no_material_change_keep_default' for r in result))
            setup=root/'ab_r01_ready/node0/setup.json';original=setup.read_text();d=json.loads(original);d['fingerprint']['diagnostics_hash']='changed';fixtures.dump(setup,d)
            with self.assertRaisesRegex(ValueError,'module/source/boot/runtime'):summary.summarize(root,'ab',root/'strict_reject')
            review=summary.summarize(root,'ab',root/'limited_review',allow_diagnostic_drift=True)
            self.assertTrue(all(r['decision']=='diagnostic_drift_limited_keep_default' for r in review))
            setup.write_text(original)
            path=root/'ab_r02_ready/node1/normal/runtime.json';d=json.loads(path.read_text());d['normal_fifo_retire']='ordered';fixtures.dump(path,d)
            with self.assertRaisesRegex(ValueError,'FIFO retire'):summary.summarize(root,'ab',root/'bad')
