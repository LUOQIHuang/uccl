import ast
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace as NS

HERE = Path(__file__).resolve().parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


analysis = load('completion_analysis_tests', HERE / 'analyze.py')
source_overlay = load('completion_overlay_tests', HERE / 'overlay.py')


def window(rank=1):
    pair = dict(write_id=10, atomic_id=11, bytes=459264, operand=32, finished=True, status=0,
        fence=0 if rank else 1, order=2, cq=7, write_post_begin=10, write_post_end=20,
        atomic_post_begin=10, atomic_post_end=20, write_sdk_return=400, atomic_sdk_return=400,
        write_handled=410, atomic_handled=420, write_poll_seq=3, atomic_poll_seq=3,
        initial_zero=True, result_at_completion=160, result_lower=60, result_upper=70, result_value=160)
    sdk = [dict(cq=7, seq=i+1, begin=b, end=e, count=n)
           for i, (b, e, n) in enumerate([(90,150,0), (190,250,0), (290,400,2)])]
    snapshots = [dict(seq=1,begin=100,end=110,sdk_return=150,ci=31,after_ci=31,count=0,ready_mask=0,completed=[]),
        dict(seq=2,begin=200,end=210,sdk_return=250,ci=31,after_ci=31,count=0,ready_mask=2,completed=[]),
        dict(seq=3,begin=300,end=310,sdk_return=400,ci=31,after_ci=33,count=2,ready_mask=3,
             completed=[dict(wr_id=10,token=1000,position=31),dict(wr_id=11,token=1001,position=32)])]
    return dict(index=1, streams=[dict(proxy=i, errors=0, dropped=0, pairs=[copy.deepcopy(pair)],
        polls=copy.deepcopy(sdk), controls=[dict(wr_id=(ring<<32)|12,kind=3,begin=1,end=9) for ring in range(8)] +
            ([dict(wr_id=j,kind=4,begin=10,end=11) for j in (13,14)] if i==0 else [])) for i in range(4)],
        cq_ready=[dict(proxy=i,dropped=0,depth=32,stride=64,lookahead=16,polls=copy.deepcopy(snapshots)) for i in range(4)])


class CompletionAnalysisTests(unittest.TestCase):
    def test_write_and_fadd_identity_and_different_readiness(self):
        for rank in (0, 1):
            rows, controls = analysis.operation_rows(window(rank), rank)
            self.assertEqual(len(rows), 8)
            write, atomic = rows[:2]
            self.assertEqual((write['cq_position'], atomic['cq_position']), (31, 32))
            self.assertAlmostEqual(write['valid_to_sdk_lower_us'], .09)
            self.assertAlmostEqual(atomic['valid_to_sdk_lower_us'], .19)
            self.assertEqual(write['target_valid_head_invalid_polls'], 0)
            self.assertEqual(atomic['target_valid_head_invalid_polls'], 1)
            self.assertEqual(len(controls), 34)

    def test_unseen_cqe_censored_and_invalid_identity_rejected(self):
        w = window()
        for stream in w['cq_ready']:
            for poll in stream['polls']: poll['ready_mask'] = 0
        rows, _ = analysis.operation_rows(w, 1)
        self.assertTrue(all(not r['observed_valid'] and r['valid_to_sdk_lower_us'] is None for r in rows))
        for corruption in ('ci', 'id', 'fence', 'drop', 'quiet'):
            w = window()
            if corruption == 'ci': w['cq_ready'][0]['polls'][-1]['after_ci'] = 34
            elif corruption == 'id': w['cq_ready'][0]['polls'][-1]['completed'][0]['wr_id'] = 9
            elif corruption == 'fence': w['streams'][0]['pairs'][0]['fence'] = 1
            elif corruption == 'drop': w['streams'][0]['dropped'] = 1
            else: w['streams'][0]['controls'] = []
            with self.assertRaises(ValueError): analysis.operation_rows(w, 1)

    def test_overlay_matches_current_sources_without_gpu_kernel_edits(self):
        generated = source_overlay.compose()
        self.assertFalse(any(name.endswith('.cu') for name in generated))
        for name, text in generated.items():
            if name.endswith('.py'): compile(text, name, 'exec')
        self.assertIn('ControlSpan control_span', generated['ep/src/proxy.cpp'])
        self.assertIn('cq_ready::before', generated['ep/src/urma_transport.cpp'])

    def test_generated_measure_keeps_api_association_and_late_completion_boundary(self):
        import statistics
        source=source_overlay.compose()['test/reference_events/event_runtime.py']
        tree=ast.parse(source)
        tree.body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='measure']
        saved=[];events=[]
        namespace=dict(os=os,json=json,statistics=statistics,
                       append=lambda path,row:saved.append((path.name,row)),control_marker=lambda *a:None)
        exec(compile(tree,'generated_measure','exec'),namespace)
        event=lambda:NS(record=lambda:None,elapsed_time=lambda other:1.0)
        torch=NS(int=object(),empty=lambda *a,**kw:NS(zero_=lambda:None),
                 cuda=NS(synchronize=lambda:None,current_device=lambda:0,Event=lambda **kw:event()))
        dist=NS(barrier=lambda:events.append('barrier'))
        ep=NS(__file__='ep.so',get_normal_completion_probe_version=lambda:1,
              fadd_path_begin=lambda:events.append('probe_begin'),
              fadd_path_end=lambda:(events.append('probe_end') or '{"version":1}'))
        with patch.object(os,'environ',dict(os.environ,FADD_PATH_CAPTURE='1')):
            namespace['measure'](torch,dist,ep,Path('case'),lambda:events.append('fn'),analysis.LABEL,1,3,record_events=False)
        probe=[r for name,r in saved if name=='fadd_path.jsonl']
        api=[r for name,r in saved if name=='api_samples.jsonl']
        self.assertEqual([(r['index'],r['us']) for r in probe],[(r['index'],r['us']) for r in api])
        self.assertEqual(len(probe),3)
        self.assertTrue(all(events[i-1]=='barrier' for i,v in enumerate(events) if v=='probe_end'))

    def test_summary_exports_both_operations_and_keeps_failed_control_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'campaign'
            for rank in (0,1):
                case=root/'cases'/'campaign_probe'/('node%d'%rank)/'normal'
                case.mkdir(parents=True)
                (case/'fadd_path.jsonl').touch()
            def case_data(location,rank,*args,**kwargs):
                probe=location.name.endswith('_probe')
                return dict(case=location/('node%d'%rank)/'normal',
                    c=dict(job=location.name,normal_fadd_fence_plan='peer-tail-off',
                           device='udmac1d1e2' if rank==0 else 'udmac0d1e2',urma_device_plan='202c1-204c0',
                           normal_window_plan='default',normal_retire_plan='ordered',reference_profile='boundaries',
                           fadd_path_capture=probe),
                    runtime=dict(normal_completion_probe_version=1,module_sha='same'),identity=dict(node=rank),
                    means={analysis.LABEL:dict(mean_us=1000,cv_pct=10 if probe else 0)})
            def records(path):
                if path.name!='fadd_path.jsonl':
                    return [dict(index=i,used=i>0,us=1000) for i in range(10)]
                rank=0 if 'node0' in path.parts else 1
                result=[]
                for i in range(10):
                    row=window(rank)
                    row.update(index=i,us=1000,version=1,target_payload=6,ring=0,poll_stride=1,
                               label=analysis.LABEL,clock='CLOCK_MONOTONIC_RAW')
                    result.append(row)
                return result
            with patch.object(analysis.base,'case_data',case_data), patch.object(analysis.base,'rows',records), \
                 patch.object(analysis.base,'read',return_value={'version':1}):
                decision=analysis.summarize(root,Path(tmp)/'summary')
            self.assertFalse(decision['mechanism_usable'])
            self.assertEqual(decision['target_observations'],144)
            self.assertTrue((Path(tmp)/'summary/REPORT.zh-CN.md').is_file())

    @unittest.skipUnless(shutil.which('c++') and os.name == 'posix', 'Linux host C++ compiler')
    def test_actual_generated_probe_with_existing_native_lifecycle_tests(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'fadd_path_probe.hpp').write_text(source_overlay.header())
            (root / 'cq_ready_probe.hpp').write_text((HERE.parent / 'fadd_path/native/cq_ready.hpp').read_text())
            test = (HERE.parent / 'fadd_path/native/probe_test.cpp').read_text().replace(
                '#include "probe.hpp"', '#include "fadd_path_probe.hpp"').replace(
                '  auto text=end();', '  {ControlSpan quiet(500,3,0);}\n  assert(local->controls.size()==1);\n  auto text=end();')
            (root/'test.cpp').write_text(test)
            subprocess.run(['c++','-std=c++17','-O2','-pthread','-I'+str(root),
                str(root/'test.cpp'),'-o',str(root/'test')],check=True)
            result=subprocess.run([str(root/'test')],check=True,stdout=subprocess.PIPE,universal_newlines=True)
            record=json.loads(result.stdout)
            self.assertEqual(record['poll_stride'],1)
            self.assertEqual(record['streams'][0]['controls'][0]['kind'],3)


@unittest.skipUnless(os.name == 'posix', 'Linux runner')
class CompletionRunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.runner = load('completion_runner_tests', HERE / 'runner.py')
        self.tree = self.root/'tree'
        for name in ('test/urma_mode_parity', 'test/gpu_stage_recovery'):
            (self.tree/name).mkdir(parents=True)
        (self.root/'lib/urma').mkdir(parents=True)
        (self.root/'lib/urma/liburma-udma.so').write_text('fake fixture')
        (self.root/'build').mkdir()
        (self.root/'build/manifest.json').write_text('{"module_sha":"same"}')
        (self.tree/'test/urma_mode_parity/run.sh').write_text('#!/bin/bash\nexec "$URMA_DIAG_PYTHON" "$(dirname "$0")/fake.py" "$@"\n')
        (self.tree/'test/urma_mode_parity/fake.py').write_text('''
import json,os,sys
from pathlib import Path
a=sys.argv[1:];get=lambda k:a[a.index(k)+1]
root=Path(get('--output'));root.mkdir(parents=True,exist_ok=True)
row={k:get(k) for k in ('--action','--job','--node-rank','--reference-events')}
row.update(capture=os.environ['FADD_PATH_CAPTURE'],owner=os.environ['FADD_PATH_CQ_READY'])
with (root/'calls.jsonl').open('a') as f:f.write(json.dumps(row)+'\\n')
if get('--action')=='run':(root/get('--job')/('node'+get('--node-rank'))).mkdir(parents=True)
sys.exit(7 if get('--action')=='run' and get('--job').endswith(os.environ.get('FAIL_SUFFIX','NOT_A_CASE')) else 0)
''')
        (self.tree/'test/gpu_stage_recovery/collect.py').write_text('''
import sys
from pathlib import Path
p=Path(sys.argv[1]).parent/'collected.txt'
with p.open('a') as f:f.write(Path(sys.argv[1]).name+'\\n')
''')

    def invoke(self, action, failure=None):
        argv=['--action',action,'--node-rank','1','--job','test_completion','--tree',str(self.tree),
              '--build-dir',str(self.root/'build'),'--output',str(self.root/'out'),'--destination',str(self.root/'dest')]
        env=dict(URMA_DIAG_LIBRARY_DIR=str(self.root/'lib'),URMA_DIAG_PYTHON=sys.executable)
        if failure: env['FAIL_SUFFIX']=failure
        with patch.dict(os.environ,env), patch.object(self.runner.overlay,'prepare',return_value={'version':1}), \
             patch.object(self.runner.overlay,'sha',return_value=self.runner.PROVIDER_SHA):
            self.runner.main(argv)

    def calls(self):
        return [json.loads(line) for line in (self.root/'out/test_completion/cases/calls.jsonl').read_text().splitlines()]

    def test_exact_three_case_sequence_capture_and_no_overwrite(self):
        self.invoke('setup')
        self.invoke('run')
        runs=[r for r in self.calls() if r['--action']=='run']
        self.assertEqual([r['--job'] for r in runs],['test_completion_pre','test_completion_probe','test_completion_post'])
        self.assertEqual([r['capture'] for r in runs],['0','1','0'])
        self.assertEqual([r['owner'] for r in runs],['0','1','0'])
        self.assertTrue(all(r['--reference-events']=='paced' for r in runs))
        before=len(self.calls())
        with self.assertRaises(ValueError): self.invoke('run')
        self.assertEqual(len(self.calls()),before)

    def test_failed_probe_collected_and_post_not_started(self):
        self.invoke('setup')
        with self.assertRaises(RuntimeError): self.invoke('run','_probe')
        self.assertNotIn('test_completion_post',[r['--job'] for r in self.calls()])
        self.assertIn('test_completion_probe',(self.root/'out/test_completion/cases/collected.txt').read_text())


if __name__=='__main__': unittest.main()
