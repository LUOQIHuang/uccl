import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
HERE=Path(__file__).resolve().parents[1];sys.path.insert(0,str(HERE))
import common
import archived_base
import driver
from notify_support import matched_geometries
from summarize_normal_focus import urma_micro,summarize
from notify_support import points

class NormalFocusTests(unittest.TestCase):
    def test_archived_preflight_uses_pin_and_rejects_trace_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'lib').mkdir();binary=root/'lib/ep.abi3.so';binary.write_bytes(b'fixture')
            manifest=json.loads(archived_base.RECORD.read_text());manifest['module_sha']=common.sha(binary);(root/'manifest.json').write_text(json.dumps(manifest))
            args=SimpleNamespace(variant='archived',build_dir=root,expected_kernel=None,hca='fake',ifname='eth',gpu='0',gid_index=None,notify_check=True,campaign_check=True,cpu_diagnostics=True,gpu_stages=False,micro_suite='notify')
            info=dict(verbs_notify_version=1,verbs_diag_version=1,verbs_micro=True,gpu_stage_version=0,queue_backend='fifo',loaded_libraries=[])
            reply=SimpleNamespace(returncode=0,stdout='VERBS_IMPORT_OK '+json.dumps(info),stderr='')
            with patch.object(driver.os,'uname',create=True,return_value=SimpleNamespace(release=archived_base.KERNEL)),patch.object(driver.subprocess,'run',return_value=reply),patch.object(common,'sources',side_effect=AssertionError('must verify archived inputs, not current sources')):
                self.assertEqual(driver.preflight(args)[2]['kind'],'pinned_archived_source_build')
                info['gpu_stage_version']=1;reply.stdout='VERBS_IMPORT_OK '+json.dumps(info)
                with self.assertRaisesRegex(RuntimeError,'TRACE_OFF_FIFO'):driver.preflight(args)
    def test_archive_checks_inputs_sha_and_frozen_python(self):
        reference=json.loads(archived_base.RECORD.read_text())
        self.assertEqual(common.digest(reference),archived_base.RECORD_DIGEST)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'lib').mkdir();binary=root/'lib/ep.abi3.so';binary.write_bytes(b'CPU fixture only')
            manifest=copy.deepcopy(reference);manifest['module_sha']=common.sha(binary);(root/'manifest.json').write_text(json.dumps(manifest))
            module,record,identity=archived_base.load(root)
            self.assertEqual(module,binary.resolve());self.assertEqual(identity['kind'],'pinned_archived_source_build')
            binary.write_bytes(b'changed')
            with self.assertRaisesRegex(RuntimeError,'SHA_MISMATCH'):archived_base.load(root)
            manifest['flags']['UCCL_GPU_STAGE_TRACE']='1';(root/'manifest.json').write_text(json.dumps(manifest))
            with self.assertRaisesRegex(RuntimeError,'INPUTS_NOT_PINNED'):archived_base.load(root)
            bundle=root/'bundle';bundle.mkdir();provenance=common.prepare(bundle,'archived')
            self.assertEqual(provenance['benchmark_revision'],archived_base.REVISION)
            self.assertEqual((bundle/'buffer.py').read_text(),common.git('show',archived_base.REVISION+':ep/bench/buffer.py'))
    def test_real_plan_is_matched_and_rejects_changed_operations(self):
        path=common.HERE.parent/'urma_mode_parity/results/urma_notify_05/urma_notify_05_micro_r01/node0/micro/plan.json'
        geom=matched_geometries(path)
        self.assertEqual([g['bytes'] for g in geom],[237056,459264])
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'plan.json';data=json.loads(path.read_text());data['points'][0]['operation']=2;p.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError,'operation/geometry'):matched_geometries(p)
    def test_historical_urma_raw_samples_validate_independently(self):
        groups,contexts=urma_micro(common.HERE.parent/'urma_mode_parity/results/urma_notify_05')
        self.assertEqual((len(groups),len(contexts)),(16,6));self.assertTrue(all(len(v)==3 for v in groups.values()))
    def test_five_job_summary_and_historical_comparison(self):
        history=common.HERE.parent/'urma_mode_parity/results/urma_notify_05'
        def dump(path,data):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(data))
        def lines(path,data):path.write_text('\n'.join(json.dumps(r) for r in data))
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for rank in (0,1):
                for kind in ('notify_r01','notify_r02','notify_r03','baseline','cpu'):
                    node=root/('job_'+kind)/f'node{rank}';micro=kind.startswith('notify');case=node/('micro' if micro else 'cpu_diagnostic/normal' if kind=='cpu' else 'r01_baseline/normal');case.mkdir(parents=True)
                    old=history/'urma_notify_05_micro_r01'/f'node{rank}'
                    c=dict(job='job_'+kind,identity={'pinned':'fixture'},kernel='stock',torch='2.6.0+metax3.7.0.7',libraries={},diagnostics={},gid_index=3,hca='fake',mode='normal',variant='archived',warmups=20 if micro else 50,samples=50,cpu_diagnostics=kind=='cpu',cpu_light=kind=='cpu')
                    if micro:c.update(suite='verbs_notify_micro',threads=4,repeat=int(kind[-1]))
                    dump(node/'setup.json',dict(error=None,fingerprint=c));dump(node/'host.json',dict(host=json.loads((old/'host.json').read_text())['host'],boot_id=str(rank)))
                    dump(node/'module_manifest.json',dict(sources={'a':'b'},flags={'UCCL_GPU_STAGE_TRACE':'0'},module_sha='fixture'))
                    dump(case/'status.json',dict(rc=0,checks={'done':True}));dump(case/'runtime.json',dict(gpu_stage_version=0,module_sha='fixture'))
                    if micro:
                        old=history/('urma_notify_05_micro_r0'+kind[-1])/f'node{rank}'/'micro'
                        geometries=matched_geometries(old/'plan.json');plan=points(geometries,int(kind[-1]))
                        dump(case/'plan.json',dict(geometries=geometries,points=plan));dump(case/'request.json',dict(fingerprint=c))
                        placement=json.loads((old/'affinity_pair.json').read_text());dump(case/'affinity_pair.json',placement);dump(node/'affinity.json',placement[str(rank)]);dump(case/'request.json',dict(fingerprint=c,placement=placement[str(rank)]))
                        dump(case/'verbs_path.json',dict(notifications=True,control_memory='gpu',atomic_return_memory='host',registration='dmabuf',gid_index=3,fadd_send_fence=False))
                        raw=[json.loads(x) for x in (old/'micro_samples.jsonl').read_text().splitlines()]
                        for r in raw:
                            p=next(p for p in plan if p['label']==r['label']);r.update(threads=4,visible=p['visible'],src_mod=p['geometry']['src_mod'],dst_mod=p['geometry']['dst_mod'])
                        lines(case/'micro_samples.jsonl',raw)
                        saved=[json.loads(x) for x in (old/'micro_summary.jsonl').read_text().splitlines()];lines(case/'micro_summary.jsonl',saved)
                    else:
                        dump(case/'config.json',dict(shape=dict(tokens=4096,hidden=7168,experts=256,topk=8)))
                        labels=['layout','dispatch_fp8_chunk32','dispatch_bf16_chunk32','combine_chunk12','combine_chunk16']
                        lines(case/'api_samples.jsonl',[dict(label=l,index=i,used=i>0,us=103 if kind=='cpu' else 100) for l in labels for i in range(50)])
                        if kind=='cpu':lines(case/'verbs_phases.jsonl',[dict(label=l,records=[dict(metrics={'sdk_post':dict(calls=50,ns=50000,items=100)})]) for l in labels])
            summarize(root,'job',root/'summary',urma=history)
            import csv
            with (root/'summary/notification_comparison.csv').open() as stream:comparison=list(csv.DictReader(stream))
            self.assertEqual(len(comparison),16);self.assertTrue(all(float(r['urma_over_verbs'])==1 for r in comparison))
            path=root/'job_notify_r01/node0/micro/micro_samples.jsonl';raw=[json.loads(x) for x in path.read_text().splitlines()];raw[0]['write_bytes']+=1;lines(path,raw)
            with self.assertRaisesRegex(ValueError,'CQE/bytes'):summarize(root,'job',root/'summary',urma=history)

if __name__=='__main__':unittest.main()
