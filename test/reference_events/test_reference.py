import argparse
import ast
from contextlib import redirect_stdout
import ctypes
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock,patch

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE));sys.path.append(str(HERE.parent/'dispatch_diag'));sys.path.append(str(HERE.parent/'urma_mode_parity'))
from event_runtime import Trace,Clock,LABELS,NAMES,validate,measure,validate_control,control_marker
from correctness import install
import test_dispatch as fixtures
from test_dispatch import dump,lines
spec=importlib.util.spec_from_file_location('reference_summary',HERE/'summarize.py')
summary=importlib.util.module_from_spec(spec);spec.loader.exec_module(summary)


def window(label,index=0):
    events=range(3) if label.startswith('dispatch') else range(3,6)
    records=[dict(event=e,slot=0,name=NAMES[e],host_ns=1000+e,device_before=10,device_after=20,block=0,warp=0,epoch=index+1) for e in events]
    cpu=[dict(proxy=0,wr_id=7,qp=0,ns=100+i*100,stage=s,value=0) for i,s in enumerate(('poll','decoded','collect','prepare','post_begin','post_end','cqe','ack_slot'))]
    return dict(label=label,index=index,us=1010,gpu=dict(version=1,epoch=index+1,clock='CLOCK_MONOTONIC_RAW',dropped=0,
        records=records,counts=[int(e in events) for e in range(6)],clock_stats=dict(updates=100,max_gap_ns=500,gaps_over_1us=0,gaps_over_10us=0,scheduler_error=0)),cpu=dict(clock='CLOCK_MONOTONIC_RAW',origin_ns=50,dropped=0,events=cpu))


class ReferenceTests(unittest.TestCase):
    def test_batch_control_uses_original_continuous_runtime_and_no_timeline(self):
        from phase_runtime import Measurements
        class Event:
            def __init__(self,**kw):pass
            def record(self):pass
            def elapsed_time(self,other):return 1
        torch=NS(cuda=NS(synchronize=Mock(),current_device=lambda:0,Event=Event),empty=lambda *a,**k:NS(zero_=Mock()),int=0)
        ep=NS(urma_phase_snapshot=lambda:'{"records":[]}',urma_phase_enable=Mock(),urma_timeline_begin=Mock())
        dist=NS(barrier=Mock());fn=Mock()
        with tempfile.TemporaryDirectory() as tmp,redirect_stdout(io.StringIO()):
            case=Path(tmp);m=Measurements(torch,dist,ep,case,'normal',False,1,3)
            m.reference_control='batch'
            for label in LABELS:m.api(fn,label)
            self.assertTrue(validate_control(case,'batch',1,3))
            self.assertEqual(fn.call_count,8)
            self.assertEqual(dist.barrier.call_count,4) # once before/after each group
        ep.urma_timeline_begin.assert_not_called()

    def test_paced_control_runs_same_samples_without_loading_capture_or_cpu_timeline(self):
        class Event:
            def __init__(self,**kw):pass
            def record(self):pass
            def elapsed_time(self,other):return 1
        torch=NS(cuda=NS(synchronize=Mock(),current_device=lambda:0,Event=Event),empty=lambda *a,**k:NS(zero_=Mock()),int=0)
        ep=NS(__file__='fake.so',urma_timeline_begin=Mock(),urma_timeline_end=Mock())
        factory=Mock(side_effect=AssertionError('inactive control must not load capture'))
        dist=NS(barrier=Mock());fn=Mock()
        with tempfile.TemporaryDirectory() as tmp,redirect_stdout(io.StringIO()):
            case=Path(tmp)
            for label in LABELS:
                measure(torch,dist,ep,case,fn,label,1,3,capture_factory=factory,record_events=False)
            self.assertTrue(validate_control(case,'paced',1,3))
            self.assertEqual(fn.call_count,8)
            self.assertEqual(dist.barrier.call_count,24)
            self.assertEqual(len((case/'api_samples.jsonl').read_text().splitlines()),6)
            (case/'event_trace.jsonl').write_text('{}\n')
            with self.assertRaises(ValueError):validate_control(case,'paced',1,3)
        factory.assert_not_called();ep.urma_timeline_begin.assert_not_called();ep.urma_timeline_end.assert_not_called()

    def test_cadence_summary_validates_two_inactive_profiles(self):
        spec=importlib.util.spec_from_file_location('cadence_summary',HERE/'summarize_cadence.py')
        cadence=importlib.util.module_from_spec(spec);spec.loader.exec_module(cadence)
        with tempfile.TemporaryDirectory() as tmp,redirect_stdout(io.StringIO()):
            for backend in ('urma','verbs'):
                root=Path(tmp)/backend;_,template,_=fixtures.DispatchTests().fixture(root,backend)
                for mode in ('batch','paced'):
                    shutil.copytree(template,root/mode)
                    for rank in (0,1):
                        node=root/mode/f'node{rank}';case=node/('normal' if backend=='urma' else 'r01_baseline/normal')
                        data=json.loads((node/'setup.json').read_text());data['fingerprint'].update(gpu_stages=False,reference_events=mode);dump(node/'setup.json',data)
                        data=json.loads((case/'runtime.json').read_text());data.update(gpu_stage_version=0,reference_events_version=1);dump(case/'runtime.json',data)
                        data=json.loads((case/'status.json').read_text());data['checks']['reference_control']=True;dump(case/'status.json',data)
                        manifest=node/('build_manifest.json' if backend=='urma' else 'module_manifest.json')
                        data=json.loads(manifest.read_text());data['flags'].update(UCCL_GPU_STAGE_TRACE='0',UCCL_REFERENCE_EVENTS='1');dump(manifest,data)
                        (case/'gpu_stages.jsonl').unlink()
                        for label in LABELS:control_marker(case,label,mode,5,10)
                result=cadence.summarize(root/'batch',root/'paced',root/'summary')
                self.assertEqual(len(result),10);self.assertTrue(all(r['stable'] and r['within_5pct'] for r in result))
                (case/'event_trace.jsonl').write_text('{}\n')
                with self.assertRaises(ValueError):cadence.summarize(root/'batch',root/'paced',root/'bad')

    def test_correctness_capture_restores_dispatch_before_timing(self):
        class Buffer:
            def dispatch(self,value):return value*2
        original=Buffer.dispatch
        cap=NS(begin=Mock(),end=Mock(return_value=window('dispatch_fp8_chunk32')['gpu']))
        with tempfile.TemporaryDirectory() as tmp,redirect_stdout(io.StringIO()):
            case=Path(tmp);finish=install(NS(__file__='fake.so'),NS(Buffer=Buffer),case,capture_factory=lambda p:cap)
            self.assertEqual(Buffer().dispatch(3),6)
            self.assertEqual(Buffer().dispatch(4),8)
            finish(True)
            self.assertIs(Buffer.dispatch,original)
            self.assertTrue(json.loads((case/'reference_correctness.json').read_text())['verified'])
            self.assertNotIn('records',json.loads((case/'reference_correctness.json').read_text()))
        cap.begin.assert_called_once();cap.end.assert_called_once()
    def test_abi_and_rejection_of_partial_stale_or_overflowed_captures(self):
        self.assertEqual(ctypes.sizeof(Trace),1769504);self.assertEqual(ctypes.sizeof(Clock),40)
        with tempfile.TemporaryDirectory() as tmp:
            case=Path(tmp);good=[window(l,i) for l in LABELS for i in range(3)]
            lines(case/'event_trace.jsonl',good);self.assertTrue(validate(case,3))
            for mutate in (lambda r:r['gpu']['records'].pop(),lambda r:r['gpu'].update(dropped=1),lambda r:r['gpu']['records'][0].update(epoch=999),lambda r:r['cpu']['events'].pop(),lambda r:r['cpu']['events'][5].update(value=1),lambda r:r['cpu']['events'][6].update(value=1)):
                bad=json.loads(json.dumps(good));mutate(bad[0]);lines(case/'event_trace.jsonl',bad)
                with self.assertRaises(ValueError):validate(case,3)

    def test_real_runtime_closes_capture_and_cpu_window_on_failure(self):
        class Event:
            def __init__(self,**kw):pass
            def record(self):pass
            def elapsed_time(self,other):return 1
        torch=NS(cuda=NS(synchronize=Mock(),current_device=lambda:0,Event=Event),empty=lambda *a,**k:NS(zero_=Mock()),int=0)
        ep=NS(__file__='fake.so',urma_timeline_begin=Mock(),urma_timeline_end=Mock(return_value='{"events":[],"dropped":0}'))
        cap=NS(begin=Mock(),end=Mock(return_value={'records':[]}))
        fn=Mock(side_effect=[None,RuntimeError('benchmark failed')])
        with tempfile.TemporaryDirectory() as tmp,self.assertRaisesRegex(RuntimeError,'benchmark failed'):
            measure(torch,NS(barrier=Mock()),ep,Path(tmp),fn,'dispatch_fp8_chunk32',1,3,capture_factory=lambda p:cap)
        cap.end.assert_called_once();ep.urma_timeline_end.assert_called_once()

    def test_real_runtime_writes_each_sample_and_matching_capture(self):
        class Event:
            def __init__(self,**kw):pass
            def record(self):pass
            def elapsed_time(self,other):return 1
        torch=NS(cuda=NS(synchronize=Mock(),current_device=lambda:0,Event=Event),empty=lambda *a,**k:NS(zero_=Mock()),int=0)
        ep=NS(__file__='fake.so',urma_timeline_begin=Mock(),urma_timeline_end=lambda:json.dumps(window('dispatch_fp8_chunk32')['cpu']))
        with tempfile.TemporaryDirectory() as tmp,redirect_stdout(io.StringIO()):
            case=Path(tmp)
            for label in LABELS:
                class Capture:
                    def __init__(self,p):self.index=-1
                    def begin(self):self.index+=1
                    def end(self):return window(label,self.index)['gpu']
                result=measure(torch,NS(barrier=Mock()),ep,case,Mock(),label,1,3,capture_factory=Capture)
                self.assertEqual(result,(0.001,0.001,0.001))
            self.assertTrue(validate(case,3))
            self.assertEqual(len((case/'api_samples.jsonl').read_text().splitlines()),6)

    def test_actual_parsers_allow_reference_builds_and_reject_old_trace_mixing(self):
        for backend,folder in [('urma','urma_mode_parity'),('verbs','verbs_baseline')]:
            path=HERE.parent/folder/'driver.py';tree=ast.parse(path.read_text())
            fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='parser')
            ns=dict(argparse=argparse,Path=Path,os=os,re=re,HERE=path.parent,sys=sys)
            exec(compile(ast.Module(body=[fn],type_ignores=[]),str(path),'exec'),ns)
            args=['driver','--job','reference','--node-rank','0','--mode','normal','--dispatch-focus','--reference-events','on']
            args+=['--phase-suite','--api-only'] if backend=='urma' else ['--variant','current','--no-profile','--repeats','1']
            for mode in ('on','batch','paced'):
                changed=list(args);changed[changed.index('--reference-events')+1]=mode
                with patch.object(sys,'argv',changed):self.assertEqual(ns['parser']().reference_events,mode)
            for extra in (['--gpu-stages'],['--action','build'],['--timeline']):
                with patch.object(sys,'argv',args+extra),patch('sys.stderr',io.StringIO()),self.assertRaises(SystemExit):ns['parser']()

    def test_end_to_end_summaries_and_perturbation_gate(self):
        with tempfile.TemporaryDirectory() as tmp,redirect_stdout(io.StringIO()):
            for backend in ('urma','verbs'):
                root=Path(tmp)/backend;b,t,p=fixtures.DispatchTests().fixture(root,backend)
                for rank in (0,1):
                    node=t/f'node{rank}';case=node/('normal' if backend=='urma' else 'r01_baseline/normal')
                    d=json.loads((node/'setup.json').read_text());d['fingerprint'].update(gpu_stages=False,reference_events='on');dump(node/'setup.json',d)
                    d=json.loads((case/'runtime.json').read_text());d.update(gpu_stage_version=0,reference_events_version=1);dump(case/'runtime.json',d)
                    d=json.loads((case/'status.json').read_text());d['checks'].update(reference_events=True,reference_correctness=True);dump(case/'status.json',d)
                    dump(case/'reference_correctness.json',dict(window('dispatch_fp8_chunk32')['gpu'],verified=True))
                    manifest=node/('build_manifest.json' if backend=='urma' else 'module_manifest.json')
                    d=json.loads(manifest.read_text());d['flags'].update(UCCL_GPU_STAGE_TRACE='0',UCCL_REFERENCE_EVENTS='1');dump(manifest,d)
                    (case/'gpu_stages.jsonl').unlink()
                    lines(case/'event_trace.jsonl',[window(l,i) for l in LABELS for i in range(10)])
                result=summary.summarize(b,t,p,root/'summary')
                self.assertEqual(len(result),10);self.assertTrue(all(r['representative'] for r in result))
                d=json.loads((case/'event_trace.jsonl').read_text().splitlines()[0]);d['gpu']['dropped']=1
                rows=[json.loads(s) for s in (case/'event_trace.jsonl').read_text().splitlines()];rows[0]=d;lines(case/'event_trace.jsonl',rows)
                with self.assertRaises(ValueError):summary.summarize(b,t,p,root/'bad')


if __name__=='__main__':unittest.main()
