import json
from pathlib import Path
import socket
import os
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from micro_support import select_geometries,points,Peer
from summarize_micro import summarize
from phase_support import distribution
from phase_runtime import Measurements


def capture(path):
    windows=[]
    for label in ('dispatch_fp8_chunk32','dispatch_bf16_chunk32'):
        windows.append(dict(label=label,modulus=4096,dropped=0,writes=[
            dict(bytes=4,src_mod=0,dst_mod=0,lane=0,count=999),
            dict(bytes=8192,src_mod=12,dst_mod=20,lane=0,count=30),
            dict(bytes=8192,src_mod=12,dst_mod=20,lane=1,count=30),
            dict(bytes=8192,src_mod=0,dst_mod=0,lane=0,count=10),
            dict(bytes=16384,src_mod=0,dst_mod=0,lane=0,count=20)]))
    path.write_text('\n'.join(json.dumps(r) for r in windows))
    return windows


class MicroTests(unittest.TestCase):
    def test_geometry_uses_payload_frequency_and_actual_alignment(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'layout.jsonl';windows=capture(path)
            g=select_geometries(path)
            self.assertEqual((g[0]['bytes'],g[0]['src_mod'],g[0]['dst_mod']),(8192,12,20))
            self.assertEqual(g[0]['selected_count'],60)
            self.assertEqual(g[0]['payload_writes'],90)
            self.assertEqual(len(points(g)),12)
            windows[0]['dropped']=1
            path.write_text('\n'.join(json.dumps(r) for r in windows))
            with self.assertRaises(ValueError):select_geometries(path)

    def test_peer_exchange_handles_large_frames_and_rejects_stage_mismatch(self):
        left,right=socket.socketpair()
        a,b=Peer.__new__(Peer),Peer.__new__(Peer)
        a.rank=0;a.socket=left;b.rank=1;b.socket=right
        left.settimeout(3);right.settimeout(3)
        result=[];errors=[]
        def other():
            try:
                result.append(b.exchange('descriptors','remote'*20000))
                b.agree('wrong',0)
            except Exception as exc:errors.append(exc)
        thread=threading.Thread(target=other);thread.start()
        try:
            self.assertEqual(a.exchange('descriptors','local'*20000),'remote'*20000)
            with self.assertRaises(RuntimeError):a.agree('right',0)
            thread.join(5);self.assertFalse(thread.is_alive());self.assertEqual(len(errors),1)
            self.assertEqual(result,['local'*20000])
        finally:a.close();b.close()

    def test_layout_capture_ends_on_failed_measurement(self):
        with tempfile.TemporaryDirectory() as tmp:
            ep=SimpleNamespace(urma_phase_snapshot=lambda:'{"records":[]}',urma_phase_enable=Mock(),
                urma_layout_begin=Mock(),urma_layout_end=Mock(return_value='{"writes":[],"modulus":4096,"dropped":0}'))
            m=Measurements(SimpleNamespace(cuda=SimpleNamespace(synchronize=lambda:None)),
                SimpleNamespace(barrier=lambda:None),ep,Path(tmp),'normal',False,1,3,True)
            def fail():raise RuntimeError('measurement failed')
            with self.assertRaises(RuntimeError):m.window('dispatch_fp8_chunk32',fail,3)
            ep.urma_layout_begin.assert_called_once();ep.urma_layout_end.assert_called_once()
            row=json.loads((Path(tmp)/'write_layout.jsonl').read_text())
            self.assertEqual(row['iterations'],3)

    def test_worker_two_process_protocol_and_sample_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);layout=root/'layout.jsonl';capture(layout)
            with socket.socket() as sock:
                sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            # Execute the real worker and real TCP exchange, replacing only
            # the extension boundary and read-only kernel log subprocess.
            bootstrap=root/'bootstrap.py'
            bootstrap.write_text('''
import json,os,runpy,subprocess,sys,types
from pathlib import Path
sys.path.insert(0,sys.argv[1])
case=Path(os.environ['MICRO_CASE'])
request=json.loads((case/'request.json').read_text())
class Session:
    def __init__(self,device,threads,max_bytes):self.threads=threads;self.count=0
    def descriptors(self,n):return [b'descriptor']*n
    def connect(self,remote):assert remote==[b'descriptor']*self.threads
    def prepare(self,size,src,dst,window,rank):self.size=size;self.count=0;Session.current=self;self.src=src;self.dst=dst
    def arm(self,seq,visible,fadd,messages):assert seq==self.count
    def step(self,seq,fadd,messages):
        assert seq==self.count;self.count+=1;self.messages=messages
        ops=self.threads*messages*(2 if fadd else 1)
        return json.dumps(dict(span_ns=10000,cqes=ops,retired=ops,threads=[10000]*self.threads,
            write_bytes=self.threads*messages*self.size,build_ns_sum=100,post_ns_sum=200,poll_ns_sum=300,observe_ns_sum=0))
    def validate(self,seq,fadd,messages):assert self.count==request['warmups']+request['samples'] and seq+1==self.count
    def close(self):pass
support=types.ModuleType('support')
def layout_end():
    s=Session.current
    return json.dumps(dict(modulus=4096,dropped=0,writes=[dict(bytes=s.size,src_mod=s.src,dst_mod=s.dst,count=s.messages*s.threads)]))
support.load_extension=lambda path:(types.SimpleNamespace(UrmaMicroSession=Session,urma_phase_enable=lambda x:None,urma_layout_begin=lambda:None,urma_layout_end=layout_end),dict(module_sha='sha'))
sys.modules['support']=support
subprocess.run=lambda *a,**k:types.SimpleNamespace(stdout='simulated kernel log',stderr='',returncode=0)
runpy.run_path(str(Path(sys.argv[1])/'micro_worker.py'),run_name='__main__')
''')
            processes=[];logs=[]
            try:
                for rank in (0,1):
                    case=root/f'node{rank}'/'micro';case.mkdir(parents=True)
                    request=dict(module='simulated',module_sha='sha',rank=rank,threads=4,port=port,
                        master_addr='127.0.0.1',warmups=2,samples=3,layout=str(layout),fingerprint={'same':True})
                    (case/'request.json').write_text(json.dumps(request))
                    log=(case/'console.log').open('w');logs.append(log)
                    processes.append(subprocess.Popen([sys.executable,str(bootstrap),str(Path(__file__).resolve().parents[1])],
                        env=dict(os.environ,MICRO_CASE=str(case)),stdout=log,stderr=subprocess.STDOUT,text=True))
                for rank,process in enumerate(processes):
                    process.wait(timeout=20)
                    out=(root/f'node{rank}/micro/console.log').read_text()
                    self.assertEqual(process.returncode,0,out)
                    self.assertEqual(out.count('URMA_MICRO_POINT '),12)
                    self.assertIn('URMA_MICRO_ALL_PASSED',out)
                for rank in (0,1):
                    raw=[json.loads(s) for s in (root/f'node{rank}/micro/micro_samples.jsonl').read_text().splitlines()]
                    self.assertEqual(len(raw),36)
                    self.assertEqual(sum(r['used'] for r in raw),24)
            finally:
                for process in processes:
                    if process.poll() is None:process.kill()
                    process.communicate()
                for log in logs:log.close()

    def test_summary_preserves_nodes_and_rejects_corrupt_or_missing_samples(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);cap=root/'layout.jsonl';capture(cap);gs=select_geometries(cap)
            plan=points(gs)
            for rank in (0,1):
                for repeat in range(3):
                    node=root/f'job{repeat}'/f'node{rank}';case=node/'micro';case.mkdir(parents=True)
                    config=dict(job=f'job{repeat}',threads=4,warmups=20,samples=50,kernel='k',
                        build_hash='b',diagnostics_hash='d',libraries={},torch='t',normal_completion='all')
                    (node/'setup.json').write_text(json.dumps(dict(fingerprint=config,error=None)))
                    (node/'host.json').write_text(json.dumps(dict(host=f'host{rank}',boot_id=f'boot{rank}')))
                    (case/'status.json').write_text('{"rc":0,"checks":{"done":true}}')
                    (case/'plan.json').write_text(json.dumps(dict(points=plan,geometries=gs)))
                    raw=[];summaries=[];layouts=[]
                    for point in plan:
                        layouts.append(dict(label=point['label'],modulus=4096,dropped=0,writes=[dict(bytes=8192,src_mod=12,dst_mod=20,count=4*point['messages'])]))
                        # Different hosts must remain separate independent groups.
                        us=10+rank*10+repeat+(5 if point['fadd'] else 0)+(2 if point['visible'] else 0)
                        for i in range(50):
                            raw.append(dict(label=point['label'],index=i,used=i>0,us=us,span_ns=us*1000,
                                write_bytes=4*point['messages']*point['geometry']['bytes'],
                                cqes=4*point['messages']*(2 if point['fadd'] else 1),
                                retired=4*point['messages']*(2 if point['fadd'] else 1),threads=[us*1000]*4,
                                bytes=point['geometry']['bytes'],window=point['window'],messages=point['messages'],
                                fadd=point['fadd'],gpu_observed=point['visible'],
                                build_ns_sum=100,post_ns_sum=200,poll_ns_sum=300,observe_ns_sum=0))
                        summaries.append(dict(label=point['label'],samples=50,warmups=20,validation=True,**distribution([us]*49)))
                    for name,data in [('micro_samples.jsonl',raw),('micro_summary.jsonl',summaries),('micro_layout_verified.jsonl',layouts)]:
                        (case/name).write_text('\n'.join(json.dumps(r) for r in data))
            runs,aggregates,comparisons=summarize([root],root/'summary')
            self.assertEqual((len(runs),len(aggregates),len(comparisons)),(72,24,12))
            self.assertEqual({r['independent_runs'] for r in aggregates},{3})
            path=root/'job0/node0/micro/micro_samples.jsonl';original=path.read_text()
            bad=[json.loads(s) for s in original.splitlines()];bad[0]['cqes']-=1
            path.write_text('\n'.join(json.dumps(r) for r in bad))
            with self.assertRaises(ValueError):summarize([root],root/'summary')
            path.write_text(original)
            missing=root/'job3/node0';missing.mkdir(parents=True)
            (missing/'setup.json').write_text(json.dumps(dict(fingerprint=config)))
            with self.assertRaises(ValueError):summarize([root],root/'summary')


if __name__=='__main__':unittest.main()
