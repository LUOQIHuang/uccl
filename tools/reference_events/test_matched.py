from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

REPO=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(REPO/'test/reference_events'))
import test_reference as fixtures
spec=importlib.util.spec_from_file_location('matched_summary',Path(__file__).with_name('summarize_matched.py'))
matched=importlib.util.module_from_spec(spec);spec.loader.exec_module(matched)


class MatchedTests(unittest.TestCase):
    def test_valid_pair_and_changed_identity_rejection(self):
        with tempfile.TemporaryDirectory() as tmp,redirect_stdout(io.StringIO()):
            for backend in ('urma','verbs'):
                root=Path(tmp)/backend;_,template,_=fixtures.fixtures.DispatchTests().fixture(root,backend)
                for mode in ('paced','on'):
                    shutil.copytree(template,root/mode)
                    for rank in (0,1):
                        node=root/mode/f'node{rank}';case=node/('normal' if backend=='urma' else 'r01_baseline/normal')
                        d=json.loads((node/'setup.json').read_text());d['fingerprint'].update(gpu_stages=False,reference_events=mode);fixtures.dump(node/'setup.json',d)
                        d=json.loads((case/'runtime.json').read_text());d.update(gpu_stage_version=0,reference_events_version=1);fixtures.dump(case/'runtime.json',d)
                        manifest=node/('build_manifest.json' if backend=='urma' else 'module_manifest.json')
                        d=json.loads(manifest.read_text());d['flags'].update(UCCL_GPU_STAGE_TRACE='0',UCCL_REFERENCE_EVENTS='1');fixtures.dump(manifest,d)
                        (case/'gpu_stages.jsonl').unlink()
                        status=json.loads((case/'status.json').read_text())
                        if mode=='paced':
                            status['checks']['reference_control']=True
                            for label in fixtures.LABELS:fixtures.control_marker(case,label,mode,5,10)
                        else:
                            status['checks'].update(reference_events=True,reference_correctness=True)
                            fixtures.dump(case/'reference_correctness.json',dict(fixtures.window('dispatch_fp8_chunk32')['gpu'],verified=True))
                            fixtures.lines(case/'event_trace.jsonl',[fixtures.window(l,i) for l in fixtures.LABELS for i in range(10)])
                        fixtures.dump(case/'status.json',status)
                result=matched.summarize(root/'paced',root/'on',root/'summary')
                self.assertEqual(len(result),10);self.assertTrue(all(r['representative'] for r in result))
                host=root/'on/node1/host.json';data=json.loads(host.read_text());data['boot_id']='different';fixtures.dump(host,data)
                with self.assertRaisesRegex(ValueError,'boot or module changed'):matched.summarize(root/'paced',root/'on',root/'invalid')


if __name__=='__main__':unittest.main()
