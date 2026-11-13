from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch
import collect_fence_failure as capture


class FailureCaptureTests(unittest.TestCase):
    def test_missing_trace_and_console_only_failure_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'job_all_on_paced/node0').mkdir(parents=True)
            (root/'job_all_on_trace/console/node0').mkdir(parents=True)
            self.assertEqual([s for s,_ in capture.existing_cases(root,'job',0)],['all_on_paced','all_on_trace'])
            self.assertEqual(capture.existing_cases(root,'job',1),[])

    def test_unavailable_diagnostic_does_not_hide_collected_logs(self):
        with patch.object(capture.subprocess,'run',side_effect=FileNotFoundError('not installed')):
            result=capture.snapshot(['coredumpctl','info'])
            self.assertIn('not installed',result['error'])

    def test_collect_console_only_trace_without_benchmark_or_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'root';destination=Path(directory)/'dest'
            console=root/'job_all_on_trace/console/node0';console.mkdir(parents=True)
            (console/'run.log').write_text('trace failed before status writer')
            with patch.object(capture,'snapshot',side_effect=lambda cmd:dict(command=cmd,error='fixture')) as probe:
                capture.collect(root,'job',0,Path(directory)/'build',destination,'yesterday','today')
            self.assertEqual((destination/'job_all_on_trace/console/node0/run.log').read_text(),'trace failed before status writer')
            data=json.loads(next((destination/'job_failure_capture').rglob('capture.json')).read_text())
            self.assertEqual(data['collected'],['all_on_trace'])
            self.assertIn('peer_off_trace',data['missing'])
            self.assertEqual({args[0][0] for args,_ in probe.call_args_list},{'uname','mx-smi','ps','fuser','journalctl','coredumpctl'})


if __name__=='__main__':unittest.main()
