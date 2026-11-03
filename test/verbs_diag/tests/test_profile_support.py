"""CPU-only validation of focused source changes and perf lifecycle."""
import ast
import json
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock

DIAG=Path(__file__).resolve().parents[1]
REPO=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(DIAG))
import profile_support as support
import collect_results

class ProfileTests(unittest.TestCase):
    def test_focused_changes_only_four_tuning_ranges(self):
        source=(REPO/'ep/bench/test_internode.py').read_text(encoding='utf-8')
        expected=ast.parse(source)
        replacements={'range(4, 45, 4)':(4,), 'range(1, 8, 1)':(4,), 'range(4, 33, 4)':(32,), 'range(12 if num_nodes == 2 else 8, 33, 4)':(16,)}
        counts={key:0 for key in replacements}
        for node in ast.walk(expected):
            if isinstance(node,ast.For):
                key=ast.unparse(node.iter)
                if key in replacements:
                    node.iter=ast.Tuple(elts=[ast.Constant(value=value) for value in replacements[key]],ctx=ast.Load())
                    counts[key]+=1
        self.assertTrue(all(count==1 for count in counts.values()))
        self.assertEqual(ast.dump(expected),ast.dump(ast.parse(support.prepare_benchmark(source,'focused'))))
        self.assertEqual(support.plan('focused'),[('dispatch_fp8',32),('dispatch_bf16',32),('combine',16)])
        self.assertEqual(len(support.plan('scan')),22)
        with self.assertRaises(ValueError): support.prepare_benchmark('changed source','focused')

    def test_missing_perf_and_empty_report_rejected(self):
        with patch.object(support.shutil,'which',return_value=None):
            with self.assertRaisesRegex(RuntimeError,'PERF_MISSING'): support.capability_check('none')
        self.assertFalse(support.valid_report(SimpleNamespace(returncode=0,stdout='# zero samples')))
        self.assertFalse(support.valid_report(SimpleNamespace(returncode=1,stdout='  80.00% lib urma_poll')))

    def test_targets_only_proxy_tids_and_stops_own_recorder(self):
        with tempfile.TemporaryDirectory() as temporary:
            profiler=support.ProxyProfile(temporary,[112,111,112],'dwarf')
            process=Mock(pid=900,returncode=0)
            process.poll.return_value=None
            report=SimpleNamespace(returncode=0,stdout='  80.00% liburma.so [.] urma_poll_jfc\n',stderr='')
            with patch.object(support.subprocess,'Popen',return_value=process) as popen, patch.object(support.subprocess,'run',return_value=report), patch.object(support.time,'sleep'):
                profiler.start()
                profiler.stop()
            process.send_signal.assert_called_once_with(signal.SIGINT)
            self.assertNotIn('start_new_session',popen.call_args.kwargs)
            self.assertNotIn('sleep',profiler.command)
            self.assertEqual(profiler.command[profiler.command.index('-t')+1],'111,112')
            self.assertIn('--no-inherit',profiler.command)
            status=json.loads((Path(temporary)/'perf.status.json').read_text())
            self.assertTrue(status['ok'])
            self.assertEqual(status['phase'],'dispatch_bf16')
            self.assertTrue((Path(temporary)/'perf.callgraph.txt').is_file())

    def test_early_perf_exit_rejected_even_with_nonempty_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            profiler=support.ProxyProfile(temporary,[111],'none')
            profiler.process=Mock(returncode=1)
            profiler.process.poll.return_value=1
            profiler.stream=Mock()
            profiler.command=[]
            with patch.object(support.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='  80.00% liburma.so poll',stderr='')):
                with self.assertRaisesRegex(RuntimeError,'PERF_REPORT_EMPTY_OR_FAILED'): profiler.stop()
            self.assertFalse(json.loads((Path(temporary)/'perf.status.json').read_text())['ok'])

    def test_collector_includes_reports_excludes_binary(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            source=root/'source'
            case=source/'r1_unpinned'
            case.mkdir(parents=True)
            for name in ('perf.report.txt','perf.callgraph.txt','perf.status.json','perf.stderr.log','perf.data'):
                (case/name).write_text('data')
            destination=root/'destination'
            self.assertEqual(collect_results.collect(source,destination),4)
            self.assertFalse((destination/'r1_unpinned/perf.data').exists())

    def test_focused_single_affinity_summary_and_incomplete_case_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            node=root/'node0'
            case=node/'r1_unpinned'
            case.mkdir(parents=True)
            (node/'setup.json').write_text(json.dumps(dict(fingerprint=dict(suite='focused',affinity_mode='unpinned'))))
            (case/'status.json').write_text(json.dumps(dict(rc=0,checks=dict(metrics=True))))
            rows=[dict(op=op,rdma=chunk,transmit_us=100,notify_us=10,api_cuda_event_us=115,cpu_profile=op=='dispatch_bf16',profiler_num_tests=200 if op=='dispatch_bf16' else 30) for op,chunk in support.plan('focused')]
            metrics=case/'metrics.jsonl'
            metrics.write_text('\n'.join(map(json.dumps,rows)))
            command=[sys.executable,str(DIAG/'summarize.py'),str(node)]
            completed=subprocess.run(command,capture_output=True,text=True)
            self.assertEqual(completed.returncode,0,completed.stderr)
            self.assertEqual(len((node/'summary.csv').read_text().splitlines()),4)
            metrics.write_text('\n'.join(map(json.dumps,rows[:2])))
            completed=subprocess.run(command,capture_output=True,text=True)
            self.assertNotEqual(completed.returncode,0)
            self.assertIn('incomplete/duplicate metrics',completed.stderr)

if __name__=='__main__': unittest.main()
