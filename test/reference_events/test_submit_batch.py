import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace as NS

from test_window_plan import driver_functions
from submit_witness import inspect


class SubmitBatchTests(unittest.TestCase):
    def arguments(self):
        return ['driver','--node-rank','1','--job','batch','--mode','normal','--phase-suite','--api-only',
                '--dispatch-focus','--reference-events','batch','--reference-profile','boundaries',
                '--normal-submit-schedule','writes-first','--normal-submit-witness',
                '--normal-fadd-fence-plan','peer-tail-off','--warmups','5','--samples','10']

    def test_scope_and_effective_environment(self):
        ns=driver_functions('urma')
        with patch.object(sys,'argv',self.arguments()):args=ns['parser']()
        self.assertEqual(ns['environment'](args)['PARITY_NORMAL_SUBMIT_WITNESS'],'1')
        self.assertEqual(ns['environment'](args)['UCCL_URMA_NORMAL_SUBMIT_SCHEDULE'],'writes-first')
        for extra in (['--reference-events','on'],['--normal-window-plan','both32'],
                      ['--normal-submit-schedule','tail-interleaved'],['--stage-profile'],['--action','build'],
                      ['--normal-fadd-fence-plan','all-on']):
            with patch.object(sys,'argv',self.arguments()+extra),patch('sys.stderr',io.StringIO()),self.assertRaises(SystemExit):ns['parser']()

    def test_sdk_chain_audit_rejects_mixed_candidate_and_truncation(self):
        def capture(mixed):
            events=[]
            for proxy in range(4):
                ops=[('payload_bytes',0,8192),('post_begin',0,0)]
                ops+=([('post_begin',1,34),('post_end',0,0),('post_end',1,0)] if mixed else
                      [('post_end',0,0),('post_begin',1,34),('post_end',1,0)])
                events.extend(dict(proxy=proxy,ns=proxy*10+i,stage=s,wr_id=w,value=v,qp=1) for i,(s,w,v) in enumerate(ops))
            return dict(version=1,label='dispatch_bf16_chunk32',after_timed_samples=True,purpose='submission_coverage_only',
                        cpu=dict(version=1,clock='CLOCK_MONOTONIC_RAW',heartbeat_stride=0,dropped=0,events=events))
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'w.json';path.write_text(json.dumps(capture(True)))
            self.assertTrue(all(r['mixed_payload_pct']==100 for r in inspect(path,'lane-mixed')))
            with self.assertRaisesRegex(ValueError,'still mixed'):inspect(path,'writes-first')
            data=capture(False);path.write_text(json.dumps(data))
            self.assertTrue(all(r['mixed_payload_pct']==0 for r in inspect(path,'writes-first')))
            data['cpu']['events'].pop();path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError,'truncated'):inspect(path,'writes-first')

    def test_witness_occurs_after_all_api_timing(self):
        directory=Path(__file__).resolve().parents[1]/'urma_mode_parity'
        sys.path.insert(0,str(directory))
        spec=importlib.util.spec_from_file_location('witness_phase_runtime',directory/'phase_runtime.py')
        mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);calls=[]
            torch=NS(int=object(),empty=lambda *a,**kw:NS(zero_=lambda:None),
                cuda=NS(synchronize=lambda:None,current_device=lambda:0,
                    Event=lambda **kw:NS(record=lambda:None,elapsed_time=lambda e:1.0)))
            ep=NS(urma_timeline_begin=lambda:calls.append(('begin',(root/'sample_summary.jsonl').exists())),
                  urma_timeline_end=lambda:'{"version":1,"events":[]}')
            measure=mod.Measurements(torch,NS(barrier=lambda:None),ep,root,'normal',False,1,3)
            measure.reference_control='batch';measure.window=lambda label,fn,count:fn()
            with patch.object(os,'environ',dict(os.environ,PARITY_NORMAL_SUBMIT_WITNESS='1')):
                measure.api(lambda:calls.append(('fn',None)),'dispatch_bf16_chunk32')
            self.assertEqual(calls.count(('fn',None)),5) # 1 warm + 3 timed + 1 extra
            self.assertEqual([v for name,v in calls if name=='begin'],[True])
            self.assertEqual(len((root/'api_samples.jsonl').read_text().splitlines()),3)
            self.assertTrue(json.loads((root/'submit_witness.json').read_text())['after_timed_samples'])

    def test_summary_requires_coverage_gain_and_round_stability(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tools/reference_events'))
        import summarize_submit as tool
        def case(root,rank,*args,**kw):
            schedule='writes-first' if root.name.endswith('writes_first') else 'lane-mixed'
            return dict(c=dict(job=root.name,normal_submit_schedule=schedule,normal_submit_witness=True,
                normal_fadd_fence_plan='peer-tail-off',normal_window_plan='default',normal_retire_plan='ordered'),
                identity=dict(backend='urma',reference_profile='boundaries',normal_submit_schedule=schedule,
                    source='same',boot='node%d'%rank),runtime=dict(normal_submit_schedule=schedule,module_sha='same'),
                case=root/('node%d'%rank)/'normal',means={label:dict(mean_us=1000,cv_pct=0) for label in tool.matched.base.ALL_LABELS})
        for scenario in ('gain','no_gain','drift','coverage'):
            def samples(path):
                candidate='writes_first' in str(path)
                value=900 if candidate and scenario!='no_gain' else 1000
                if scenario=='drift' and 'r02_lane_mixed' in str(path):value=1100
                return [dict(label=label,index=i,used=True,us=value) for label in tool.matched.base.ALL_LABELS for i in range(1,10)]
            def coverage(path,schedule):
                mixed=0 if schedule=='writes-first' or scenario=='coverage' else 50
                return [dict(proxy=i,payload_bytes=100,mixed_payload_bytes=mixed) for i in range(4)]
            with tempfile.TemporaryDirectory() as tmp,patch.object(tool.matched.base,'case_data',case), \
                 patch.object(tool.matched.base,'read',return_value={'checks':dict(normal_submit_schedule=True,normal_submit_witness=True)}), \
                 patch.object(tool.matched.base,'rows',samples),patch('submit_witness.inspect',coverage):
                result=tool.summarize(Path('data'),'submit',Path(tmp)/'out',cadence='batch')
            primary=next(r for r in result if r['label']=='dispatch_bf16_chunk32')
            self.assertEqual(primary['decision'],dict(gain='material_improvement',no_gain='no_material_change_keep_default',
                drift='unstable_keep_default',coverage='coverage_incomplete_keep_default')[scenario])


@unittest.skipUnless(os.name=='posix','Linux shell runner')
class SubmitBatchRunnerTests(unittest.TestCase):
    def setUp(self):
        from test_credit_ab import CreditRunnerTests
        self.f=CreditRunnerTests();self.f.setUp()
        self.addCleanup(self.f.fixture.temporary.cleanup)
        (self.f.fixture.root/'reference_events/run_fadd_fence.sh').write_text(Path(__file__).with_name('run_writes_first_batch.sh').read_text())
        fake=self.f.fixture.root/'urma_mode_parity/fake.py'
        fake.write_text(fake.read_text().replace("flags={'--phase-suite'","flags={'--normal-submit-witness','--phase-suite'"))

    def test_exact_abba_and_failed_candidate_collection(self):
        result=self.f.execute(fail='r01_writes_first')
        self.assertEqual(result.returncode,7,result.stdout)
        runs=[r for r in self.f.fixture.calls() if r['--action']=='run']
        self.assertEqual([r['--normal-submit-schedule'] for r in runs],['lane-mixed','writes-first'])
        self.assertTrue(all(r['--reference-events']=='batch' and '--normal-submit-witness' in r for r in runs))
        self.assertIn('r01_writes_first',(self.f.fixture.root/'out/collected.jsonl').read_text())

    def test_both_nodes_full_abba_and_no_overwrite(self):
        for rank in (0,1):
            result=self.f.execute(rank)
            self.assertEqual(result.returncode,0,result.stdout)
        runs=[r for r in self.f.fixture.calls() if r['--action']=='run']
        for rank in (0,1):
            local=[r for r in runs if r['--node-rank']==str(rank)]
            self.assertEqual([r['--normal-submit-schedule'] for r in local],['lane-mixed','writes-first','writes-first','lane-mixed'])
            self.assertEqual({r['--urma-device'] for r in local},{'udmac1d1e2' if rank==0 else 'udmac0d1e2'})
        before=len(self.f.fixture.calls())
        self.assertNotEqual(self.f.execute().returncode,0)
        self.assertEqual(len(self.f.fixture.calls()),before)


if __name__=='__main__':unittest.main()
