import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
HERE=Path(__file__).resolve().parents[1];sys.path.insert(0,str(HERE))
import common
from summarize_campaign import summarize
from collect_campaign import collect
LABELS=['layout','dispatch_fp8_chunk32','dispatch_bf16_chunk32','combine_chunk12','combine_chunk16']


class CampaignTests(unittest.TestCase):
    def test_cpu_measurement_hook_encloses_only_timed_window(self):
        import ast
        from types import SimpleNamespace
        from unittest.mock import Mock
        source=ast.parse((HERE/'worker.py').read_text());fn=next(n for n in source.body if isinstance(n,ast.FunctionDef) and n.name=='measure')
        events=[];rows=[]
        class Event:
            def __init__(self,**kw):pass
            def record(self):pass
            def elapsed_time(self,other):return 1
        ep=SimpleNamespace(urma_phase_snapshot=lambda:'{"records":[]}',urma_layout_begin=lambda:events.append('layout_begin'),verbs_diag_begin=lambda:events.append('begin'),verbs_diag_end=lambda:events.append('end'),urma_layout_end=lambda:'{"writes":[],"dropped":0}')
        torch=SimpleNamespace(cuda=SimpleNamespace(synchronize=lambda:None,current_device=lambda:0,Event=Event),int='int',empty=lambda *a,**k:SimpleNamespace(zero_=lambda:None))
        ns=dict(torch=torch,dist=SimpleNamespace(barrier=lambda:None),ep=ep,warmups=2,samples=3,cpu_diag=True,profile=False,gpu_stages=False,time=time,mode='normal',common=common,json=json,append=lambda n,r:rows.append((n,r)))
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'measure','exec'),ns)
        ns['measure'](lambda:events.append('fn'),'dispatch_fp8_chunk32')
        self.assertEqual(events,['fn','fn','layout_begin','begin','fn','fn','fn','end'])
        self.assertIn('verbs_phases.jsonl',[x[0] for x in rows])
        events.clear();rows.clear();ns['cpu_light']=True
        ns['measure'](lambda:events.append('fn'),'dispatch_fp8_chunk32')
        self.assertEqual(events,['fn','fn','begin','fn','fn','fn','end'])
        self.assertNotIn('write_layout.jsonl',[x[0] for x in rows])

    def test_real_micro_protocol_all_jobs_and_campaign_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);layout=root/'layout.jsonl'
            layout.write_text('\n'.join(json.dumps(dict(label=l,modulus=4096,dropped=0,writes=[dict(bytes=8192,src_mod=12,dst_mod=20,lane=0,count=10)])) for l in LABELS[1:3]))
            boot=root/'boot.py';boot.write_text('''
import json,os,runpy,sys,types,subprocess
from pathlib import Path
sys.path[:0]=[sys.argv[1],str(Path(sys.argv[1]).parent/'urma_mode_parity')]
import phase_support
class Session:
    def __init__(self,hca,gid,threads,size):self.threads=threads;assert gid==3
    def descriptor(self,i):return b'fake'
    def metadata(self):return '{"registration":"dmabuf","gid_index":3}'
    def connect(self,bs):assert bs==[b'fake']*self.threads
    def prepare(self,size,src,dst,rank):self.size=size;self.seq=0
    def experiment(self,send,receive,cpus):self.send=send;assert len(cpus)==self.threads
    def step(self,seq):
        assert seq==self.seq;self.seq+=1
        return json.dumps(dict(span_ns=10000,write_bytes=128*self.size if self.send else 0,cqes=128 if self.send else 0,retired=128 if self.send else 0))
    def validate(self,seq):assert seq+1==self.seq
    def close(self):pass
c=types.ModuleType('common');c.phase_support=phase_support
c.load_extension=lambda p:(types.SimpleNamespace(VerbsWriteSession=Session),dict(module_sha='sha'))
sys.modules['common']=c
os.sched_setaffinity=lambda *a:None
subprocess.run=lambda *a,**k:types.SimpleNamespace(stdout='fake kernel',stderr='',returncode=0)
runpy.run_path(str(Path(sys.argv[1])/'micro_worker.py'),run_name='__main__')
''')
            def config(job,**kw):
                c=dict(job=job,identity=dict(sources_hash='src',flags={'USE_URMA':'0'}),kernel='kernel',torch='torch',libraries={},gid_index=3,variant='current',mode='normal',repeats=3,warmups=50,samples=50,no_profile=False,profile_only=False,cpu_diagnostics=False);c.update(kw);return c
            def setup(job,rank,c):
                node=root/job/f'node{rank}';node.mkdir(parents=True)
                (node/'setup.json').write_text(json.dumps(dict(error=None,fingerprint=c)))
                (node/'host.json').write_text(json.dumps(dict(host=f'host{rank}',boot_id=f'boot{rank}')));return node
            def measurement(case,n,profile):
                case.mkdir(parents=True);(case/'status.json').write_text('{"rc":0,"checks":{"done":true}}')
                (case/'config.json').write_text(json.dumps(dict(profile=profile)))
                raw=[dict(label=l,mode='normal',index=i,used=i>0,us=1000) for l in LABELS for i in range(n)]
                (case/'api_samples.jsonl').write_text('\n'.join(json.dumps(r) for r in raw))
            for rank in (0,1):
                job='job_baseline';node=setup(job,rank,config(job))
                for tag in ('r01_baseline','r02_baseline','r03_baseline','gpu_profile'):
                    case=node/tag/'normal';measurement(case,50,tag=='gpu_profile')
                    if tag=='gpu_profile':(case/'gpu_stages.jsonl').write_text('\n'.join(json.dumps(dict(label=l,kernel_names=['dispatch','notify'],kernel_seconds=[.001,.0001])) for l in LABELS[1:]))
                job='job_cpu';node=setup(job,rank,config(job,cpu_diagnostics=True,no_profile=True,repeats=1,warmups=5,samples=5));case=node/'cpu_diagnostic/normal';measurement(case,5,False)
                windows=[dict(label=l,iterations=5,records=[dict(metrics={k:dict(calls=10,items=10,ns=100) for k in ('write_requests','fadd_requests','sdk_post','submit_to_observed_cqe_sampled')})]) for l in LABELS]
                (case/'verbs_phases.jsonl').write_text('\n'.join(json.dumps(r) for r in windows))
                (case/'write_layout.jsonl').write_text('\n'.join(json.dumps(dict(label=l,dropped=0)) for l in LABELS))
            for repeat in range(3):
                for threads in (1,4):
                    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
                    processes=[];logs=[]
                    try:
                        job=f'job_micro_r0{repeat+1}_t{threads}'
                        for rank in (0,1):
                            c=config(job,suite='verbs_write_micro',threads=threads,warmups=20,samples=50)
                            node=setup(job,rank,c);case=node/'micro';case.mkdir();placement=dict(workers=[0,1,2,3],controller=4)
                            (node/'affinity.json').write_text(json.dumps(placement));(case/'status.json').write_text('{"rc":0,"checks":{"done":true}}')
                            request=dict(module='fake',module_sha='sha',rank=rank,threads=threads,port=port,master_addr='127.0.0.1',warmups=20,samples=50,placement=placement,layout=str(layout),fingerprint=c,hca='fake',gid_index=3)
                            (case/'request.json').write_text(json.dumps(request));log=(case/'bench.log').open('w');logs.append(log)
                            processes.append(subprocess.Popen([sys.executable,str(boot),str(HERE)],env=dict(os.environ,VM_CASE=str(case)),stdout=log,stderr=subprocess.STDOUT))
                        for rank,p in enumerate(processes):
                            p.wait(timeout=25);out=(root/job/f'node{rank}/micro/bench.log').read_text();self.assertEqual(p.returncode,0,out);self.assertIn('VERBS_MICRO_ALL_PASSED',out)
                    finally:
                        for p in processes:
                            if p.poll() is None:p.kill()
                            p.wait()
                        for f in logs:f.close()
            runs,agg,stages=summarize(root,root/'summary')
            self.assertEqual((len(runs),len(agg)),(72,24));self.assertTrue(stages)
            self.assertTrue(all(x['tx_GBps']=='' for x in agg if not x['send']))
            path=root/'job_micro_r01_t1/node0/micro/micro_samples.jsonl';raw=[json.loads(s) for s in path.read_text().splitlines()];raw[0]['cqes']=127;path.write_text('\n'.join(json.dumps(r) for r in raw))
            with self.assertRaises(ValueError):summarize(root,root/'summary')
            dest=root/'collected';collect(root,'job',1,dest)
            self.assertTrue((dest/'job_micro_r01_t1/node1/micro/plan.json').is_file())


if __name__=='__main__':unittest.main()
