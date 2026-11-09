import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
HERE=Path(__file__).resolve().parents[1];sys.path.insert(0,str(HERE))
from summarize_normal_notify import summarize
from phase_support import distribution
from phase_runtime import Measurements
from test_micro import capture
LABELS=['layout','dispatch_fp8_chunk32','dispatch_bf16_chunk32','combine_chunk12','combine_chunk16']

class NormalNotifyTests(unittest.TestCase):
    def test_timeline_only_first_dispatch_sample_and_closes_on_failure(self):
        for fail in (False,True):
            with tempfile.TemporaryDirectory() as tmp:
                events=[]
                class Event:
                    def __init__(self,**kw):pass
                    def record(self):pass
                    def elapsed_time(self,other):return 1
                ep=SimpleNamespace(urma_phase_snapshot=lambda:'{"records":[]}',urma_phase_enable=Mock(),urma_timeline_begin=lambda:events.append('begin'),urma_timeline_end=lambda:(events.append('end') or '{"events":[],"dropped":0}'))
                torch=SimpleNamespace(cuda=SimpleNamespace(synchronize=lambda:None,current_device=lambda:0,Event=Event),int='int',empty=lambda *a,**k:SimpleNamespace(zero_=lambda:None))
                m=Measurements(torch,SimpleNamespace(barrier=lambda:None),ep,Path(tmp),'normal',True,2,3,timeline=True)
                count=[0]
                def fn():
                    count[0]+=1;events.append('fn')
                    if fail and count[0]==3:raise RuntimeError('GPU failed')
                if fail:
                    with self.assertRaises(RuntimeError):m.api(fn,'dispatch_fp8_chunk32')
                    self.assertEqual(events,['fn','fn','begin','fn','end'])
                else:
                    m.api(fn,'dispatch_fp8_chunk32');self.assertEqual(events,['fn','fn','begin','fn','end','fn','fn'])
                self.assertEqual(len((Path(tmp)/'urma_timeline.jsonl').read_text().splitlines()),1)
                self.assertFalse(ep.urma_phase_enable.call_args.args[0])

    def test_three_dual_node_workers_and_seven_job_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);layout=root/'layout.jsonl';capture(layout)
            boot=root/'boot.py';boot.write_text("""
import json,os,runpy,sys,types,subprocess
from pathlib import Path
sys.path.insert(0,sys.argv[1])
class Session:
    def __init__(self,device,threads,size):assert threads==4
    def metadata(self):return json.dumps(dict(channels=8,normal_completion='all',write_order='relax',atomic_order='strong',atomic_fence='per_request',control_memory='gpu',atomic_return_memory='host',production_transport=True))
    def descriptors(self,n):return [b'fake']*n
    def connect(self,bs):assert bs==[b'fake']*4
    def prepare(self,size,src,dst,window,rank):assert window==1;self.size=size;self.src=src;self.dst=dst;self.seq=0;Session.current=self
    def notification_mode(self,op):assert op in (0,1,2);self.op=op
    def experiment(self,send,receive,scan,cpus):assert send and receive and not scan and len(cpus)==4
    def arm(self,seq,visible,fadd,messages):assert seq==self.seq and messages==8 and fadd==(self.op!=0) and (not visible or self.op==2)
    def step(self,seq,fadd,messages):
        assert seq==self.seq;self.seq+=1
        return json.dumps(dict(span_ns=10000,cq_span_ns=9000,write_bytes=0 if self.op==1 else 32*self.size,cqes=64 if self.op==2 else 32,retired=64 if self.op==2 else 32,threads=[9000]*4))
    def validate(self,seq,fadd,messages):assert seq+1==self.seq
    def close(self):pass
def end_layout():
    s=Session.current
    return json.dumps(dict(modulus=4096,dropped=0,writes=[] if s.op==1 else [dict(bytes=s.size,src_mod=s.src,dst_mod=s.dst,count=32)]))
support=types.ModuleType('support');support.load_extension=lambda p:(types.SimpleNamespace(UrmaMicroSession=Session,urma_phase_enable=lambda x:None,urma_layout_begin=lambda:None,urma_layout_end=end_layout),dict(module_sha='sha'))
sys.modules['support']=support;os.sched_setaffinity=lambda *a:None
subprocess.run=lambda *a,**k:types.SimpleNamespace(stdout='kernel',stderr='',returncode=0)
runpy.run_path(str(Path(sys.argv[1])/'notify_worker.py'),run_name='__main__')
""")
            def setup(job,rank,**kw):
                node=root/job/f'node{rank}';node.mkdir(parents=True)
                c=dict(job=job,build_hash='build',kernel='kernel',torch='torch',libraries={},diagnostics_hash='diag',device='urma',gpu='0',ifname='eth',normal_completion='all');c.update(kw)
                (node/'setup.json').write_text(json.dumps(dict(error=None,fingerprint=c)));(node/'host.json').write_text(json.dumps(dict(host=f'host{rank}',boot_id=f'boot{rank}')));return node,c
            for repeat in (1,2,3):
                with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
                processes=[];logs=[];job=f'job_micro_r0{repeat}'
                try:
                    for rank in (0,1):
                        node,c=setup(job,rank,suite='notify',threads=4,repeat=repeat,warmups=20,samples=50)
                        case=node/'micro';case.mkdir();placement=dict(workers=[0,1,2,3],controller=4)
                        (node/'affinity.json').write_text(json.dumps(placement));(case/'status.json').write_text('{"rc":0,"checks":{"done":true}}')
                        req=dict(module='fake',module_sha='sha',rank=rank,threads=4,master_addr='127.0.0.1',port=port,warmups=20,samples=50,placement=placement,layout=str(layout),fingerprint=c)
                        (case/'request.json').write_text(json.dumps(req));log=(case/'bench.log').open('w');logs.append(log)
                        processes.append(subprocess.Popen([sys.executable,str(boot),str(HERE)],env=dict(os.environ,MICRO_CASE=str(case)),stdout=log,stderr=subprocess.STDOUT))
                    for rank,proc in enumerate(processes):
                        proc.wait(timeout=25);text=(root/job/f'node{rank}/micro/bench.log').read_text();self.assertEqual(proc.returncode,0,text);self.assertEqual(text.count('URMA_MICRO_POINT '),8)
                finally:
                    for proc in processes:
                        if proc.poll() is None:proc.kill()
                        proc.wait()
                    for log in logs:log.close()
            for job in ('job_baseline_r01','job_baseline_r02','job_baseline_r03','job_timeline'):
                diagnostic=job.endswith('timeline');n=5 if diagnostic else 50
                for rank in (0,1):
                    node,c=setup(job,rank,backend='urma',mode='normal',shape=[4096,128,7168,256,8],phase_suite=True,api_only=True,stage_profile=diagnostic,capture_layout=diagnostic,timeline=diagnostic,save_traces=False,dispatch_trial=False,ll_write_order='default',warmups=n,samples=n)
                    case=node/'normal';case.mkdir();(case/'status.json').write_text('{"rc":0,"checks":{"done":true}}')
                    (case/'api_samples.jsonl').write_text('\n'.join(json.dumps(dict(label=l,mode='normal',index=i,used=i>0,us=100)) for l in LABELS for i in range(n)))
                    (case/'sample_summary.jsonl').write_text('\n'.join(json.dumps(dict(label=l,mode='normal',enabled=diagnostic,warmups=n,measured=n,**distribution([100]*(n-1)))) for l in LABELS))
                    metrics={k:dict(calls=64,items=64,ns=6400) for k in ('write_requests','fadd_requests','sdk_post','submit_to_observed_cqe_sampled')}
                    windows=[dict(label=l,iterations=n,enabled=diagnostic,records=[dict(metrics=metrics)] if diagnostic else []) for l in LABELS]
                    (case/'phases.jsonl').write_text('\n'.join(json.dumps(w) for w in windows))
                    if not diagnostic:continue
                    (case/'write_layout.jsonl').write_text('\n'.join(json.dumps(dict(label=l,dropped=0)) for l in LABELS))
                    traces=[]
                    for label in LABELS[1:3]:
                        events=[dict(ns=(i+1)*100,wr_id=1,proxy=0,qp=0,stage=stage,value=0) for i,stage in enumerate(['collect','prepare','post_begin','post_end','cqe','ack_slot'])]
                        events.append(dict(ns=800,wr_id=0,proxy=0,qp=0,stage='tail_published',value=2));traces.append(dict(label=label,index=0,backend='urma',origin_ns=0,dropped=0,events=events))
                    (case/'urma_timeline.jsonl').write_text('\n'.join(json.dumps(w) for w in traces))
            full,native,trace=summarize(root,root/'summary');self.assertEqual((len(full),len(native),len(trace)),(30,16,4));self.assertTrue(all(r['tx_GBps']=='' for r in native if r['operation']==1))
            path=root/'job_micro_r01/node0/micro/micro_samples.jsonl';rr=[json.loads(x) for x in path.read_text().splitlines()];rr[0]['cqes']-=1;path.write_text('\n'.join(json.dumps(x) for x in rr))
            with self.assertRaisesRegex(ValueError,'CQE/bytes'):summarize(root,root/'summary')

if __name__=='__main__':unittest.main()
