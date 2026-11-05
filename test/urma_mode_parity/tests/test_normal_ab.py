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
from unittest.mock import patch

HERE=Path(__file__).resolve().parents[1];sys.path.insert(0,str(HERE))
from summarize_normal_ab import summarize,LABELS
spec=importlib.util.spec_from_file_location('normal_driver',HERE/'driver.py')
driver=importlib.util.module_from_spec(spec);spec.loader.exec_module(driver)


class NormalTests(unittest.TestCase):
    def test_normal_policy_is_explicit_and_mode_guarded(self):
        base=['driver','--job','normal','--node-rank','0','--mode','normal']
        for policy in ('all','ordered','paired'):
            with patch.object(sys,'argv',base+['--normal-completion',policy]):args=driver.parser()
            with patch.dict(os.environ,{'UCCL_URMA_NORMAL_COMPLETION':'inherited'}):
                self.assertEqual(driver.environment(args)['UCCL_URMA_NORMAL_COMPLETION'],policy)
        for mode in ('low_latency','both'):
            with patch.object(sys,'argv',base+['--mode',mode,'--normal-completion','paired']),patch('sys.stderr',io.StringIO()):
                with self.assertRaises(SystemExit):driver.parser()

    def test_paired_preflight_rejects_module_without_capability(self):
        argv=['driver','--job','normal','--node-rank','0','--mode','normal','--normal-completion','paired']
        with patch.object(sys,'argv',argv):args=driver.parser()
        info=dict(phase_diagnostics=True,normal_completion=None)
        probe=SimpleNamespace(returncode=0,stdout='MODE_PARITY_IMPORT_OK '+json.dumps(info),stderr='')
        with patch.object(driver.support,'read_build',return_value=(Path('module.so'),{})), \
             patch.object(driver.urma_support,'library_manifest',return_value={}), \
             patch.object(driver.urma_support,'hardware_check',return_value={}),patch.object(driver.subprocess,'run',return_value=probe):
            with self.assertRaisesRegex(RuntimeError,'NORMAL_COMPLETION_DIAGNOSTICS_REQUIRES_REBUILD'):driver.preflight(args)

    def fixture(self,root,host='master',node_name='node0',ordered=False):
        policies=['all','paired']+(['ordered'] if ordered else [])
        for policy in policies:
            for tag,on in [('r01',False),('r02',False),('diagnostic',True)]:
                node=root/(tag+'_'+policy)/node_name;case=node/'normal';case.mkdir(parents=True)
                config=dict(job=tag+'_'+policy,mode='normal',api_only=True,normal_completion=policy,
                    ll_write_order='default',stage_profile=on,samples=4,warmups=50,shape=[4096,128,7168,256,8],
                    kernel='urma',build_hash='build',diagnostics_hash='diag')
                (node/'setup.json').write_text(json.dumps(dict(error=None,fingerprint=config)))
                (node/'host.json').write_text(json.dumps(dict(host=host,boot_id='boot')))
                (case/'status.json').write_text(json.dumps(dict(rc=0,checks=dict(ok=True))))
                value=1000 if on else dict(all=100,paired=90,ordered=110)[policy]
                samples=[dict(label=label,mode='normal',index=i,used=i>0,us=value) for label in sorted(LABELS) for i in range(4)]
                (case/'api_samples.jsonl').write_text('\n'.join(json.dumps(r) for r in samples))
                if on:
                    def metric(calls,items=0):return dict(calls=calls,items=items,ns=0)
                    metrics=dict(write_requests=metric(4,1024),fadd_requests=metric(2),
                        paired_write_candidates=metric(2),write_cqe_suppressed=metric(2 if policy=='paired' else 0),
                        observed_cqes=metric(4 if policy=='paired' else 6),retired_logical_ops=metric(4,6))
                    window=dict(label='dispatch_fp8_chunk32',iterations=2,records=[dict(metrics=metrics)])
                    (case/'phases.jsonl').write_text(json.dumps(window)+'\n')

    def test_summary_counts_logical_retirements_and_excludes_diagnostic_timings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.fixture(root,ordered=True);self.fixture(root,'worker1','node1',ordered=True)
            with redirect_stdout(io.StringIO()):runs,comparison,counts=summarize([root],root/'summary',2)
            self.assertEqual(len(comparison),10)
            self.assertTrue(all(abs(r['paired_change_pct']+10)<.0001 for r in comparison))
            self.assertTrue(all(r['all_runs']==2 and r['paired_runs']==2 and r['ordered_runs']==2 for r in comparison))
            self.assertTrue(all(r['retired_logical_ops_per_iteration']==3 for r in counts))
            self.assertTrue(all(r['physical_cqes_per_iteration']==2 for r in counts if r['policy']=='paired'))
            self.assertTrue(all(r['physical_cqes_per_iteration']==3 for r in counts if r['policy']=='all'))

    def test_summary_rejects_missing_repeat_and_partial_raw_samples(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.fixture(root)
            with self.assertRaisesRegex(ValueError,'expected 3'):summarize([root],root/'summary')
            raw=root/'r01_all/node0/normal/api_samples.jsonl'
            raw.write_text('\n'.join(raw.read_text().splitlines()[:-1]))
            with self.assertRaisesRegex(ValueError,'incomplete raw'):summarize([root],root/'summary',2)

    def test_summary_rejects_failed_setup_even_without_samples(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.fixture(root)
            path=root/'failed/node0/setup.json';path.parent.mkdir(parents=True)
            path.write_text(json.dumps(dict(error='setup failed',fingerprint={})))
            with self.assertRaisesRegex(ValueError,'failed setup'):summarize([root],root/'summary',2)

    def test_summary_rejects_candidate_not_exercised(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.fixture(root)
            path=root/'diagnostic_paired/node0/normal/phases.jsonl'
            data=json.loads(path.read_text());data['records'][0]['metrics']['write_cqe_suppressed']['calls']=0
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError,'not exercised'):summarize([root],root/'summary',2)


if __name__=='__main__':unittest.main()
