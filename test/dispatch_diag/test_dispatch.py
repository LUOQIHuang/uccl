import ast
import argparse
from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
import re
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock,patch

HERE=Path(__file__).resolve().parent;REPO=HERE.parents[1]
sys.path.insert(0,str(HERE));sys.path.append(str(HERE.parent/'urma_mode_parity'))
from dispatch_checks import validate_gpu,REQUIRED,LABELS,SITES
from summarize import summarize,compare,REQUIRED_CHECKS
from summarize_compiled_off import summarize as summarize_off
from phase_runtime import Measurements
from phase_support import distribution

def dump(p,d):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(d))
def lines(p,data):p.parent.mkdir(parents=True,exist_ok=True);p.write_text('\n'.join(map(json.dumps,data)))
def windows(samples=10,mode='dispatch'):
    return [dict(label=l,index=i,version=2,dispatch_trace_version=1,storage='device_private_slots',publication_correlation=False,mode=mode,clock_domain='per_thread_clock64_delta',nominal_clock_khz=1000000,
        records=[] if mode=='bypass' else [dict(block=0,warp=0,sample_lane=0,stage=s,calls=1,sum_cycles=0 if mode=='dispatch_access' else 1000,max_cycles=0 if mode=='dispatch_access' else 1000) for s in ({46} if mode=='dispatch_clock' else REQUIRED)]) for l in sorted(LABELS) for i in range(samples)]

class DispatchTests(unittest.TestCase):
    def test_compiled_off_real_parsers_and_module_guards(self):
        for backend in ('urma','verbs'):
            folder=HERE.parent/('urma_mode_parity' if backend=='urma' else 'verbs_baseline')
            source=ast.parse((folder/'driver.py').read_text())
            names={'parser','preflight','build_flags'}
            nodes=[n for n in source.body if isinstance(n,ast.FunctionDef) and n.name in names or isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='BUILD_FLAGS' for t in n.targets)]
            ns=dict(argparse=argparse,Path=Path,os=os,re=re,HERE=folder,sys=sys,json=json)
            exec(compile(ast.Module(body=nodes,type_ignores=[]),'actual_'+backend+'_driver','exec'),ns)
            argv=['driver','--job','off','--node-rank','0','--mode','normal','--dispatch-focus','--dispatch-compiled-off']
            argv+=['--phase-suite','--api-only'] if backend=='urma' else ['--variant','current','--no-profile','--repeats','1']
            with patch.object(sys,'argv',argv):args=ns['parser']()
            self.assertFalse(args.gpu_stages)
            for extra in (['--gpu-stages'],['--action','build']):
                with patch.object(sys,'argv',argv+extra),patch('sys.stderr',io.StringIO()),self.assertRaises(SystemExit):ns['parser']()
            metadata=dict(gpu_stage_version=2,dispatch_trace_version=1,phase_diagnostics=True,loaded_libraries=[])
            flags={'UCCL_GPU_STAGE_TRACE':'1'} if backend=='urma' else ns['build_flags'](args)
            manifest=dict(flags=flags,sources={'a':'same'},module='ep.so',module_sha='sha')
            ns['environment']=lambda args:{}
            ns['subprocess']=NS(run=lambda *a,**kw:NS(returncode=0,stdout=('MODE_PARITY_IMPORT_OK ' if backend=='urma' else 'VERBS_IMPORT_OK ')+json.dumps(metadata),stderr=''))
            if backend=='urma':
                ns['support']=NS(read_build=lambda p:(Path('ep.so'),manifest))
                ns['urma_support']=NS(library_manifest=lambda p:{},hardware_check=lambda a:{})
                ns['preflight'](args)
                args.dispatch_compiled_off=False
                with self.assertRaisesRegex(RuntimeError,'TRACE_OFF_BUILD'):ns['preflight'](args)
                args.dispatch_compiled_off=True
            else:
                ns['common']=NS(sources=lambda:manifest['sources'],sha=lambda p:'sha',digest=lambda p:'hash')
                with patch.object(Path,'read_text',return_value=json.dumps(manifest)):ns['preflight'](args)
            metadata['gpu_stage_version']=0
            with patch.object(Path,'read_text',return_value=json.dumps(manifest)),self.assertRaisesRegex(RuntimeError,'COMPILED_OFF_REQUIRES_TRACE_BUILD'):ns['preflight'](args)

    def test_compiled_off_adapters_do_not_enter_capture_or_add_sample_barriers(self):
        with tempfile.TemporaryDirectory() as tmp,redirect_stdout(io.StringIO()):
            t,e=self.fake_gpu();dist=NS(barrier=Mock())
            m=Measurements(t,dist,e,Path(tmp),'normal',False,1,50);m.dispatch_focus=True
            m.api(Mock(),'dispatch_fp8_chunk32')
            e.gpu_stage_begin.assert_not_called();e.gpu_stage_end.assert_not_called()
            self.assertEqual(dist.barrier.call_count,2)
            self.assertFalse((Path(tmp)/'gpu_stages.jsonl').exists())
            source=ast.parse((HERE.parent/'verbs_baseline/worker.py').read_text())
            fn=next(n for n in source.body if isinstance(n,ast.FunctionDef) and n.name=='measure')
            dist.barrier.reset_mock();out=[]
            ns=dict(torch=t,ep=e,dist=dist,warmups=1,samples=50,cpu_diag=False,dispatch_focus=True,gpu_stages=False,timeline=False,
                os=os,time=__import__('time'),json=json,mode='normal',profile=False,common=NS(phase_support=NS(distribution=distribution)),append=lambda name,data:out.append((name,data)))
            exec(compile(ast.Module(body=[fn],type_ignores=[]),'actual_worker','exec'),ns)
            ns['measure'](Mock(),'dispatch_fp8_chunk32')
            e.gpu_stage_begin.assert_not_called();e.gpu_stage_end.assert_not_called()
            self.assertEqual(dist.barrier.call_count,3)
            self.assertNotIn('gpu_stages.jsonl',{name for name,data in out})

    def test_coverage_rejects_layout_only_partial_duplicate_and_untimed(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            for mode in ['bypass','dispatch_access','dispatch_clock','dispatch']:
                lines(p/'gpu_stages.jsonl',windows(3,mode));self.assertTrue(validate_gpu(p,3,mode))
            data=windows(3);data[0]['label']='layout';lines(p/'gpu_stages.jsonl',data)
            with self.assertRaisesRegex(ValueError,'Dispatch windows'):validate_gpu(p,3,'dispatch')
            for mutation in [lambda w:w['records'].pop(),lambda w:w['records'].append(w['records'][0]),lambda w:w.update(dispatch_trace_version=0)]:
                data=windows(3);mutation(data[0]);lines(p/'gpu_stages.jsonl',data)
                with self.assertRaises(ValueError):validate_gpu(p,3,'dispatch')

    def fake_gpu(self):
        class Event:
            def __init__(self,**kw):pass
            def record(self):pass
            def elapsed_time(self,e):return 1
        torch=NS(cuda=NS(synchronize=Mock(),current_device=lambda:0,Event=Event),empty=lambda *a,**kw:NS(zero_=Mock()),int=0)
        ep=NS(urma_phase_snapshot=lambda:'{"records":[]}',urma_phase_enable=Mock(),gpu_stage_begin=Mock(),gpu_stage_end=Mock(return_value='{"records":[]}'),urma_timeline_begin=Mock(side_effect=AssertionError('CPU tracing entered')),verbs_timeline_begin=Mock(side_effect=AssertionError('CPU tracing entered')))
        return torch,ep

    def test_real_urma_adapter_records_only_dispatch_without_cpu_timeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            t,e=self.fake_gpu();m=Measurements(t,NS(barrier=Mock()),e,Path(tmp),'normal',False,1,3,gpu_rings=lambda:[1]);m.dispatch_focus=True
            with redirect_stdout(io.StringIO()):
                for label in ['layout','dispatch_fp8_chunk32','dispatch_bf16_chunk32','combine_chunk12','combine_chunk16']:m.api(Mock(),label)
            self.assertEqual(e.gpu_stage_begin.call_count,6);e.urma_timeline_begin.assert_not_called()
            self.assertEqual({json.loads(x)['label'] for x in (Path(tmp)/'gpu_stages.jsonl').read_text().splitlines()},LABELS)

    def test_real_verbs_adapter_records_only_dispatch_without_cpu_timeline(self):
        source=(HERE.parent/'verbs_baseline/worker.py').read_text();fn=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='measure')
        t,e=self.fake_gpu();out=[]
        ns=dict(torch=t,ep=e,dist=NS(barrier=Mock()),warmups=1,samples=3,cpu_diag=False,dispatch_focus=True,gpu_stages=True,timeline=False,
            os=os,time=__import__('time'),json=json,label='',mode='normal',profile=False,common=NS(phase_support=NS(distribution=distribution)),stage_rings=lambda:[1],append=lambda name,data:out.append((name,data)))
        with patch.dict(os.environ,{'VB_GPU_STAGE_MODE':'dispatch'}),redirect_stdout(io.StringIO()):
            exec(compile(ast.Module(body=[fn],type_ignores=[]),'actual_worker','exec'),ns)
            for label in ['layout','dispatch_fp8_chunk32','dispatch_bf16_chunk32','combine_chunk12','combine_chunk16']:ns['measure'](Mock(),label)
        self.assertEqual(e.gpu_stage_begin.call_count,6);e.verbs_timeline_begin.assert_not_called()
        self.assertEqual({row['label'] for name,row in out if name=='gpu_stages.jsonl'},LABELS)

    def fixture(self,root,backend):
        paths=[]
        for tag,on in [('base',False),('trace',True),('post',False)]:
            job=root/tag;paths.append(job)
            for rank in (0,1):
                node=job/f'node{rank}';case=node/('normal' if backend=='urma' else 'r01_baseline/normal')
                c=dict(backend=backend,mode='normal',dispatch_focus=True,gpu_stages=on,gpu_stage_mode='dispatch',warmups=5 if on else 50,samples=10 if on else 50,normal_tokens=4096,shape=[4096,128,7168,256,8],kernel=backend,api_only=True,normal_completion='all',normal_write_order='default',ll_write_order='default',variant='current',no_profile=True,repeats=1,libraries={'sdk':'hash'})
                rt=dict(dispatch_trace_version=1,queue_backend='fifo',gpu_stage_version=2 if on else 0,module_sha=backend+('_trace' if on else '_base'),torch='same',selected_runtime_hashes={'libmcruntime.so':'same'},selected_library_hashes={'libmcruntime.so':'same'},normal_write_order='relax')
                flags=dict(UCCL_GPU_STAGE_TRACE='1' if on else '0',USE_URMA='1' if backend=='urma' else '0',USE_METAX='1',USE_DMABUF='1',MAX_NUM_GPUS='1',NUM_MAX_NVL_PEERS='1',PER_EXPERT_BATCHING='0')
                dump(node/'setup.json',dict(error=None,fingerprint=c));dump(node/'host.json',dict(host='host'+str(rank),boot_id='boot'))
                dump(node/'dispatch_sites.json',SITES)
                dump(case/'runtime.json',rt);dump(case/'status.json',dict(rc=0,checks={k:True for k in REQUIRED_CHECKS|{'dispatch_coverage'}}))
                dump(node/('build_manifest.json' if backend=='urma' else 'module_manifest.json'),dict(module_sha=rt['module_sha'],flags=flags,sources={'ep/src/internode.cu':'same'}))
                bench=node/('normal.py' if backend=='urma' else 'benchmark/test_internode.py');bench.parent.mkdir(parents=True,exist_ok=True);bench.write_text('# same frozen Normal benchmark')
                (case/'gpu_before.txt').write_text('Kernel Mode Driver Version: 3.10.14 |\nMACA Version: 3.7.0.38 |')
                raw=[dict(label=l,index=i,used=i>0,us=1010 if on else 1000,mode='normal') for l in ['layout','dispatch_fp8_chunk32','dispatch_bf16_chunk32','combine_chunk12','combine_chunk16'] for i in range(c['samples'])]
                lines(case/'api_samples.jsonl',raw);lines(case/'sample_summary.jsonl',[dict(label=l,mean_us=1010 if on else 1000) for l in {'layout','combine_chunk12','combine_chunk16'}|LABELS])
                if on:lines(case/'gpu_stages.jsonl',windows())
        return paths

    def test_complete_backend_summaries_compare_and_reject_runtime_drift(self):
        with tempfile.TemporaryDirectory() as tmp,redirect_stdout(io.StringIO()):
            root=Path(tmp)
            for backend in ['verbs','urma']:
                paths=self.fixture(root/backend,backend);api,scopes=summarize(*paths,root/backend/'summary')
                self.assertEqual(len(api),4);self.assertTrue(all(x['representative'] for x in api));self.assertTrue(scopes)
            compare(root/'verbs/summary',root/'urma/summary',root/'comparison')
            p=root/'urma/post/node1/normal/runtime.json';d=json.loads(p.read_text());d['selected_runtime_hashes']={'libmcruntime.so':'changed'};dump(p,d)
            with self.assertRaisesRegex(ValueError,'changed between'):summarize(root/'urma/base',root/'urma/trace',root/'urma/post',root/'bad')

    def test_compiled_off_summary_requires_disabled_capture_and_classifies_large_cost(self):
        with tempfile.TemporaryDirectory() as tmp,redirect_stdout(io.StringIO()):
            root=Path(tmp)
            for backend in ('urma','verbs'):
                b,t,p=self.fixture(root/backend,backend)
                for rank in (0,1):
                    node=t/f'node{rank}';case=node/('normal' if backend=='urma' else 'r01_baseline/normal')
                    c=json.loads((node/'setup.json').read_text());c['fingerprint'].update(gpu_stages=False,dispatch_compiled_off=True,warmups=50,samples=50);dump(node/'setup.json',c)
                    (case/'gpu_stages.jsonl').unlink()
                    raw=[dict(label=l,index=i,used=i>0,us=2000,mode='normal') for l in {'layout','combine_chunk12','combine_chunk16'}|LABELS for i in range(50)]
                    lines(case/'api_samples.jsonl',raw);lines(case/'sample_summary.jsonl',[dict(label=l,mean_us=2000) for l in {'layout','combine_chunk12','combine_chunk16'}|LABELS])
                data=summarize_off(b,t,p,root/backend/'summary_off')
                self.assertEqual(len(data),10);self.assertTrue(all(r['verdict']=='compiled_build_slower' for r in data))
                self.assertTrue(all(r['compiled_off_change_pct']==100 for r in data))
                lines(case/'gpu_stages.jsonl',windows(50,'bypass'))
                with self.assertRaisesRegex(ValueError,'instrumented'):summarize_off(b,t,p,root/'bad')

if __name__=='__main__':unittest.main()
