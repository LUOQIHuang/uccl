import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
HERE=Path(__file__).resolve().parent
def load(name):
    spec=importlib.util.spec_from_file_location('recovery_'+name,HERE/(name+'.py'));module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
validator=load('validate');collector=load('collect')
def dump(p,data):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(data))
def lines(p,data):p.write_text('\n'.join(json.dumps(r) for r in data))
def fixture(root,verbs,mode):
    trace=mode!='cpu'
    stages={'cpu':[],'bypass':[],'access':[41,42,46,48],'clock':[46],'kernels':[46,48],'waits':[0],'operators':[40,41,42],'all':[40,41,42,46,48]}[mode]
    for rank in (0,1):
        node=root/f'node{rank}';case=node/('cpu_diagnostic/normal' if verbs else 'normal');case.mkdir(parents=True)
        c=dict(mode='normal',variant='current' if verbs else None,normal_tokens=128,shape=[128,128,7168,256,8],samples=3,warmups=1,gpu_stages=trace,gpu_stage_mode=mode,kernel='k')
        dump(node/'setup.json',dict(error=None,fingerprint=c));dump(node/('module_manifest.json' if verbs else 'build_manifest.json'),dict(sources={'a':'b'},flags={'UCCL_GPU_STAGE_TRACE':str(int(trace))}))
        dump(case/'status.json',dict(rc=0,checks={'done':True}));dump(case/'runtime.json',dict(gpu_stage_version=2 if trace else 0))
        lines(case/'api_samples.jsonl',[dict(label=l,index=i,used=i>0,us=100) for l in validator.LABELS for i in range(3)])
        windows=[dict(label=l,index=i,dropped=0,events=[]) for l in validator.LABELS for i in range(3)] if trace else [dict(label=l,index=0,dropped=0,events=[]) for l in validator.LABELS if l.startswith('dispatch_')]
        lines(case/('verbs_timeline.jsonl' if verbs else 'urma_timeline.jsonl'),windows)
        lines(case/('verbs_phases.jsonl' if verbs else 'phases.jsonl'),[dict(label=l,records=[]) for l in validator.LABELS])
        if trace:lines(case/'gpu_stages.jsonl',[dict(version=2,storage='device_private_slots',mode=mode,label=l,index=i,records=[dict(block=0,warp=0,sample_lane=0,stage=k,calls=1,sum_cycles=0 if mode=='access' else 10,max_cycles=0 if mode=='access' else 10) for k in stages] if l!='layout' else []) for l in validator.LABELS for i in range(3)])

class RecoveryTests(unittest.TestCase):
    def test_each_group_both_backends_and_local_collection(self):
        for verbs in (False,True):
            for mode in ('cpu','bypass','access','clock','kernels','waits','operators','all'):
                with tempfile.TemporaryDirectory() as tmp:
                    root=Path(tmp);fixture(root,verbs,mode);validator.validate(root);validator.validate(root,0)
    def test_reject_old_module_partial_window_and_wrong_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);fixture(root,False,'all');case=root/'node0/normal'
            dump(case/'runtime.json',dict(gpu_stage_version=1))
            with self.assertRaisesRegex(ValueError,'old GPU'):validator.validate(root)
            dump(case/'runtime.json',dict(gpu_stage_version=2));windows=validator.rows(case/'gpu_stages.jsonl');lines(case/'gpu_stages.jsonl',windows[:-1])
            with self.assertRaisesRegex(ValueError,'missing GPU'):validator.validate(root)
            windows[0]['mode']='access';lines(case/'gpu_stages.jsonl',windows)
            with self.assertRaisesRegex(ValueError,'mode mismatch'):validator.validate(root)
    def test_collector_preserves_failure_and_excludes_binaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'job';node=root/'node0';node.mkdir(parents=True)
            (node/'bench.log').write_text('failed');(node/'ep.so').write_bytes(b'binary');(node/'cache').mkdir();(node/'cache/skip.json').write_text('{}')
            dest=Path(tmp)/'out';collector.collect(root,0,{},dest)
            self.assertEqual((dest/'node0/bench.log').read_text(),'failed');self.assertFalse((dest/'node0/ep.so').exists());self.assertFalse((dest/'node0/cache').exists())
            (node/'bench.log').write_text('changed')
            with self.assertRaisesRegex(ValueError,'conflict'):collector.collect(root,0,{},dest)

if __name__=='__main__':unittest.main()
