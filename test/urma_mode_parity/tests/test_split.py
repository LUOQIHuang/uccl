import ast
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parents[1];sys.path.insert(0,str(HERE))
from split_support import points,roles,affinity
from summarize_split import summarize
from test_micro import capture


class SplitTests(unittest.TestCase):
    def test_plan_has_equal_total_payload_and_unambiguous_roles(self):
        gs=[dict(label='fp8'),dict(label='bf16')]
        for threads,count in ((1,6),(4,14)):
            plan=points(gs,threads);self.assertEqual(len(plan),count)
            for p in plan:
                if '_write_' in p['label']:self.assertEqual(p['messages']*threads,128)
                if p['direction']=='0to1':self.assertEqual((roles(p,0),roles(p,1)),((True,False),(False,True)))
                if p['local']:self.assertEqual(roles(p,0),(False,False))

    def test_affinity_selects_workers_and_separate_controller(self):
        fake=SimpleNamespace(parent=SimpleNamespace(name='node2'),read_text=lambda:'8-15')
        with patch('split_support.os.sched_getaffinity',return_value=set(range(8,16)),create=True),patch('split_support.Path.glob',return_value=[fake]),patch('split_support.Path.read_text',return_value='2'):
            a=affinity(SimpleNamespace(gpu_pci='gpu',micro_cpus='auto'))
            self.assertEqual(a['workers'],[8,9,10,11]);self.assertEqual(a['controller'],12)
            with self.assertRaises(ValueError):affinity(SimpleNamespace(gpu_pci='gpu',micro_cpus='8,8,9,10'))

    def test_real_split_worker_protocol_and_summary(self):
        # Reuse the fake extension boundary of the legacy worker test, run
        # the REAL split worker with both process roles and all 6 jobs.
        tree=ast.parse((HERE/'tests/test_micro.py').read_text())
        bootstrap=next(n.value for n in ast.walk(tree) if isinstance(n,ast.Constant) and isinstance(n.value,str) and 'class Session:' in n.value)
        bootstrap=bootstrap.replace('    def close(self):pass', '''    def experiment(self,send,receive,scan,cpus):self.send=send
    def local_scan(self,seq):
        self.messages=0
        return json.dumps(dict(span_ns=10000,gpu_us=2.0,write_bytes=0,cqes=0,retired=0))
    def close(self):pass''')
        bootstrap=bootstrap.replace('ops=self.threads*messages*(2 if fadd else 1)','ops=self.threads*messages*(2 if fadd else 1) if self.send else 0')
        bootstrap=bootstrap.replace('write_bytes=self.threads*messages*self.size','write_bytes=self.threads*messages*self.size if self.send else 0')
        bootstrap=bootstrap.replace("count=s.messages*s.threads", "count=s.messages*s.threads if getattr(s,'send',False) else 0")
        bootstrap=bootstrap.replace("writes=[dict(bytes=s.size,src_mod=s.src,dst_mod=s.dst,count=s.messages*s.threads if getattr(s,'send',False) else 0)]", "writes=([dict(bytes=s.size,src_mod=s.src,dst_mod=s.dst,count=s.messages*s.threads)] if getattr(s,'send',False) and s.messages else [])")
        bootstrap=bootstrap.replace("'micro_worker.py'","'split_worker.py'")
        bootstrap=bootstrap.replace("runpy.run_path(","os.sched_setaffinity=lambda *a:None\nrunpy.run_path(")
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);layout=root/'layout.jsonl';capture(layout);boot=root/'boot.py';boot.write_text(bootstrap)
            for repeat in range(3):
                for threads in (1,4):
                    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
                    ps=[];logs=[]
                    try:
                        for rank in (0,1):
                            node=root/f'r{repeat}_t{threads}'/f'node{rank}';case=node/'micro';case.mkdir(parents=True)
                            c=dict(job=node.parent.name,suite='split',threads=threads,warmups=2,samples=3,kernel='k',build_hash='b',diagnostics_hash='d',libraries={},torch='t',normal_completion='all')
                            placement=dict(workers=[0,1,2,3],controller=4)
                            req=dict(module='fake',module_sha='sha',rank=rank,threads=threads,port=port,master_addr='127.0.0.1',warmups=2,samples=3,layout=str(layout),fingerprint=c,placement=placement)
                            for path,data in [(case/'request.json',req),(node/'setup.json',dict(error=None,fingerprint=c)),(node/'host.json',dict(host=f'host{rank}',boot_id='boot')),(node/'affinity.json',placement),(case/'status.json',dict(rc=0,checks=dict(done=True)))]:path.write_text(json.dumps(data))
                            log=(case/'console.log').open('w');logs.append(log)
                            ps.append(subprocess.Popen([sys.executable,str(boot),str(HERE)],env=dict(os.environ,MICRO_CASE=str(case)),stdout=log,stderr=subprocess.STDOUT))
                        for rank,p in enumerate(ps):
                            p.wait(timeout=20);out=(root/f'r{repeat}_t{threads}/node{rank}/micro/console.log').read_text()
                            self.assertEqual(p.returncode,0,out);self.assertEqual(out.count('URMA_MICRO_POINT '),6 if threads==1 else 14)
                    finally:
                        for p in ps:
                            if p.poll() is None:p.kill()
                            p.wait()
                        for log in logs:log.close()
            runs,agg=summarize([root],root/'summary')
            self.assertEqual((len(runs),len(agg)),(120,40))
            self.assertTrue(all(r['tx_GBps']=='' for r in agg if not r['send']))
            path=root/'r0_t1/node0/micro/micro_samples.jsonl';raw=path.read_text();a=[json.loads(s) for s in raw.splitlines()];a[0]['cqes']-=1
            path.write_text('\n'.join(json.dumps(r) for r in a))
            with self.assertRaises(ValueError):summarize([root],root/'summary')


if __name__=='__main__':unittest.main()
