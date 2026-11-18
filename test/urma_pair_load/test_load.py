import ast,io,json,os,socket,subprocess,sys,tempfile,unittest
from contextlib import redirect_stdout
from pathlib import Path
import analyze,overlay
HERE=Path(__file__).resolve().parent
def sample(lanes=1,limit=1):
    pairs=[]
    for j in range(4):
        b=1000+(j*10000 if limit==1 else (j*100 if lanes==4 else 0))
        pairs.append(dict(write_id=((j%lanes)<<32)+40+j*2,atomic_id=((j%lanes)<<32)+41+j*2,
            write_post_begin=b,write_post_end=b+100,atomic_post_begin=b,atomic_post_end=b+100,
            write_cqe=b+2000,atomic_cqe=b+4000,write_poll_seq=j*2+1 if limit==1 else 1,
            atomic_poll_seq=j*2+2 if limit==1 else 2,sdk_poll_calls=8))
    return dict(version=1,clock='CLOCK_MONOTONIC_RAW',data_lanes=lanes,max_inflight_pairs=limit,
        observed_max_inflight_pairs=limit,pairs_expected=4,sdk_poll_calls=8,loop_api_ns=40000,pairs=pairs)
class Tests(unittest.TestCase):
    def test_shapes_and_negative_gap(self):
        for lanes,limit in analyze.SHAPES.values():
            r,span=analyze.metrics(sample(lanes,limit),lanes,limit)
            self.assertEqual(len(r),4);self.assertGreater(span,0)
        s=sample();s['pairs'][0]['atomic_cqe']=2500
        self.assertEqual(analyze.metrics(s,1,1)[0][0]['gap_us'],-.5)
    def test_rejects_wrong_concurrency_identity_and_chain(self):
        s=sample(1,4);s['observed_max_inflight_pairs']=1
        with self.assertRaises(ValueError):analyze.metrics(s,1,4)
        s=sample(4,4);s['pairs'][1]['write_id']=s['pairs'][0]['write_id']
        with self.assertRaises(ValueError):analyze.metrics(s,4,4)
        s=sample(1,4);s['pairs'][0]['atomic_post_begin']+=1
        with self.assertRaises(ValueError):analyze.metrics(s,1,4)
        s=sample();s['pairs'][1]['write_post_begin']=1100
        with self.assertRaises(ValueError):analyze.metrics(s,1,1)
    def test_overlay_and_python(self):
        for name in overlay.base.TARGETS:
            text=subprocess.check_output(['git','-C',str(overlay.REPO),'show',overlay.BASE+':'+name]).decode()
            self.assertNotEqual(text,overlay.changes(name,text))
        self.assertIn('load_measure',overlay.header())
        for p in HERE.glob('*.py'):ast.parse(p.read_text())
    @unittest.skipUnless(os.name=='posix','real TCP workers with fake OS/GPU boundaries')
    def test_two_node_worker_report_and_missing_sample(self):
        with tempfile.TemporaryDirectory() as tmp,redirect_stdout(io.StringIO()):
            root=Path(tmp)/'job';folder=root/'urma';folder.mkdir(parents=True)
            for n in (0,1):(folder/('overlay_node%d.json'%n)).write_text('{"version":1}')
            bootstrap=Path(tmp)/'fake.py'
            bootstrap.write_text('''
import json,os,runpy,sys,types
from pathlib import Path
sys.path.insert(0,sys.argv[1]);from test_load import sample
r=json.loads(Path(os.environ['SERIAL_PAIR_REQUEST']).read_text());saved=Path.read_text
Path.read_text=lambda self,*a,**k: '0' if self.name=='numa_node' else saved(self,*a,**k)
os.sched_getaffinity=lambda pid:{0}
os.sched_setaffinity=lambda pid,cpus:None
class Session:
 def __init__(self,*args):self.lanes=args[-1];self.seq=0
 def metadata(self):return json.dumps(dict(serial=True,channels=self.lanes,control_memory='gpu',write_order='relax',atomic_order='strong',atomic_fence='per_request',normal_completion='all'))
 def descriptors(self,count):return [b'fake']
 def connect(self,peer):assert peer==[b'fake']
 def load_prepare(self,limit,rank):self.limit=limit
 def experiment(self,send,receive,*args):self.send=send;assert send!=receive
 def load_step(self,seq,limit):
  assert seq==self.seq and limit==self.limit;self.seq+=1
  return json.dumps(sample(self.lanes,limit) if self.send else dict(version=1,receiver_only=True))
 def load_validate(self,seq):assert seq+1==self.seq
 def close(self):pass
ep=types.SimpleNamespace(get_urma_pair_load_version=lambda:1,UrmaMicroSession=Session)
sys.modules['support']=types.SimpleNamespace(load_extension=lambda path:(ep,dict(module_sha='x')))
runpy.run_path(str(Path(sys.argv[1])/'worker.py'),run_name='__main__')
''')
            with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
            procs=[]
            try:
                for n in (0,1):
                    p=folder/'cases/job_urma_run'/('node%d'%n);p.mkdir(parents=True)
                    req=dict(case=str(p),backend='urma',node_rank=n,module='dummy',module_sha='x',gpu_pci='dummy',worker_cpu='0',
                        master_addr='127.0.0.1',port=port,job='job',rounds=1,warmups=1,samples=11,source_id=dict(sources={},flags={}))
                    (p/'worker_request.json').write_text(json.dumps(req));(p/'manifest.json').write_text('{"module_sha":"x"}')
                    env=dict(os.environ,SERIAL_PAIR_REQUEST=str(p/'worker_request.json'),PYTHONDONTWRITEBYTECODE='1')
                    procs.append(subprocess.Popen([sys.executable,str(bootstrap),str(HERE)],env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT))
                for proc in procs:
                    text=proc.communicate(timeout=25)[0].decode();self.assertEqual(proc.returncode,0,text)
                result=analyze.summarize(root,root/'summary');self.assertEqual(len(result),6)
                self.assertTrue(all(r['pairs']==40 and r['stable'] for r in result))
                p=folder/'cases/job_urma_run/node0/samples.jsonl';p.write_text('\n'.join(p.read_text().splitlines()[1:]))
                with self.assertRaisesRegex(ValueError,'missing'):analyze.summarize(root,root/'bad')
            finally:
                for proc in procs:
                    if proc.poll() is None:proc.kill();proc.wait()
                    if proc.stdout:proc.stdout.close()
if __name__=='__main__':unittest.main()
