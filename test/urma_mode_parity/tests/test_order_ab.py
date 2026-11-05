import ast
from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

HERE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(HERE))
import support
from summarize_order_ab import summarize
spec=importlib.util.spec_from_file_location('order_driver',HERE/'driver.py')
driver=importlib.util.module_from_spec(spec); spec.loader.exec_module(driver)


class OrderTests(unittest.TestCase):
    def test_collected_benchmark_copies_do_not_change_diagnostic_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'test/urma_mode_parity/driver.py';source.parent.mkdir(parents=True);source.write_text('source')
            with patch.object(support,'REPO',root):
                before=support.diagnostic_manifest()
                for subdir in ('results/job/node1','cache','tmpdir'):
                    copy=source.parent/subdir/'normal.py';copy.parent.mkdir(parents=True);copy.write_text('local result')
                self.assertEqual(before,support.diagnostic_manifest())

    def test_argument_rules_and_explicit_environment(self):
        args=['driver','--job','test','--node-rank','0','--phase-suite','--api-only','--ll-write-order','relax']
        with patch.object(sys,'argv',args):
            parsed=driver.parser()
        with patch.dict(os.environ,{'UCCL_URMA_LL_WRITE_ORDER':'strong'}):
            self.assertEqual(driver.environment(parsed)['UCCL_URMA_LL_WRITE_ORDER'],'relax')
        for extra in [ ['--api-only'], ['--phase-suite','--api-only','--save-traces'], ['--backend','verbs','--ll-write-order','relax'] ]:
            with patch.object(sys,'argv',['driver','--job','test','--node-rank','0']+extra),redirect_stdout(io.StringIO()),patch('sys.stderr',io.StringIO()):
                with self.assertRaises(SystemExit):driver.parser()

    def test_api_only_profile_callback_never_enters_profiler(self):
        source=(HERE/'worker.py').read_text()
        node=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='measured_profile')
        for mode in ('normal','low_latency'):
            with tempfile.TemporaryDirectory() as tmp:
                case=Path(tmp)
                forbidden=Mock(side_effect=AssertionError('profiler entered API-only run'))
                measure=Mock(return_value=(.004,.003,.005))
                record=Mock()
                ns=dict(count=0,normal_plan=[('dispatch_fp8',32),('dispatch_bf16',32),('combine',12),('combine',16)],
                    mode=mode,api_only=True,phase_suite=True,measurements=SimpleNamespace(api=measure,window=forbidden),
                    original_profile=forbidden,original_api=forbidden,record=record,case=case,
                    os=os,dist=SimpleNamespace(barrier=Mock()),time=SimpleNamespace(time_ns=lambda:10),subprocess=SimpleNamespace(run=lambda *a,**k:SimpleNamespace(stdout='kernel',stderr='',returncode=0)),json=json)
                with patch.dict(os.environ,{'PARITY_SAVE_TRACES':'0','PARITY_LL_HOOK_WORKLOAD':'immediate'}),redirect_stdout(io.StringIO()):
                    exec(compile(ast.Module(body=[node],type_ignores=[]),'profile_callback','exec'),ns)
                    for i in range(4 if mode=='normal' else 2):
                        result=ns['measured_profile'](lambda:None,('dispatch','notify'))
                        if mode=='normal':self.assertGreater(result[1],0)
                forbidden.assert_not_called()
                self.assertEqual(measure.call_count,4 if mode=='normal' else 1)
                self.assertTrue(all(c.args[0]!='MODE_PARITY_PROFILE' for c in record.call_args_list))
                self.assertTrue((case/'kernel_after_measurements.txt').exists())
                self.assertTrue((case/'measurement_boundary.json').exists())
                ns['dist'].barrier.assert_called_once()

    def test_collect_suite_keeps_both_variants_and_skips_native(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for order in ('strong','relax'):
                p=root/'raw'/('ab_r01_baseline_'+order)/'node0/low_latency';p.mkdir(parents=True)
                (p/'status.json').write_text('{}');(p/'kernel_after_measurements.txt').write_text('boundary')
                (p/'module.so').write_text('excluded')
            args=SimpleNamespace(job='ab',output=root/'raw',node_rank=0,collect_suite=True)
            with patch.object(support,'REPO',root/'repo'),redirect_stdout(io.StringIO()):driver.collect(args)
            target=root/'repo/test/urma_mode_parity/results/ab'
            self.assertEqual(len(list(target.rglob('status.json'))),2)
            self.assertEqual(len(list(target.rglob('*.so'))),0)

    def test_order_summary_uses_only_uninstrumented_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for order,value in [('strong',20000),('relax',2000)]:
                for tag,instrumented in [('r01',False),('r02',False),('diag',True)]:
                    node=root/(tag+'_'+order)/'node0';case=node/'low_latency';case.mkdir(parents=True)
                    fingerprint=dict(job=tag+'_'+order,api_only=True,ll_write_order=order,stage_profile=instrumented,
                        samples=4,warmups=50,shape=[4096,128,7168,256,8],kernel='urma',build_hash='same',
                        diagnostics_hash='diag',ll_hook_workload='immediate')
                    (node/'setup.json').write_text(json.dumps(dict(error=None,fingerprint=fingerprint)))
                    (node/'host.json').write_text(json.dumps(dict(host='master',boot_id='boot')))
                    (case/'status.json').write_text(json.dumps(dict(rc=0,checks=dict(ok=True))))
                    (case/'sample_summary.jsonl').write_text('{}\n')
                    rows=[dict(label=label,mode='low_latency',index=i,used=i>0,us=value if not instrumented else 99999) for label in ('ll_no_hook_dispatch_combine','ll_immediate_hook_dispatch_combine') for i in range(4)]
                    (case/'api_samples.jsonl').write_text('\n'.join(json.dumps(r) for r in rows))
            with redirect_stdout(io.StringIO()):runs,comparison=summarize([root],root/'summary')
            self.assertEqual(len(runs),12)
            self.assertEqual(comparison[0]['strong_runs'],2)
            self.assertEqual(comparison[0]['relax_change_pct'],-90)
            failed=root/'diag_relax/node0/low_latency/status.json'
            failed.write_text(json.dumps(dict(rc=1,checks=dict(ok=False))))
            with self.assertRaisesRegex(ValueError,'failed case'):summarize([root],root/'summary')


if __name__=='__main__':unittest.main()
