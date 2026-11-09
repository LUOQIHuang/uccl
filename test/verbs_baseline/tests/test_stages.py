import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
HERE=Path(__file__).resolve().parents[1];sys.path.insert(0,str(HERE))
import common
import driver
from summarize_stage_breakdown import summarize,LABELS,SITES
from compare_stage_breakdown import compare

def dump(path,data):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(data))
def lines(path,data):path.write_text('\n'.join(json.dumps(r) for r in data))

def fixture(root,backend):
    for job in ('r01','r02','r03','diagnostic'):
        diag=job=='diagnostic';n=5 if diag else 50
        for rank in (0,1):
            node=root/job/f'node{rank}';case=node/('cpu_diagnostic/normal' if diag else 'r01_baseline/normal') if backend=='verbs' else node/'normal'
            case.mkdir(parents=True)
            flags=dict(driver.BUILD_FLAGS,UCCL_GPU_STAGE_TRACE=str(int(diag)));flags['USE_URMA']=str(int(backend=='urma'))
            c=dict(job=job,mode='normal',variant='current',stage_suite=True,backend=backend,api_only=True,normal_completion='all',dispatch_trial=False,gpu_stages=diag,cpu_diagnostics=diag,stage_profile=diag,timeline=diag,warmups=n,samples=n,kernel=backend+'_kernel',libraries={},torch='t',no_profile=True,profile_only=False,repeats=1,benchmark={},diagnostics={},gid_index=3,hca='roce',gpu='0',ifname='eth',shape=[4096,128,7168,256,8],phase_suite=True,save_traces=False,ll_write_order='default')
            manifest=dict(sources={'ep/src/internode.cu':'same'},sdk={},flags=flags)
            dump(node/'setup.json',dict(error=None,fingerprint=c));dump(node/'host.json',dict(boot_id=str(rank),host='host'+str(rank)))
            dump(node/('module_manifest.json' if backend=='verbs' else 'build_manifest.json'),manifest)
            dump(node/('runtime.json' if backend=='verbs' else 'normal/runtime.json'),dict(queue_backend='fifo',gpu_stage_version=int(diag),loaded_libraries=[]))
            if backend=='verbs':
                (node/'benchmark').mkdir();(node/'benchmark/test_internode.py').write_text('same benchmark')
            else:(node/'normal.py').write_text('same benchmark')
            dump(case/'status.json',dict(rc=0,checks={'done':True}));dump(case/'config.json',dict(shape=dict(tokens=4096,hidden=7168,experts=256,topk=8)))
            (node/'gpu_stage_sites.json').write_bytes(SITES.read_bytes())
            lines(case/'api_samples.jsonl',[dict(label=l,mode='normal',index=i,used=i>0,us=100 if backend=='verbs' else 120) for l in LABELS for i in range(n)])
            stats=common.phase_support.distribution([100 if backend=='verbs' else 120]*(n-1))
            lines(case/'sample_summary.jsonl',[dict(label=l,profile=False,cpu_diagnostics=diag,enabled=diag,warmups=n,measured=n,**stats) for l in LABELS])
            phases=[dict(label=l,records=[],iterations=n) for l in LABELS]
            if backend=='urma' or diag:lines(case/('verbs_phases.jsonl' if backend=='verbs' else 'phases.jsonl'),phases)
            if not diag:continue
            gpu=[];trace=[]
            for label in LABELS:
                for i in range(n):
                    gpu.append(dict(version=1,label=label,index=i,clock_domain='per_thread_clock64_delta',nominal_clock_khz=1000,records=[dict(ring=0,stage=k,sample_lane=0,calls=1,sum_cycles=100,max_cycles=100) for k in (40,41,42,46,48)]))
                    events=[dict(ns=(j+1)*100,wr_id=1,proxy=0,qp=1,stage=s,value=0) for j,s in enumerate(('collect','prepare','post_begin','post_end','cqe','ack_slot'))]
                    events.append(dict(ns=250,wr_id=1,proxy=0,qp=0,stage='gpu_publish_clock',value=1000))
                    trace.append(dict(label=label,index=i,origin_ns=0,dropped=0,events=events))
            lines(case/'gpu_stages.jsonl',gpu);lines(case/(backend+'_timeline.jsonl'),trace)

class StageTests(unittest.TestCase):
    def test_two_builds_and_clock_windows_are_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);fixture(root,'verbs');gpu,trace=summarize(root,root/'summary')
            self.assertEqual(len(gpu),250);self.assertEqual(len(trace),50)
            p=root/'diagnostic/node0/module_manifest.json';m=json.loads(p.read_text());m['flags']['UCCL_GPU_STAGE_TRACE']='0';dump(p,m)
            with self.assertRaisesRegex(ValueError,'build/backend'):summarize(root,root/'summary')
    def test_cross_kernel_comparison_and_source_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);v=root/'verbs';u=root/'urma';fixture(v,'verbs');fixture(u,'urma')
            timing=compare(v,u,root/'summary');self.assertEqual(len(timing),10)
            self.assertTrue(all(abs(r['urma_slower_pct']-20)<1e-9 for r in timing))
            for folder,filename,digest in ((v,'module_manifest.json','b7784cfde6f79109eb92685be8aa0bf1518e2aa33798b5fc7cf85dfdefd6f64c'),(u,'build_manifest.json','25620b7c4883940cf3550be152f2a3ca9a2523768a8c417f4a2b36ad6265540a')):
                for p in folder.glob('*/node*/'+filename):
                    m=json.loads(p.read_text());m['sources']['ep/include/verbs_stage_diag.hpp']=digest;dump(p,m)
            self.assertEqual(len(compare(v,u,root/'summary')),10)
            for p in u.glob('*/node*/build_manifest.json'):
                m=json.loads(p.read_text());m['sources']['ep/src/internode.cu']='different';dump(p,m)
            with self.assertRaisesRegex(ValueError,'sources changed'):compare(v,u,root/'summary')
    def test_parser_rejects_accidental_profiler_and_old_build(self):
        good=['driver','--job','stage','--node-rank','0','--variant','current','--mode','normal','--no-profile','--repeats','1','--stage-suite','--cpu-diagnostics','--timeline','--gpu-stages']
        with patch.object(sys,'argv',good):self.assertEqual(driver.build_flags(driver.parser())['UCCL_GPU_STAGE_TRACE'],'1')
        with patch.object(sys,'argv',[x for x in good if x!='--no-profile']):
            with self.assertRaises(SystemExit):driver.parser()
        with patch.object(sys,'argv',[x for x in good if x!='--cpu-diagnostics']):
            with self.assertRaises(SystemExit):driver.parser()

if __name__=='__main__':unittest.main()
