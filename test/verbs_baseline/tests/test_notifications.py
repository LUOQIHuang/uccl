import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
HERE=Path(__file__).resolve().parents[1];sys.path.insert(0,str(HERE))
import common
from summarize_notifications import summarize,timeline_rows
LABELS=['layout','dispatch_fp8_chunk32','dispatch_bf16_chunk32','combine_chunk12','combine_chunk16']

class NotificationTests(unittest.TestCase):
    def test_protocol_sampling_modes_and_strict_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);layout=root/'layout.jsonl'
            layout.write_text('\n'.join(json.dumps(dict(label=l,modulus=4096,dropped=0,writes=[dict(bytes=8192,src_mod=12,dst_mod=20,lane=0,count=10)])) for l in LABELS[1:3]))
            boot=root/'boot.py';boot.write_text("""
import json,os,runpy,sys,types,subprocess
from pathlib import Path
sys.path[:0]=[sys.argv[1],str(Path(sys.argv[1]).parent/'urma_mode_parity')]
import phase_support
class Session:
    def __init__(self,hca,gid,threads,size,notifications):assert notifications and threads==4 and gid==3
    def descriptor(self,i):return b'fake'
    def metadata(self):return json.dumps(dict(notifications=True,control_memory='gpu',atomic_return_memory='host',registration='dmabuf',gid_index=3,fadd_send_fence=False))
    def connect(self,bs):assert len(bs)==4
    def prepare(self,size,src,dst,rank):self.size=size;self.seq=0
    def experiment(self,send,receive,cpus):assert send and receive and len(cpus)==4
    def arm(self,seq,op,visible):assert seq==self.seq and (not visible or op==2);self.op=op;self.visible=visible
    def step(self,seq):
        assert seq==self.seq;self.seq+=1
        return json.dumps(dict(span_ns=10000,cq_span_ns=9000,write_bytes=0 if self.op==1 else 32*self.size,cqes=64 if self.op==2 else 32,retired=64 if self.op==2 else 32))
    def validate(self,seq):assert seq+1==self.seq
    def close(self):pass
c=types.ModuleType('common');c.phase_support=phase_support
c.load_extension=lambda p:(types.SimpleNamespace(VerbsWriteSession=Session),dict(module_sha='sha'))
sys.modules['common']=c
os.sched_setaffinity=lambda *a:None
subprocess.run=lambda *a,**k:types.SimpleNamespace(stdout='kernel',stderr='',returncode=0)
runpy.run_path(str(Path(sys.argv[1])/'notify_worker.py'),run_name='__main__')
""")
            def setup(job,rank,**kw):
                node=root/job/f'node{rank}';node.mkdir(parents=True)
                c=dict(job=job,identity={'src':'source'},kernel='kernel',torch='torch',libraries={},diagnostics={},gid_index=3,hca='fake');c.update(kw)
                (node/'setup.json').write_text(json.dumps(dict(error=None,fingerprint=c)))
                (node/'host.json').write_text(json.dumps(dict(host=f'host{rank}',boot_id=f'boot{rank}')));return node,c
            for repeat in (1,2,3):
                with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
                processes=[];logs=[];job=f'job_notify_r0{repeat}'
                try:
                    for rank in (0,1):
                        node,c=setup(job,rank,suite='verbs_notify_micro',threads=4,repeat=repeat,warmups=20,samples=50)
                        case=node/'micro';case.mkdir();placement=dict(workers=[0,1,2,3],controller=4)
                        (node/'affinity.json').write_text(json.dumps(placement));(case/'status.json').write_text('{"rc":0,"checks":{"done":true}}')
                        req=dict(module='fake',module_sha='sha',rank=rank,threads=4,master_addr='127.0.0.1',port=port,warmups=20,samples=50,placement=placement,layout=str(layout),fingerprint=c,hca='fake',gid_index=3)
                        (case/'request.json').write_text(json.dumps(req));log=(case/'bench.log').open('w');logs.append(log)
                        processes.append(subprocess.Popen([sys.executable,str(boot),str(HERE)],env=dict(os.environ,VM_CASE=str(case)),stdout=log,stderr=subprocess.STDOUT))
                    for rank,proc in enumerate(processes):
                        proc.wait(timeout=25);text=(root/job/f'node{rank}/micro/bench.log').read_text();self.assertEqual(proc.returncode,0,text);self.assertEqual(text.count('VERBS_MICRO_POINT '),8)
                finally:
                    for proc in processes:
                        if proc.poll() is None:proc.kill()
                        proc.wait()
                    for log in logs:log.close()
            for rank in (0,1):
                node,c=setup('job_timeline',rank,timeline=True,cpu_diagnostics=True,mode='normal',warmups=5,samples=5)
                case=node/'cpu_diagnostic/normal';case.mkdir(parents=True)
                (case/'status.json').write_text('{"rc":0,"checks":{"done":true}}')
                (case/'api_samples.jsonl').write_text('\n'.join(json.dumps(dict(label=l,index=i,used=i>0,us=100)) for l in LABELS for i in range(5)))
                (case/'verbs_phases.jsonl').write_text(json.dumps(dict(records=[])))
                windows=[]
                for label in LABELS[1:3]:
                    events=[dict(ns=(i+1)*100,wr_id=1,proxy=0,qp=9 if stage in ('post_begin','post_end','cqe') else 0,stage=stage,value=0) for i,stage in enumerate(['collect','prepare','post_begin','post_end','cqe','ack_slot'])]
                    events.append(dict(ns=800,wr_id=0,proxy=0,qp=0,stage='tail_published',value=2))
                    windows.append(dict(label=label,index=0,origin_ns=0,dropped=0,events=events))
                (case/'verbs_timeline.jsonl').write_text('\n'.join(json.dumps(w) for w in windows))
            runs,agg,trace=summarize(root,root/'summary');self.assertEqual((len(runs),len(agg),len(trace)),(48,16,4))
            self.assertTrue(all(r['tx_GBps']=='' for r in agg if r['operation']==1))
            r=trace[0];self.assertAlmostEqual(sum(r[k] for k in r if '_to_' in k and k.endswith('_us') and k not in ('collect_to_ack_slot_us','ack_slot_to_tail_published_us')),r['collect_to_ack_slot_us'])
            path=root/'job_notify_r01/node0/micro/micro_samples.jsonl';samples=[json.loads(x) for x in path.read_text().splitlines()];samples[0]['write_bytes']+=1;path.write_text('\n'.join(json.dumps(x) for x in samples))
            with self.assertRaisesRegex(ValueError,'CQE/bytes'):summarize(root,root/'summary')
            path=root/'job_timeline/node0/cpu_diagnostic/normal/verbs_timeline.jsonl';windows=[json.loads(x) for x in path.read_text().splitlines()];windows[0]['dropped']=1;path.write_text('\n'.join(json.dumps(x) for x in windows))
            with self.assertRaisesRegex(ValueError,'truncated'):timeline_rows(path,'node0')

if __name__=='__main__':unittest.main()
