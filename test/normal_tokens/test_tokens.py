"""CPU-only tests: command plan, failure handling and raw-data acceptance."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import statistics
import tempfile
import unittest
from unittest.mock import patch

import plan
import runner
import summarize


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def lines(path, values):
    path.write_text(''.join(json.dumps(r)+'\n' for r in values))


def fixture(root):
    """Synthetic files only in a temporary directory; never measurement data."""
    job = root.name
    for backend in ('verbs','urma'):
        for rank in (0,1):
            metadata = dict(version=1, job=job, backend=backend, node_rank=rank, tokens=list(plan.TOKENS),
                warmups=10, samples=31, repeats=3, cases=plan.cases(job,backend), cpu_affinity='inherited',
                capture='inactive-paced', script_hashes={n:'fixture-sha' for n in ('plan.py','runner.py','run.sh','summarize.py')},
                dependency_hashes={n:'fixture-sha' for n in plan.DEPENDENCIES},
                expected_kernel=plan.KERNELS[backend], master_addr='202',port=33780,gid_index=3,
                hca='roce',ifname='eth',urma_device='udma')
            dump(root/backend/('plan_node%d.json'%rank),metadata)
        for point in plan.cases(job,backend):
            for rank in (0,1):
                node = root/backend/'cases'/point['job']/('node%d'%rank)
                case = node/('normal' if backend=='urma' else 'r01_baseline/normal')
                case.mkdir(parents=True)
                c = dict(job=point['job'],backend=backend,mode='normal',dispatch_focus=True,reference_events='paced',
                    warmups=10,samples=31,kernel=plan.KERNELS[backend],libraries={'fixture-library':backend},
                    shape=[point['tokens'],128,7168,256,8],normal_tokens=point['tokens'],api_only=True,
                    normal_completion='all',normal_write_order='default',ll_write_order='default',normal_fadd_fence_plan='all-on',
                    normal_retire_plan='ordered',variant='current',no_profile=True,repeats=1,ifname='eth',gpu='0',
                    diagnostics_hash='fixture-diagnostics')
                dump(node/'setup.json',dict(error=None,fingerprint=c))
                dump(node/'dispatch_sites.json',summarize.base.SITES)
                dump(node/'host.json',dict(host='host%d'%rank,boot_id='boot%d'%rank))
                manifest=dict(module_sha='%s-module%d'%(backend,rank),sources={'ep/a.cpp':'fixture-source'},
                              flags={'USE_URMA':'1' if backend=='urma' else '0','UCCL_GPU_STAGE_TRACE':'0','UCCL_REFERENCE_EVENTS':'1'})
                dump(node/('build_manifest.json' if backend=='urma' else 'module_manifest.json'),manifest)
                bench = node/('normal.py' if backend=='urma' else 'benchmark/test_internode.py')
                bench.parent.mkdir(parents=True,exist_ok=True);bench.write_text('# same frozen fixture benchmark\n')
                runtime=dict(module_sha=manifest['module_sha'],queue_backend='fifo',gpu_stage_version=0,
                    dispatch_trace_version=1,reference_events_version=1,normal_max_inflight=8,normal_fifo_retire='ordered',
                    normal_write_order='relax',normal_fadd_fence_version=1,normal_fadd_fence='on',torch='fixture-torch',
                    selected_runtime_hashes={'libmcruntime.so':'same'},selected_library_hashes={'libmcruntime.so':'same'})
                dump(case/'runtime.json',runtime)
                dump(case/'status.json',dict(rc=0,checks={k:True for k in summarize.base.REQUIRED_CHECKS|{'reference_control','normal_fadd_fence'}}))
                (case/'gpu_before.txt').write_text('Kernel Mode Driver Version: fixture-kmd\nMACA Version: fixture-maca\n')
                (case/'bench.log').write_text('[URMA_FADD_ROLE] role=tail fence=on order=strong\n')
                dump(case/'config.json',dict(shape=dict(tokens=point['tokens'],hidden=7168,experts=256,topk=8),env={'UCCL_EP_DISABLE_CPU_AFFINITY':'1'}))
                raw=[];saved=[]
                for label in sorted(summarize.base.ALL_LABELS):
                    value=point['tokens']*(1 if backend=='verbs' else 1.1)+rank*7+point['repeat']
                    values=[value+i*.01 for i in range(31)]
                    raw.extend(dict(label=label,mode='normal',index=i,used=i>0,us=v) for i,v in enumerate(values))
                    saved.append(dict(label=label,mean_us=statistics.mean(values[1:])))
                lines(case/'api_samples.jsonl',raw);lines(case/'sample_summary.jsonl',saved)
                markers=[dict(label=l,mode='paced',warmups=10,samples=31,gpu_events=False,cpu_events=False,host_clock=False)
                         for l in ('dispatch_fp8_chunk32','dispatch_bf16_chunk32')]
                lines(case/'reference_control.jsonl',markers)


class RunnerTests(unittest.TestCase):
    def args(self,root,backend):
        return ['--backend',backend,'--node-rank','0','--job','sweep','--output',str(root/'out'),
                '--destination',str(root/'collected'),'--build-dir',str(root/'build')]

    def test_plan_and_real_driver_parsers_accept_every_point(self):
        for backend,relative in [('verbs','verbs_baseline'),('urma','urma_mode_parity')]:
            directory=runner.HERE.parent/relative
            # Import each driver in a fresh Python process: their legacy module
            # names intentionally overlap and must not contaminate each other.
            with tempfile.TemporaryDirectory() as temporary:
                a=runner.arguments(self.args(Path(temporary),backend))
                points=plan.cases(a.job,backend)
                self.assertEqual(len(points),21)
                for repeat in (1,2,3):
                    self.assertEqual({p['tokens'] for p in points if p['repeat']==repeat},set(plan.TOKENS))
                for point in points:
                    cmd=runner.driver_command(a,'run',point)
                    self.assertEqual(cmd[cmd.index('--samples')+1],'31')
                    self.assertEqual(cmd[cmd.index('--reference-events')+1],'paced')
                    if backend=='urma':self.assertIn('all-on',cmd)
                cmd=runner.driver_command(a,'check',points[0])
                # Driver parser names are verified without loading torch/GPU.
                script="import sys;sys.path.insert(0,sys.argv.pop(1));import driver;sys.argv=[sys.argv[0]]+sys.argv[1:];driver.parser()"
                for tokens in plan.TOKENS:
                    args=cmd[2:];args[args.index('--normal-tokens')+1]=str(tokens)
                    result=runner.subprocess.run([runner.sys.executable,'-c',script,str(directory)]+args,
                                                 stdout=runner.subprocess.PIPE,stderr=runner.subprocess.STDOUT,universal_newlines=True)
                    self.assertEqual(result.returncode,0,result.stdout)
                cmd=runner.driver_command(a,'build',points[0])
                result=runner.subprocess.run([runner.sys.executable,'-c',script,str(directory)]+cmd[2:],
                                             stdout=runner.subprocess.PIPE,stderr=runner.subprocess.STDOUT,universal_newlines=True)
                self.assertEqual(result.returncode,0,result.stdout)

    def test_failure_stops_after_collection(self):
        with tempfile.TemporaryDirectory() as temporary,redirect_stdout(io.StringIO()):
            root=Path(temporary)
            with patch.object(runner,'execute',side_effect=[0,7]) as execute,patch.object(runner,'collect_case') as collect:
                with self.assertRaises(SystemExit) as error:
                    runner.main(self.args(root,'urma')+['--action','run'])
                self.assertEqual(error.exception.code,7)
                self.assertEqual(execute.call_count,2)
                self.assertEqual(collect.call_count,1)

    def test_changed_plan_rejected_without_running(self):
        with tempfile.TemporaryDirectory() as temporary:
            a=runner.arguments(self.args(Path(temporary),'urma'))
            runner.record_plan(a)
            a.gid_index=4
            with self.assertRaisesRegex(ValueError,'PLAN_CHANGED'):runner.record_plan(a)


class SummaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)/'sweep';fixture(self.root)

    def first(self,backend='urma'):
        point=plan.cases('sweep',backend)[0]
        node=self.root/backend/'cases'/point['job']/'node0'
        return node,node/('normal' if backend=='urma' else 'r01_baseline/normal')

    def test_full_comparison_and_single_backend(self):
        with redirect_stdout(io.StringIO()):
            rows,comparison=summarize.summarize(self.root,self.root/'compare')
            self.assertEqual(len(rows),210);self.assertEqual(len(comparison),105)
            self.assertTrue(all(r['n']==90 and r['stable'] for r in rows))
            selected=next(r for r in comparison if r['node']=='0' and r['tokens']==64 and r['label']=='dispatch_bf16_chunk32')
            self.assertAlmostEqual(selected['urma_minus_verbs_us'],6.4)
            summarize.summarize(self.root,self.root/'only_urma',('urma',))
            self.assertFalse((self.root/'only_urma/comparison.csv').exists())

    def test_raw_missing_sample_rejected(self):
        node,case=self.first();path=case/'api_samples.jsonl'
        path.write_text('\n'.join(path.read_text().splitlines()[1:]))
        with self.assertRaisesRegex(ValueError,'incomplete Normal samples'):
            summarize.load_case(node.parent,0,'urma',64)

    def test_wrong_tokens_and_active_capture_rejected(self):
        node,case=self.first();p=node/'setup.json';data=json.loads(p.read_text())
        data['fingerprint']['shape'][0]=128;dump(p,data)
        with self.assertRaisesRegex(ValueError,'fixed 64 shape'):summarize.load_case(node.parent,0,'urma',64)
        data['fingerprint']['shape'][0]=64;dump(p,data)
        (case/'event_trace.jsonl').write_text('{}\n')
        with self.assertRaisesRegex(ValueError,'unexpected reference events'):summarize.load_case(node.parent,0,'urma',64)

    def test_fence_change_rejected(self):
        node,case=self.first();p=case/'runtime.json';data=json.loads(p.read_text());data['normal_fadd_fence']='off';dump(p,data)
        with self.assertRaisesRegex(ValueError,'FADD fence'):summarize.load_case(node.parent,0,'urma',64)

    def test_boot_drift_rejected(self):
        point=plan.cases('sweep','urma')[-1];p=self.root/'urma/cases'/point['job']/'node0/host.json'
        data=json.loads(p.read_text());data['boot_id']='new-boot';dump(p,data)
        with self.assertRaisesRegex(ValueError,'boot/library changed'):
            summarize.collect_backend(self.root,'urma')

    def test_default_historical_gate_not_relaxed(self):
        node,_=self.first()
        with self.assertRaisesRegex(ValueError,'fixed 4096 shape'):
            summarize.base.case_data(node.parent,0,False,reference_events='paced')


if __name__=='__main__':
    unittest.main()
