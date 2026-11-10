import json,re,subprocess,sys,tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
HERE=Path(__file__).resolve().parents[1];REPO=HERE.parents[1];sys.path.insert(0,str(HERE))
from phase_runtime import Measurements
from phase_support import distribution
from summarize_stage_breakdown import summarize
LABELS=['layout','dispatch_fp8_chunk32','dispatch_bf16_chunk32','combine_chunk12','combine_chunk16']
def mask(s):return re.sub(r'//[^\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'',lambda m:''.join('\n' if c=='\n' else ' ' for c in m.group()),s)
class GpuStageTests(unittest.TestCase):
    def test_annotations_preserve_original_kernel_statements(self):
        s=(REPO/'ep/src/internode.cu').read_text();s=re.sub(r'// GPU_STAGE_V2_LAUNCH_GUARD_BEGIN[\s\S]*?// GPU_STAGE_V2_LAUNCH_GUARD_END\n\n','',s);masked=mask(s);edits=[]
        pattern=r'\{ uccl::gpu_stages::Span gpu_stage_(\d+)\([^;]+;\n'
        matches=list(re.finditer(pattern,s));self.assertEqual(len(matches),24)
        for m in matches:
            depth=1;i=m.start()+1
            while depth:
                if masked[i]=='{':depth+=1
                if masked[i]=='}':depth-=1
                i+=1
            self.assertEqual(s[i-2:i],'\n}');edits.extend([(m.start(),m.end()),(i-2,i)])
        for start,end in sorted(edits,reverse=True):s=s[:start]+s[end:]
        s=re.sub(r'\n  uccl::gpu_stages::Span gpu_kernel_stage\([^;]+;', '', s)
        old=subprocess.check_output(['git','-C',str(REPO),'show','008dca1a:ep/src/internode.cu']).decode()
        self.assertEqual(s,old)
    def test_gpu_capture_every_sample_after_warmups(self):
        with tempfile.TemporaryDirectory() as tmp:
            class Event:
                def __init__(self,**kw):pass
                def record(self):pass
                def elapsed_time(self,other):return 1
            ep=SimpleNamespace(urma_phase_enable=Mock(),urma_phase_snapshot=lambda:'{"records":[]}',urma_timeline_begin=Mock(),urma_timeline_end=lambda:'{"events":[],"origin_ns":0,"dropped":0}',gpu_stage_begin=Mock(),gpu_stage_end=Mock(return_value='{"records":[],"nominal_clock_khz":1000}'))
            torch=SimpleNamespace(cuda=SimpleNamespace(synchronize=lambda:None,current_device=lambda:0,Event=Event),int='int',empty=lambda *a,**k:SimpleNamespace(zero_=lambda:None))
            m=Measurements(torch,SimpleNamespace(barrier=lambda:None),ep,Path(tmp),'normal',True,2,5,timeline=True,gpu_rings=lambda:[123]);fn=Mock();m.api(fn,'combine_chunk16')
            self.assertEqual(fn.call_count,7);self.assertEqual(ep.gpu_stage_begin.call_count,5);self.assertEqual(ep.gpu_stage_end.call_count,5)
            self.assertEqual([json.loads(x)['index'] for x in (Path(tmp)/'gpu_stages.jsonl').read_text().splitlines()],list(range(5)))
    def test_strict_two_build_summary_and_corruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for job in ('r01','r02','r03','diagnostic'):
                diag=job=='diagnostic';n=5 if diag else 50
                for rank in (0,1):
                    node=root/job/f'node{rank}';case=node/'normal';case.mkdir(parents=True)
                    c=dict(mode='normal',backend='urma',api_only=True,normal_completion='all',dispatch_trial=False,gpu_stages=diag,stage_profile=diag,timeline=diag,warmups=n,samples=n,job=job,kernel='k',libraries={},torch='t')
                    for p,data in [(node/'setup.json',dict(error=None,fingerprint=c)),(node/'host.json',dict(boot_id=str(rank))),(node/'build_manifest.json',dict(sources={'a':'b'},sdk={},flags={'UCCL_GPU_STAGE_TRACE':str(int(diag))})),(case/'status.json',dict(rc=0,checks={'done':True}))]:p.write_text(json.dumps(data))
                    (case/'api_samples.jsonl').write_text('\n'.join(json.dumps(dict(label=l,mode='normal',index=i,used=i>0,us=100)) for l in LABELS for i in range(n)))
                    (case/'sample_summary.jsonl').write_text('\n'.join(json.dumps(dict(label=l,enabled=diag,warmups=n,measured=n,**distribution([100]*(n-1)))) for l in LABELS))
                    (case/'phases.jsonl').write_text('\n'.join(json.dumps(dict(label=l,records=[])) for l in LABELS))
                    if not diag:continue
                    (node/'gpu_stage_sites.json').write_bytes((HERE/'gpu_stage_sites.json').read_bytes())
                    windows=[];traces=[]
                    for l in LABELS:
                        for i in range(5):
                            windows.append(dict(label=l,index=i,clock_domain='per_thread_clock64_delta',nominal_clock_khz=1000,records=[dict(ring=0,stage=k,sample_lane=0,calls=1,sum_cycles=100,max_cycles=100) for k in (40,41,42,46,48)]))
                            events=[dict(ns=(j+1)*100,wr_id=1,proxy=0,qp=1,stage=s,value=0) for j,s in enumerate(['collect','prepare','post_begin','post_end','cqe','ack_slot'])]
                            events.append(dict(ns=250,wr_id=1,proxy=0,qp=0,stage='gpu_publish_clock',value=1000))
                            traces.append(dict(label=l,index=i,origin_ns=0,dropped=0,events=events))
                    (case/'gpu_stages.jsonl').write_text('\n'.join(json.dumps(w) for w in windows));(case/'urma_timeline.jsonl').write_text('\n'.join(json.dumps(w) for w in traces))
            gpu,trace=summarize(root,root/'summary');self.assertEqual(len(gpu),250);self.assertEqual(len(trace),50)
            p=root/'diagnostic/node0/normal/gpu_stages.jsonl';w=[json.loads(x) for x in p.read_text().splitlines()];w[0]['records'][0]['max_cycles']=101;p.write_text('\n'.join(json.dumps(x) for x in w))
            with self.assertRaisesRegex(ValueError,'GPU metric'):summarize(root,root/'summary')
if __name__=='__main__':unittest.main()
