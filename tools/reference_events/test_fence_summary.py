from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from test_matched import fixtures
import summarize_fadd_fence as summary


class FaddFenceSummaryTests(unittest.TestCase):
    def test_fence_intervention_is_not_confused_with_window_or_ignored_setting(self):
        with tempfile.TemporaryDirectory() as temporary,redirect_stdout(io.StringIO()):
            root=Path(temporary);_,template,_=fixtures.fixtures.DispatchTests().fixture(root/'template','urma')
            for repeat in (1,2):
                for plan,option in [('all_on','all-on'),('peer_off','peer-off')]:
                    target=root/('ab_r%02d_%s'%(repeat,plan));shutil.copytree(template,target)
                    for rank in (0,1):
                        node=target/f'node{rank}';case=node/'normal'
                        d=json.loads((node/'setup.json').read_text());d['fingerprint'].update(job=target.name,gpu_stages=False,reference_events='paced',normal_fadd_fence_plan=option);fixtures.dump(node/'setup.json',d)
                        d=json.loads((case/'runtime.json').read_text());d.update(gpu_stage_version=0,reference_events_version=1,normal_fadd_fence_version=1,normal_fadd_fence='off' if plan=='peer_off' and rank==1 else 'on');fixtures.dump(case/'runtime.json',d)
                        d=json.loads((node/'build_manifest.json').read_text());d['flags'].update(UCCL_GPU_STAGE_TRACE='0',UCCL_REFERENCE_EVENTS='1');fixtures.dump(node/'build_manifest.json',d)
                        (case/'gpu_stages.jsonl').unlink()
                        d=json.loads((case/'status.json').read_text());d['checks'].update(reference_control=True,normal_fadd_fence=True);fixtures.dump(case/'status.json',d)
                        for label in fixtures.LABELS:fixtures.control_marker(case,label,'paced',5,10)
            result=summary.summarize(root,'ab',root/'summary')
            self.assertTrue(all(r['decision']=='no_material_change_keep_default' for r in result))
            path=root/'ab_r02_peer_off/node1/normal/runtime.json';d=json.loads(path.read_text());d['normal_fadd_fence']='on';fixtures.dump(path,d)
            with self.assertRaisesRegex(ValueError,'unverified Normal FADD fence'):summary.summarize(root,'ab',root/'bad')


if __name__=='__main__':unittest.main()
