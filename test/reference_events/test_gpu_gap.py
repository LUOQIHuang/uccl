"""GPU-only capture keeps coverage checks and cannot enter legacy attribution."""
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
from test_boundaries import boundary_window
from test_window_plan import driver_functions
import event_runtime as events


class GpuGapTests(unittest.TestCase):
    def arguments(self,backend,cadence='on'):
        argv=['driver','--node-rank','1','--job','gap','--mode','normal','--reference-events',cadence,
            '--reference-profile','boundaries','--reference-gpu-only','--dispatch-focus','--warmups','5','--samples','10']
        return argv+(['--phase-suite','--api-only'] if backend=='urma' else ['--variant','current','--no-profile','--repeats','1'])

    def test_parsers_scope_and_clear_inherited_capture_setting(self):
        for backend in ('urma','verbs'):
            ns=driver_functions(backend)
            for cadence in ('batch','paced','on'):
                with patch.object(sys,'argv',self.arguments(backend,cadence)):args=ns['parser']()
                self.assertEqual(ns['environment'](args)['UCCL_REFERENCE_GPU_ONLY'],'1')
            for extra in (['--reference-profile','legacy'],['--normal-tokens','128'],['--samples','31'],['--action','build']):
                with patch.object(sys,'argv',self.arguments(backend)+extra),patch('sys.stderr',io.StringIO()),self.assertRaises(SystemExit):ns['parser']()
            plain=[v for v in self.arguments(backend) if v!='--reference-gpu-only']
            with patch.object(sys,'argv',plain),patch.dict(os.environ,UCCL_REFERENCE_GPU_ONLY='1'):
                self.assertEqual(ns['environment'](ns['parser']())['UCCL_REFERENCE_GPU_ONLY'],'0')

    def test_gpu_only_runtime_never_calls_cpu_capture_and_retains_all_points(self):
        class Capture:
            def __init__(self,path):self.i=-1
            def begin(self):self.i+=1
            def end(self):return boundary_window('dispatch_bf16_chunk32',self.i)['gpu']
        torch=NS(empty=lambda *a,**k:NS(zero_=lambda:None),int=object(),cuda=NS(synchronize=lambda:None,
            current_device=lambda:0,Event=lambda **k:NS(record=lambda:None,elapsed_time=lambda e:1.0)))
        # No CPU timeline methods: an accidental call is an immediate failure.
        ep=NS(__file__='fake.so');dist=NS(barrier=lambda:None)
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,UCCL_REFERENCE_PROFILE='boundaries',UCCL_REFERENCE_GPU_ONLY='1'):
            case=Path(tmp)
            for label in events.LABELS:events.measure(torch,dist,ep,case,lambda:None,label,1,3,capture_factory=Capture)
            self.assertTrue(events.validate(case,3,'boundaries',cpu_events=False))
            with self.assertRaisesRegex(ValueError,'CPU capture mode'):events.validate(case,3,'boundaries')
            data=[json.loads(s) for s in (case/'event_trace.jsonl').read_text().splitlines()]
            self.assertTrue(all(w['cpu'] is None and w['cpu_events'] is False for w in data))
            data[0]['gpu']['records'].pop()
            (case/'event_trace.jsonl').write_text('\n'.join(json.dumps(w) for w in data))
            with self.assertRaises(ValueError):events.validate(case,3,'boundaries',cpu_events=False)

    def test_local_envelope_partition_and_gate_failure_retained(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools/reference_events'))
        import summarize_gpu_gap as tool
        w=boundary_window('dispatch_bf16_chunk32',1);values=tool.endpoints(w)
        self.assertAlmostEqual(sum(values[k] for k in ('first_token_to_first_write_us','first_write_to_last_inbound_visible_us','last_inbound_visible_to_receiver_done_us')),values['envelope_us'])
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for bad in (False,True):
                def fake(path,rank,*a,**kw):
                    self.assertTrue(kw['reference_gpu_only'])
                    tag=next(t for t in tool.TAGS if path.name.endswith('_'+t))
                    case=path/('node%d'%rank)/'normal';case.mkdir(parents=True,exist_ok=True)
                    rows=[dict(label=tool.LABEL,index=i,used=i>0,us=1000,mode='normal') for i in range(10)]
                    (case/'api_samples.jsonl').write_text('\n'.join(json.dumps(r) for r in rows))
                    if tag=='trace':
                        data=[]
                        for i in range(10):
                            w=boundary_window(tool.LABEL,i);w.update(us=1000,cpu=None,cpu_events=False);data.append(w)
                        (case/'event_trace.jsonl').write_text('\n'.join(json.dumps(w) for w in data))
                    identity=dict(backend='urma',reference_profile='boundaries',normal_fadd_fence_plan='peer-tail-off')
                    return dict(identity=identity,runtime=dict(module_sha='same'),case=case,
                        means={tool.LABEL:dict(mean_us=1000,cv_pct=6 if bad and tag=='trace' else 0)})
                with patch.object(tool.matched.base,'case_data',fake):
                    result=tool.summarize(root,'job','urma',root/('bad' if bad else 'good'))
                self.assertEqual(result['usable'],not bad)
                self.assertFalse(result['production_gap_attribution'])

    def test_backend_comparison_refuses_source_changes_and_checks_cadence(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools/reference_events'))
        import summarize_gpu_gap as tool
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for backend,value in (('urma',1100),('verbs',1000)):
                path=root/backend;path.mkdir()
                nodes=[dict(torch='same',maca={'runtime':'sha'},gpu_versions=['same'],sources={
                    'ep/src/internode.cu':'same','ep/include/uccl_ibgda.cuh':'same'}) for i in range(2)]
                (path/'context.json').write_text(json.dumps(dict(backend=backend,usable=True,nodes=nodes,
                    batch_reference_us=value,paced_reference_us=value)))
                (path/'envelope_summary.csv').write_text('node,span,n,mean_us,median_us\n0,envelope_us,9,%d,%d\n'%(value,value))
            tool.compare(root/'urma',root/'verbs',root/'comparison')
            self.assertTrue(json.loads((root/'comparison/context.json').read_text())['cadence_gap_consistent'])
            p=root/'verbs/context.json';data=json.loads(p.read_text());data['nodes'][1]['sources']['ep/src/internode.cu']='changed';p.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError,'compiled source'):tool.compare(root/'urma',root/'verbs',root/'reject')


@unittest.skipUnless(os.name=='posix','Linux shell orchestration')
class GpuGapRunnerTests(unittest.TestCase):
    def setUp(self):
        from test_fence_runner import FenceRunnerTests
        self.f=FenceRunnerTests();self.f.setUp();self.addCleanup(self.f.temporary.cleanup)
        fake=self.f.root/'urma_mode_parity/fake.py'
        fake.write_text(fake.read_text().replace("flags={'--phase-suite'","flags={'--reference-gpu-only','--no-profile','--phase-suite'").replace("root=Path(a['--output'])","a.setdefault('--normal-fadd-fence-plan','default')\nroot=Path(a['--output'])"))
        verbs=self.f.root/'verbs_baseline';verbs.mkdir()
        (verbs/'run.sh').write_text('#!/usr/bin/env bash\nexec "$URMA_DIAG_PYTHON" "$(dirname "$0")/../urma_mode_parity/fake.py" "$@"\n')
        (self.f.root/'reference_events/run_gap_profile.sh').write_text(Path(__file__).with_name('run_gap_profile.sh').read_text())

    def execute(self,backend,fail=''):
        import subprocess
        return subprocess.run(['bash',str(self.f.root/'reference_events/run_gap_profile.sh'),'--node-rank','1','--backend',backend,
            '--output',str(self.f.root/'out'), '--destination',str(self.f.root/'dest'),'--build-dir',str(self.f.root/'build')],
            env=dict(os.environ,URMA_DIAG_PYTHON=sys.executable,FAIL_SUFFIX=fail),stdout=subprocess.PIPE,stderr=subprocess.STDOUT,universal_newlines=True)

    def test_both_backend_exact_five_cases_no_overwrite(self):
        for backend in ('urma','verbs'):
            result=self.execute(backend);self.assertEqual(result.returncode,0,result.stdout)
            runs=[r for r in self.f.calls() if r['--action']=='run' and backend in r['--job']]
            self.assertEqual([r['--reference-events'] for r in runs],['batch','paced','on','paced','batch'])
            self.assertTrue(all('--reference-gpu-only' in r for r in runs))
        before=len(self.f.calls());self.assertNotEqual(self.execute('urma').returncode,0);self.assertEqual(len(self.f.calls()),before)

    def test_failed_trace_collected_and_stops(self):
        result=self.execute('urma','_trace');self.assertEqual(result.returncode,7,result.stdout)
        runs=[r for r in self.f.calls() if r['--action']=='run']
        self.assertEqual(len(runs),3)
        self.assertIn('_trace',(self.f.root/'out/collected.jsonl').read_text())


if __name__=='__main__':unittest.main()
