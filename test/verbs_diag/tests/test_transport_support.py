"""No GPU/NIC access. Native fake-provider test also runs on Linux."""
import ast
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE.parent))
import profile_support
import transport_support as support
import collect_results
import summarize_transport

def sample():
    fields=('post_calls post_errors poll_calls empty_polls poll_errors completed completion_errors attempted_wrs posted_wrs write_wrs fadd_wrs other_wrs write_bytes complete_enabled fence_enabled inflight peak_inflight unknown_completions map_overflow context_errors batch_max').split()
    thread={key:0 for key in fields}
    thread.update(tid=123,post_calls=2,poll_calls=3,empty_polls=1,completed=2,posted_wrs=2,write_wrs=1,fadd_wrs=1,write_bytes=4096,peak_inflight=2)
    thread['write_order']=[1]+[0]*15
    for key in ('post_write','post_fadd','post_other','poll','write_observed','fadd_observed'):
        thread[key]=dict(samples=1,total_ns=100,max_ns=100,log2_ns=[0]*64)
    return dict(thread_overflow=False,constants=dict(relax=0),threads=[thread],expected_proxy_tids=[123])

class TransportTests(unittest.TestCase):
    @unittest.skipIf(sys.version_info<(3,8),'baseline benchmark uses Python 3.8+ debug f-strings')
    def test_boundaries_plan_preserves_correctness_and_changes_only_ranges(self):
        source=(HERE.parents[2]/'ep/bench/test_internode.py').read_text(encoding='utf-8')
        expected=profile_support.prepare_benchmark(source,'focused').replace('for rdma_chunk_size in (16,):','for rdma_chunk_size in (12, 16):')
        actual=profile_support.prepare_benchmark(source,'boundaries')
        self.assertEqual(ast.dump(ast.parse(actual)),ast.dump(ast.parse(expected)))
        self.assertEqual(profile_support.plan('boundaries'),[('dispatch_fp8',32),('dispatch_bf16',32),('combine',12),('combine',16)])

    def test_coverage_errors_order_and_tid_are_validated(self):
        data=sample()
        self.assertEqual(support.validate(data,[123])['empty_poll_ratio'],1/3)
        for mutate in (lambda d:d.update(thread_overflow=True),lambda d:d['threads'][0].update(tid=999),lambda d:d['threads'][0].update(post_errors=1),lambda d:d['threads'][0].update(write_wrs=0),lambda d:d['threads'][0].update(write_order=[0,1]+[0]*14)):
            changed=copy.deepcopy(data); mutate(changed)
            with self.assertRaises(RuntimeError): support.validate(changed,[123])

    def test_collector_includes_native_manifest_but_no_binaries(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); source=root/'source'; build=source/'transport_build'; case=source/'r1_unpinned'
            build.mkdir(parents=True); case.mkdir()
            for name in ('manifest.json','build.log','selftest.json','libuccl_urma_trace.so','libfake_urma.so','trace_selftest'):
                (build/name).write_text('data')
            for name in ('transport.jsonl','boundary_timings.jsonl','transport_snapshot.json'):
                (case/name).write_text('data')
            self.assertEqual(collect_results.collect(source,root/'dest'),6)
            self.assertFalse((root/'dest/transport_build/libuccl_urma_trace.so').exists())

    def test_summary_requires_all_four_configs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); case=root/'node0/r1_unpinned'; case.mkdir(parents=True)
            (case.parent/'setup.json').write_text(json.dumps(dict(fingerprint=dict(job='test',trace_transport=True))))
            (case/'status.json').write_text(json.dumps(dict(rc=0,checks=dict(trace=True))))
            traces=[]; timings=[]
            for op,chunk in profile_support.plan('boundaries'):
                trace=sample(); trace.update(op=op,rdma=chunk)
                traces.append(trace)
                timings.append(dict(op=op,rdma=chunk,iterations=30,transport_trace=True,api_cuda_event_us=1000))
            (case/'transport.jsonl').write_text('\n'.join(map(json.dumps,traces)))
            path=case/'boundary_timings.jsonl'; path.write_text('\n'.join(map(json.dumps,timings)))
            rows=summarize_transport.collect(root)
            self.assertEqual(len(rows),4)
            self.assertEqual(rows[0]['post_write_sample_mean_ns'],100)
            path.write_text('\n'.join(map(json.dumps,timings[:3])))
            with self.assertRaises(RuntimeError): summarize_transport.collect(root)

    def test_worker_separates_regular_timing_and_four_diagnostic_phases(self):
        tree=ast.parse((HERE.parent/'worker.py').read_text())
        function=next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='measured_profile')
        module=ast.parse(''); module.body=[function]
        for traced in (False,True):
            with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ,{'DIAG_SUITE':'boundaries','DIAG_PROFILE_CPU':'0','DIAG_TRACE_ITERATIONS':'2'}), patch('builtins.print'):
                tracer=Mock() if traced else None
                if tracer is not None: tracer.end.side_effect=lambda path:copy.deepcopy(sample())
                control=SimpleNamespace(groups=[dict(new_tids=[123])],verify=Mock())
                event=lambda **kwargs:SimpleNamespace(record=lambda:None,elapsed_time=lambda other:30)
                namespace=dict(counter=0,configurations=profile_support.plan('boundaries'),os=os,json=json,case=Path(temporary),snapshot=lambda label:[],affinity_control=control,tracer=tracer,transport_support=support,original_profile=lambda *args,**kwargs:(0.001,0.0001),torch=SimpleNamespace(cuda=SimpleNamespace(synchronize=lambda:None,Event=event)),dist=SimpleNamespace(barrier=lambda:None),utils=SimpleNamespace(bench=lambda *args,**kwargs:(0.001,0.001,0.001)))
                exec(compile(module,'worker_profile_test','exec'),namespace)
                fn=Mock()
                for _ in range(4): namespace['measured_profile'](fn,('transmit','notify'))
                # Only the separate pass invokes this fake callable: 3 warmups + 2 calls per phase.
                self.assertEqual(fn.call_count,20)
                timing=[json.loads(line) for line in (Path(temporary)/'boundary_timings.jsonl').read_text().splitlines()]
                self.assertEqual([(r['op'],r['rdma']) for r in timing],profile_support.plan('boundaries'))
                self.assertTrue(all(r['transport_trace']==traced for r in timing))
                self.assertEqual(len((Path(temporary)/'metrics.jsonl').read_text().splitlines()),4)
                if traced:
                    self.assertEqual(tracer.begin.call_count,4); self.assertEqual(tracer.end.call_count,4)
                    self.assertEqual(len((Path(temporary)/'transport.jsonl').read_text().splitlines()),4)
                else:
                    self.assertFalse((Path(temporary)/'transport.jsonl').exists())

    def test_mock_sdk_rejected_by_benchmark_builder(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(support.shutil,'which',return_value='g++'):
            with self.assertRaisesRegex(RuntimeError,'MOCK_SDK_REJECTED'):
                support.build(temporary,temporary,HERE/'mock_sdk')

    @unittest.skipUnless(os.name=='posix' and shutil.which('bash'),'requires bash')
    def test_boundary_wrapper_runs_control_then_trace_and_check_is_local(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            shutil.copy2(HERE.parents[1]/'urma_diag/run_boundaries.sh',root/'run_boundaries.sh')
            (root/'run.sh').write_text('#!/usr/bin/env bash\nprintf "CALL\\n" >> "$MOCK_RUN_LOG"\nprintf "%s\\n" "$@" >> "$MOCK_RUN_LOG"\n')
            env=os.environ.copy(); env['MOCK_RUN_LOG']=str(root/'calls.txt')
            command=['bash',str(root/'run_boundaries.sh'),'--node-rank','1','--master-addr','example','--job','test01']
            completed=subprocess.run(command,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
            self.assertEqual(completed.returncode,0,completed.stderr)
            calls=(root/'calls.txt').read_text().split('CALL\n')[1:]
            self.assertEqual(len(calls),2)
            self.assertIn('test01_control',calls[0]); self.assertNotIn('--trace-transport',calls[0])
            self.assertIn('test01_trace',calls[1]); self.assertIn('--trace-transport',calls[1])
            (root/'calls.txt').write_text('')
            completed=subprocess.run(command+['--check-only'],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
            self.assertEqual(completed.returncode,0,completed.stderr)
            calls=(root/'calls.txt').read_text().split('CALL\n')[1:]
            self.assertEqual(len(calls),1)
            self.assertIn('test01_check',calls[0]); self.assertIn('--check-only',calls[0])

    @unittest.skipUnless(os.name=='posix' and shutil.which('g++'),'requires Linux host C++ compiler')
    def test_native_builder_and_multithreaded_fake_provider(self):
        with tempfile.TemporaryDirectory(prefix='uccl_native_test_') as temporary:
            library,manifest,fingerprint=support.build(temporary,temporary,HERE/'mock_sdk',allow_mock=True)
            self.assertTrue(library.is_file())
            self.assertTrue(manifest['selftest_passed'])
            self.assertIn('urma_api.h',manifest['headers'])
            self.assertNotIn('library_sha',fingerprint)

if __name__=='__main__': unittest.main()
