from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest

import summarize_submit as submit
from test_matched import fixtures


class SubmitDecisionTests(unittest.TestCase):
    def test_predeclared_decisions(self):
        self.assertEqual(submit.decision([-8,-6],True),'material_improvement')
        self.assertEqual(submit.decision([1,-1],True),'no_material_change_keep_default')
        self.assertEqual(submit.decision([8,6],True),'material_regression_keep_default')
        self.assertEqual(submit.decision([-8,1],True),'threshold_not_met_keep_default')
        self.assertEqual(submit.decision([-8,-6],False),'unstable_keep_default')

    def test_complete_pairs_and_mismatched_provenance_rejection(self):
        with tempfile.TemporaryDirectory() as temporary,redirect_stdout(io.StringIO()):
            root=Path(temporary)
            _,template,_=fixtures.fixtures.DispatchTests().fixture(root/'template','urma')
            for repeat in (1,2):
                for schedule in ('lane-mixed','writes-first'):
                    job=root/('ab_r%02d_%s'%(repeat,schedule.replace('-','_')))
                    shutil.copytree(template,job)
                    for rank in (0,1):
                        node=job/f'node{rank}';case=node/'normal'
                        data=json.loads((node/'setup.json').read_text());data['fingerprint'].update(job=job.name,gpu_stages=False,reference_events='paced',normal_submit_schedule=schedule);fixtures.dump(node/'setup.json',data)
                        data=json.loads((case/'runtime.json').read_text());data.update(gpu_stage_version=0,reference_events_version=1,normal_submit_schedule=schedule);fixtures.dump(case/'runtime.json',data)
                        data=json.loads((node/'build_manifest.json').read_text());data['flags'].update(UCCL_GPU_STAGE_TRACE='0',UCCL_REFERENCE_EVENTS='1');fixtures.dump(node/'build_manifest.json',data)
                        (case/'gpu_stages.jsonl').unlink()
                        data=json.loads((case/'status.json').read_text());data['checks'].update(reference_control=True,normal_submit_schedule=True);fixtures.dump(case/'status.json',data)
                        for label in fixtures.LABELS:fixtures.control_marker(case,label,'paced',5,10)
                        raw=[json.loads(line) for line in (case/'api_samples.jsonl').read_text().splitlines()]
                        value=(100 if rank==0 else 200)*(1 if schedule=='lane-mixed' else .9)
                        for row in raw:row['us']=value
                        fixtures.lines(case/'api_samples.jsonl',raw)
                        saved=[json.loads(line) for line in (case/'sample_summary.jsonl').read_text().splitlines()]
                        for row in saved:row['mean_us']=value
                        fixtures.lines(case/'sample_summary.jsonl',saved)
            result=submit.summarize(root,'ab',root/'summary')
            self.assertTrue(all(row['decision']=='material_improvement' for row in result))
            self.assertAlmostEqual(result[0]['r01_change_pct'],-10)
            host=root/'ab_r02_writes_first/node1/host.json'
            data=json.loads(host.read_text());data['boot_id']='changed';fixtures.dump(host,data)
            with self.assertRaisesRegex(ValueError,'changed source, module'):
                submit.summarize(root,'ab',root/'bad')


if __name__=='__main__':unittest.main()
