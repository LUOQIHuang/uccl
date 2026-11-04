import ast
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from contextlib import nullcontext
from typing import Union,Optional

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import phase_support
from phase_runtime import Measurements
from summarize_phases import summarize


class PhaseTests(unittest.TestCase):
    def test_saved_ll_trace_is_exported_only_once(self):
        source=(Path(__file__).resolve().parents[3]/'ep/bench/utils.py').read_text(encoding='utf-8')
        function=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='bench_kineto')
        class Profile:
            exports=0
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def step(self): pass
            def key_averages(self):
                return SimpleNamespace(table=lambda **kwargs:'dispatch 10us 1\ncombine 20us 1')
            def export_chrome_trace(self,path):
                self.exports+=1
                if self.exports>1: raise RuntimeError('trace already saved')
                events=[dict(name='kernel::'+name,ts=i,dur=duration)
                        for name in ('dispatch','combine') for i,duration in enumerate((10,20,10,20))]
                Path(path).write_text(json.dumps(dict(traceEvents=events)))
        profile=Profile()
        torch=SimpleNamespace(profiler=SimpleNamespace(schedule=lambda **kw:None,
            ProfilerActivity=SimpleNamespace(CUDA=0),profile=lambda **kw:profile),
            cuda=SimpleNamespace(synchronize=lambda:None))
        ns=dict(torch=torch,dist=SimpleNamespace(barrier=lambda:None,get_rank=lambda:0),
                empty_suppress=nullcontext,suppress_stdout_stderr=nullcontext,Union=Union,Optional=Optional,
                Path=Path,json=json,tempfile=tempfile)
        exec(compile(ast.Module(body=[function],type_ignores=[]),'bench_kineto','exec'),ns)
        with tempfile.TemporaryDirectory() as tmp:
            result=ns['bench_kineto'](lambda:None,('dispatch','combine'),num_tests=2,
                                      trace_path=str(Path(tmp)/'trace.json'),num_kernels_per_period=2)
        self.assertEqual(profile.exports,1)
        self.assertEqual(result,[[.00001,.00002],[.00001,.00002]])

    def test_immediate_hook_changes_only_workload_body(self):
        source='def test():\n    def large_gemm_with_hook(hook):\n        x = 1\n        hook()\n    correctness()\n'
        result=phase_support.immediate_hook_source(source)
        self.assertIn('    correctness()',result)
        helper=next(n for n in ast.walk(ast.parse(result)) if isinstance(n,ast.FunctionDef) and n.name=='large_gemm_with_hook')
        self.assertEqual(len(helper.body),1)
        self.assertEqual(helper.body[0].value.func.id,'hook')
        with self.assertRaises(ValueError): phase_support.immediate_hook_source('pass\n')

    def test_real_ll_benchmark_preserves_all_other_ast_nodes(self):
        source=(Path(__file__).resolve().parents[3]/'ep/bench/test_low_latency.py').read_text(encoding='utf-8')
        before=ast.parse(source); after=ast.parse(phase_support.immediate_hook_source(source))
        for tree in (before,after):
            helper=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='large_gemm_with_hook')
            helper.body=[]
        self.assertEqual(ast.dump(before,include_attributes=False),ast.dump(after,include_attributes=False))

    def test_delta_handles_new_threads_and_rejects_counter_reset(self):
        metric=dict(calls=4,ns=200,items=8,histogram=[0]*32)
        after=dict(records=[dict(id=0,rank=0,proxy=1,metrics={'sdk_post':metric})])
        self.assertEqual(phase_support.snapshot_delta(dict(records=[]),after)[0]['metrics']['sdk_post']['items'],8)
        self.assertEqual(phase_support.snapshot_delta(after,after),[])
        with self.assertRaises(ValueError):
            phase_support.snapshot_delta(after,dict(records=[dict(id=0,rank=0,proxy=1,metrics={'sdk_post':dict(metric,calls=0)})]))

    def test_event_samples_drop_first_and_warmups_outside_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            class Event:
                counter=0
                def __init__(self,**kwargs): self.i=Event.counter; Event.counter+=1
                def record(self): pass
                def elapsed_time(self,other): return self.i+1
            torch=SimpleNamespace(cuda=SimpleNamespace(synchronize=lambda:None,current_device=lambda:0,Event=Event),
                                  int='int',empty=lambda *a,**k:SimpleNamespace(zero_=lambda:None))
            ep=SimpleNamespace(urma_phase_snapshot=lambda:'{"records":[]}',urma_phase_enable=Mock())
            measurements=Measurements(torch,SimpleNamespace(barrier=lambda:None),ep,Path(tmp),'normal',True,3,5)
            calls=Mock(); result=measurements.api(calls,'dispatch_fp8')
            self.assertEqual(calls.call_count,8)
            self.assertAlmostEqual(result[0],.0035)
            samples=[json.loads(s) for s in (Path(tmp)/'api_samples.jsonl').read_text().splitlines()]
            self.assertEqual(sum(s['used'] for s in samples),4)
            self.assertEqual(ep.urma_phase_enable.call_args_list[0].args,(True,))
            self.assertEqual(ep.urma_phase_enable.call_args_list[-1].args,(False,))

    def test_summary_keeps_independent_runs_and_rejects_failed_cases(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for i,(enabled,mean) in enumerate(((False,10),(False,12),(True,15))):
                run=root/f'run{i}/node0'; case=run/'normal'; case.mkdir(parents=True)
                config=dict(job=f'run{i}',shape=[4096,128,7168,256,8],kernel='kernel',build_hash='build',ll_hook_workload='immediate')
                (run/'setup.json').write_text(json.dumps(dict(fingerprint=config)))
                (run/'host.json').write_text('{"host":"node202"}')
                (case/'status.json').write_text('{"rc":0,"checks":{"done":true}}')
                (case/'sample_summary.jsonl').write_text(json.dumps(dict(label='dispatch',mode='normal',enabled=enabled,mean_us=mean,warmups=50,measured=50)))
                (case/'phases.jsonl').write_text(json.dumps(dict(label='dispatch',mode='normal',iterations=50,records=[])))
            _,comparison,_=summarize([root],root/'out')
            self.assertEqual(comparison[0]['baseline_runs'],2)
            self.assertEqual(comparison[0]['baseline_median_us'],11)
            (root/'run0/node0/normal/status.json').write_text('{"rc":1,"checks":{}}')
            with self.assertRaises(ValueError): summarize([root],root/'out')
