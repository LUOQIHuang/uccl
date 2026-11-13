from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch
import collect_fence_failure as capture


class FailureCaptureTests(unittest.TestCase):
    def test_core_info_requires_recorded_test_pid_and_boot(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);node=root/'job_all_on_post/node0';(node/'normal').mkdir(parents=True)
            (node/'host.json').write_text(json.dumps(dict(boot_id='12345678-abcd-1234-abcd-123456789abc')))
            (node/'normal/bench.log').write_text('failed (exitcode: -6) local_rank: 0 (pid: 243526)')
            self.assertEqual(capture.crash_matches(root,'job',0),[('12345678abcd1234abcd123456789abc','243526')])
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
            commands=[args[0] for args,_ in probe.call_args_list]
            self.assertFalse(any(cmd[0]=='journalctl' and '-k' in cmd for cmd in commands))
            self.assertFalse(any(cmd[0]=='coredumpctl' and 'info' in cmd for cmd in commands))

    def test_conflicting_source_is_archived_without_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'root';destination=Path(directory)/'dest'
            path=root/'job_peer_off_paced/node0/normal/preflight_error.txt';path.parent.mkdir(parents=True);path.write_text('new failure')
            old=destination/path.relative_to(root);old.parent.mkdir(parents=True);old.write_text('old failure')
            with patch.object(capture,'snapshot',return_value={}):capture.collect(root,'job',0,Path(directory)/'build',destination,'yesterday','today')
            self.assertEqual(old.read_text(),'old failure')
            saved=list((destination/'job_failure_capture').rglob('conflicting_source/**/preflight_error.txt'))
            self.assertEqual(len(saved),1);self.assertEqual(saved[0].read_text(),'new failure')


if __name__=='__main__':unittest.main()
