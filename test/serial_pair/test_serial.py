import ast,csv,io,json,os,shutil,socket,subprocess,sys,tempfile,unittest
from contextlib import redirect_stdout
from pathlib import Path
import analyze,overlay
HERE=Path(__file__).resolve().parent
def sample(count=1):
    pairs=[]
    for j in range(count):
        b=1000+j*10000
        pairs.append(dict(write_id=40+j*2,atomic_id=41+j*2,write_post_begin=b,write_post_end=b+100,
            atomic_post_begin=b+200,atomic_post_end=b+300,write_cqe=b+2000,atomic_cqe=b+4000,
            write_poll_seq=1,atomic_poll_seq=2,sdk_poll_calls=2))
    return dict(version=1,clock='CLOCK_MONOTONIC_RAW',max_inflight_pairs=1,pairs_expected=count,loop_api_ns=count*10000,pairs=pairs)
class Tests(unittest.TestCase):
    def test_two_node_worker_protocol_for_both_backends(self):
        # Fake only the extension, GPU NUMA query and affinity boundary. Run
        # the actual worker, real TCP handshake, collection format and analyzer.
        for backend in ('verbs','urma'):
            with tempfile.TemporaryDirectory() as tmp,redirect_stdout(io.StringIO()):
                root=Path(tmp)/'job';folder=root/backend;folder.mkdir(parents=True)
                for n in (0,1):(folder/('overlay_node%d.json'%n)).write_text('{"version":1}')
                bootstrap=Path(tmp)/'fake.py'
                bootstrap.write_text('''
import json,os,runpy,sys,types
from pathlib import Path
sys.path.insert(0,sys.argv[1]);from test_serial import sample
r=json.loads(Path(os.environ['SERIAL_PAIR_REQUEST']).read_text());saved=Path.read_text
Path.read_text=lambda self,*a,**k: '0' if self.name=='numa_node' else saved(self,*a,**k)
os.sched_getaffinity=lambda pid:{0}
os.sched_setaffinity=lambda pid,cpus:None
class Session:
 def __init__(self,*args):self.seq=0
 def metadata(self):return json.dumps(dict(serial=True,channels=1,lanes_per_thread=1,control_memory='gpu',write_order='relax',atomic_order='strong',atomic_fence='per_request',registration='dmabuf',fadd_send_fence=False))
 def descriptors(self,count):return [b'fake']
 def descriptor(self,i):return b'fake'
 def connect(self,peer):assert peer==[b'fake']
 def serial_prepare(self,count,rank):self.seq=0;self.count=count
 def experiment(self,send,receive,*args):self.send=send;assert send!=receive
 def serial_step(self,seq,count):
  assert seq==self.seq and count==self.count;self.seq+=1
  return json.dumps(sample(count) if self.send else dict(version=1,receiver_only=True))
 def serial_validate(self,seq,count):assert seq+1==self.seq and count==self.count
 def close(self):pass
ep=types.SimpleNamespace(get_serial_pair_version=lambda:1,UrmaMicroSession=Session,VerbsWriteSession=Session)
loader=lambda path:(ep,dict(module_sha='x'))
sys.modules['support']=types.SimpleNamespace(load_extension=loader)
sys.modules['common']=types.SimpleNamespace(load_extension=loader)
runpy.run_path(str(Path(sys.argv[1])/'worker.py'),run_name='__main__')
''')
                with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
                processes=[]
                try:
                    for n in (0,1):
                        p=folder/'cases'/('job_'+backend+'_run')/('node%d'%n);p.mkdir(parents=True)
                        request=dict(case=str(p),backend=backend,node_rank=n,module='dummy',module_sha='x',gpu_pci='dummy',worker_cpu='0',
                            master_addr='127.0.0.1',port=port,job='job',rounds=1,warmups=1,samples=11,source_id=dict(sources={},flags={}),hca='fake',gid_index=3)
                        (p/'worker_request.json').write_text(json.dumps(request));(p/'manifest.json').write_text('{"module_sha":"x"}')
                        env=dict(os.environ,SERIAL_PAIR_REQUEST=str(p/'worker_request.json'),PYTHONDONTWRITEBYTECODE='1')
                        processes.append(subprocess.Popen([sys.executable,str(bootstrap),str(HERE)],env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT))
                    for proc in processes:
                        text=proc.communicate(timeout=20)[0].decode();self.assertEqual(proc.returncode,0,text)
                    result=analyze.summarize(root,backend,root/'summary');self.assertEqual(len(result),4)
                finally:
                    for proc in processes:
                        if proc.poll() is None:proc.kill();proc.wait()
                        if proc.stdout:proc.stdout.close()
    def test_timing_and_same_batch(self):
        s=sample(2);r,span=analyze.metrics(s,2);self.assertEqual(span,14);self.assertEqual(r[0]['gap_us'],2)
        s=sample();s['pairs'][0].update(atomic_cqe=3000,atomic_poll_seq=1)
        r,_=analyze.metrics(s,1);self.assertTrue(r[0]['same_poll_batch']);self.assertEqual(r[0]['gap_us'],0)
    def test_overlap_and_delayed_fadd_rejected(self):
        s=sample(2);s['pairs'][1]['write_post_begin']=2000
        with self.assertRaises(ValueError):analyze.metrics(s,2)
        s=sample();s['pairs'][0].update(atomic_post_begin=4000,atomic_post_end=4100)
        with self.assertRaises(ValueError):analyze.metrics(s,1)
    def test_negative_gap_retained(self):
        s=sample();s['pairs'][0].update(atomic_cqe=2500)
        r,_=analyze.metrics(s,1);self.assertEqual(r[0]['gap_us'],-.5)
    def test_exact_overlay_and_python_syntax(self):
        for name in overlay.TARGETS:
            text=subprocess.check_output(['git','-C',str(overlay.REPO),'show',overlay.BASE+':'+name]).decode()
            self.assertNotEqual(text,overlay.changes(name,text))
        for p in HERE.glob('*.py'):ast.parse(p.read_text())
    def test_complete_report_and_missing_sample_rejection(self):
        with tempfile.TemporaryDirectory() as tmp,redirect_stdout(io.StringIO()):
            root=Path(tmp)/'job';b=root/'verbs';b.mkdir(parents=True)
            for n in (0,1):(b/('overlay_node%d.json'%n)).write_text('{"version":1}')
            plan=dict(rounds=1,samples=11)
            for n in (0,1):
                p=b/'cases/job_verbs_run'/('node%d'%n);p.mkdir(parents=True)
                for name,d in [('status',dict(passed=True,correctness=True,points=4)),('worker_request',dict(rounds=1,samples=11,module_sha='x')),
                    ('plan',plan),('affinity',dict(cpu=n,effective=[n])),('runtime',dict(module_sha='x')),
                    ('manifest',dict(module_sha='x')),('path',dict(serial=True,lanes_per_thread=1,control_memory='gpu'))]:
                    (p/(name+'.json')).write_text(json.dumps(d))
                raw=[]
                for label,(tokens,sender) in analyze.LABELS.items():
                    for i in range(11):raw.append(dict(repeat=1,label=label,index=i,tokens=tokens,sender=sender,rank=n,used=i>0,validation=True,
                        sample=sample(tokens//32) if n==sender else dict(receiver_only=True)))
                (p/'samples.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in raw))
            result=analyze.summarize(root,'verbs',root/'summary');self.assertEqual(len(result),4)
            self.assertTrue(all(r['stable'] for r in result))
            p=b/'cases/job_verbs_run/node0/samples.jsonl';p.write_text('\n'.join(p.read_text().splitlines()[1:]))
            with self.assertRaisesRegex(ValueError,'missing'):analyze.summarize(root,'verbs',root/'bad')
    @unittest.skipUnless(os.name=='posix' and shutil.which('c++'),'native compiler')
    def test_native_loop_and_decoder(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary=Path(tmp)/'serial'
            subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror','-I'+str(HERE/'native'),str(HERE/'native/test.cpp'),'-o',str(binary)],check=True)
            output=subprocess.check_output([str(binary)]).decode().splitlines()
            for line,count in zip(output,(1,2,1)):analyze.metrics(json.loads(line),count)
if __name__=='__main__':unittest.main()
