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

HERE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(HERE))
from phase_support import distribution
from summarize_normal_order import summarize,LABELS,KERNEL,REQUIRED_CHECKS
spec=importlib.util.spec_from_file_location('normal_order_driver',HERE/'driver.py')
driver=importlib.util.module_from_spec(spec);spec.loader.exec_module(driver)

class NormalOrderTests(unittest.TestCase):
    def parse(self,extra):
        with patch.object(sys,'argv',['driver','--job','ab','--node-rank','0']+extra):return driver.parser()

    def test_order_arguments_and_environment_are_explicit(self):
        base=['--mode','normal','--phase-suite','--api-only','--normal-write-order','strong']
        a=self.parse(base)
        with patch.dict(os.environ,{'UCCL_URMA_NORMAL_WRITE_ORDER':'relax','UCCL_URMA_LL_WRITE_ORDER':'relax'}):
            env=driver.environment(a)
        self.assertEqual(env['UCCL_URMA_NORMAL_WRITE_ORDER'],'strong')
        self.assertEqual(env['UCCL_URMA_LL_WRITE_ORDER'],'default')
        for extra in [['--mode','low_latency'],['--mode','both'],['--normal-completion','paired'],['--action','micro'],['--action','probe']]:
            with patch('sys.stderr',io.StringIO()),self.assertRaises(SystemExit):self.parse(base+extra)
        with patch('sys.stderr',io.StringIO()),self.assertRaises(SystemExit):self.parse(['--mode','normal','--normal-write-order','strong'])

    def test_preflight_rejects_old_or_wrong_policy_module(self):
        a=self.parse(['--mode','normal','--phase-suite','--api-only','--normal-write-order','strong'])
        for value in [None,'relax','strong']:
            metadata=dict(phase_diagnostics=True,normal_write_order=value)
            probe=SimpleNamespace(returncode=0,stdout='MODE_PARITY_IMPORT_OK '+json.dumps(metadata),stderr='')
            with patch.object(driver.support,'read_build',return_value=(Path('module.so'),{})),patch.object(driver.urma_support,'library_manifest',return_value={}),patch.object(driver.urma_support,'hardware_check',return_value={}),patch.object(driver.subprocess,'run',return_value=probe):
                if value=='strong':self.assertEqual(driver.preflight(a)[2]['runtime']['normal_write_order'],'strong')
                else:
                    with self.assertRaisesRegex(RuntimeError,'NORMAL_WRITE_ORDER_DIAGNOSTICS_REQUIRES_REBUILD'):driver.preflight(a)

    def fixture(self,root):
        for r in (1,2,3):
            for order in ('relax','strong'):
                for rank in (0,1):
                    name=f'ab_r{r:02d}_{order}';node=root/name/f'node{rank}';case=node/'normal';case.mkdir(parents=True)
                    c=dict(job=name,backend='urma',mode='normal',kernel=KERNEL,phase_suite=True,api_only=True,stage_profile=False,save_traces=False,
                        normal_write_order=order,ll_write_order='default',normal_completion='all',shape=[4096,128,7168,256,8],warmups=50,samples=50,build_hash='build',libraries={'runtime':'same'},runtime_libraries={'libmcruntime.so':'same'},torch='torch',diagnostics_hash='diag')
                    rt=dict(normal_write_order=order,ll_write_order='strong',normal_completion='all',gpu_stage_version=0,module_sha='module'+str(rank),torch='torch',queue_backend='fifo',selected_runtime_hashes={'libmcruntime.so':'same'})
                    (case/'gpu_before.txt').write_text('| Kernel Mode Driver Version: 3.8.23 |\n| MACA Version: 3.7.0.38 |\n')
                    for path,data in [(node/'setup.json',dict(error=None,fingerprint=c)),(node/'preflight.json',dict(runtime=rt,hardware={'atomic_control':'0060'})),
                        (node/'build_manifest.json',dict(module_sha=rt['module_sha'],flags={'USE_URMA':'1','UCCL_GPU_STAGE_TRACE':'0'})),
                        (node/'host.json',dict(host='host'+str(rank),boot_id='boot')), (case/'runtime.json',rt),(case/'status.json',dict(rc=0,checks={k:True for k in REQUIRED_CHECKS}))]:path.write_text(json.dumps(data))
                    raw=[];saved=[]
                    for label in sorted(LABELS):
                        value=(100 if order=='relax' else 90)+r
                        samples=[dict(label=label,mode='normal',index=i,used=i>0,us=value+i*.001) for i in range(50)]
                        raw.extend(samples);saved.append(dict(label=label,**distribution([x['us'] for x in samples[1:]])))
                    (case/'api_samples.jsonl').write_text('\n'.join(map(json.dumps,raw)))
                    (case/'sample_summary.jsonl').write_text('\n'.join(map(json.dumps,saved)))

    def test_real_summary_and_reject_mixed_or_incomplete_experiments(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.fixture(root)
            with redirect_stdout(io.StringIO()):runs,comparison=summarize(root,'ab',root/'summary')
            self.assertEqual(len(runs),60);self.assertEqual(len(comparison),10)
            self.assertTrue(all(x['strong_change_pct']<0 and x['paired_direction_consistent'] for x in comparison))
            self.assertAlmostEqual(comparison[0]['strong_us'],92.025)
            paths=[root/'ab_r03_strong/node1/normal/runtime.json',root/'ab_r03_strong/node1/host.json',root/'ab_r03_strong/node1/setup.json',root/'ab_r03_strong/node1/normal/status.json']
            updates=[lambda x:x.update(normal_write_order='relax'),lambda x:x.update(boot_id='other'),lambda x:x['fingerprint'].update(libraries={'runtime':'changed'}),lambda x:x.update(rc=1)]
            for p,update in zip(paths,updates):
                original=p.read_text();data=json.loads(original);update(data);p.write_text(json.dumps(data))
                with self.assertRaises(ValueError):summarize(root,'ab',root/'bad')
                p.write_text(original)
            p=paths[0];original=p.read_text();d=json.loads(original);d['selected_runtime_hashes']={'libmcruntime.so':'other'};p.write_text(json.dumps(d))
            with self.assertRaisesRegex(ValueError,'actual MACA runtime'):summarize(root,'ab',root/'bad')
            p.write_text(original)
            raw=root/'ab_r03_strong/node1/normal/api_samples.jsonl';original=raw.read_text()
            raw.write_text('\n'.join(original.splitlines()[:-1]))
            with self.assertRaisesRegex(ValueError,'incomplete timing'):summarize(root,'ab',root/'bad')
            raw.write_text(original)
            (root/'ab_r03_strong/node1/setup.json').unlink()
            with self.assertRaises(FileNotFoundError):summarize(root,'ab',root/'bad')

if __name__=='__main__':unittest.main()
