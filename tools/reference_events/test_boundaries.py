import copy
from contextlib import redirect_stdout
import csv
import io
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'test/reference_events'))
spec=importlib.util.spec_from_file_location('boundary_fixtures',ROOT/'test/reference_events/test_boundaries.py')
fixtures=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixtures)
import summarize_boundaries as summary
from test_matched import fixtures as legacy_fixtures


def complete_window(label,index):
    window=fixtures.boundary_window(label,index);events=[]
    for channel in range(12):
        proxy,ring=channel%4,channel//4
        for ordinal in range(1,12):
            wid=(ring<<32)|ordinal;start=3000+ordinal*100
            for stage,offset in [('poll',0),('decoded',10),('collect',15),('prepare',20),('post_begin',30),('post_end',40),('cqe',50),('ack_slot',60)]:
                events.append(dict(proxy=proxy,wr_id=wid,stage=stage,ns=start+offset,value=0,qp=ring))
            events.append(dict(proxy=proxy,wr_id=wid,stage='payload_bytes',ns=start,value=459264,qp=ring))
    for proxy in range(4):
        for ns in (100,10000):events.append(dict(proxy=proxy,wr_id=2**64-1,stage='poll_heartbeat',ns=ns,value=1,qp=0))
    window['cpu']['events']=events
    return window


class BoundaryAnalysisTests(unittest.TestCase):
    def test_full_controls_export_and_changed_post_rejection(self):
        with tempfile.TemporaryDirectory() as temporary,redirect_stdout(io.StringIO()):
            root=Path(temporary)
            _,template,_=legacy_fixtures.fixtures.DispatchTests().fixture(root/'template','urma')
            for tag,mode in [('paced','paced'),('trace','on'),('post','paced')]:
                target=root/tag;shutil.copytree(template,target)
                for rank in (0,1):
                    node=target/f'node{rank}';case=node/'normal'
                    data=json.loads((node/'setup.json').read_text());data['fingerprint'].update(gpu_stages=False,reference_events=mode,reference_profile='boundaries');legacy_fixtures.dump(node/'setup.json',data)
                    data=json.loads((case/'runtime.json').read_text());data.update(gpu_stage_version=0,reference_events_version=1,reference_boundary_version=1,reference_profile='boundaries');legacy_fixtures.dump(case/'runtime.json',data)
                    data=json.loads((node/'build_manifest.json').read_text());data['flags'].update(UCCL_GPU_STAGE_TRACE='0',UCCL_REFERENCE_EVENTS='1');legacy_fixtures.dump(node/'build_manifest.json',data)
                    (case/'gpu_stages.jsonl').unlink();status=json.loads((case/'status.json').read_text())
                    if mode=='on':
                        windows=[complete_window(label,i) for label in fixtures.LABELS for i in range(10)]
                        legacy_fixtures.lines(case/'event_trace.jsonl',windows)
                        legacy_fixtures.dump(case/'reference_correctness.json',dict(windows[0]['gpu'],verified=True))
                        status['checks'].update(reference_correctness=True,reference_events=True)
                    else:
                        status['checks']['reference_control']=True
                        with patch.dict('os.environ',{'UCCL_REFERENCE_PROFILE':'boundaries'}):
                            for label in fixtures.LABELS:fixtures.control_marker(case,label,'paced',5,10)
                    legacy_fixtures.dump(case/'status.json',status)
            result=summary.summarize(root/'paced',root/'trace',root/'post',root/'summary')
            self.assertTrue(all(r['representative'] for r in result))
            with (root/'summary/cpu_chunk6_and_last.csv').open() as stream:self.assertEqual(len(list(csv.DictReader(stream))),864)
            host=root/'post/node1/host.json';data=json.loads(host.read_text());data['boot_id']='changed';legacy_fixtures.dump(host,data)
            with self.assertRaisesRegex(ValueError,'changed source/module/boot'):summary.summarize(root/'paced',root/'trace',root/'post',root/'bad')

    def test_same_role_spans_and_signed_finish_gaps(self):
        window=fixtures.boundary_window('dispatch_bf16_chunk32',1)
        rows=summary.spans(window)
        self.assertEqual(len(rows),12*9)
        epoch=next(r for r in rows if r['channel']==0 and r['span']=='final_epoch_check')
        self.assertAlmostEqual(epoch['us'],.1);self.assertEqual(epoch['device_cycles'],100)
        bad=copy.deepcopy(window)
        next(r for r in bad['gpu']['records'] if r['event']==2 and r['block']==0)['device_before']=1
        with self.assertRaisesRegex(ValueError,'non-monotonic same-role'):summary.spans(bad)

    def test_order_based_cpu_focus_requires_complete_channels_and_bracketed_heartbeat(self):
        window=fixtures.boundary_window('dispatch_bf16_chunk32',1);window['cpu']['events']=[]
        for channel in range(12):
            proxy,ring=channel%4,channel//4
            for ordinal in range(1,12):
                wid=(ring<<32)|ordinal;start=3000+ordinal*100
                for stage,offset in [('poll',0),('decoded',10),('prepare',20),('post_begin',30),('post_end',40),('cqe',50),('ack_slot',60)]:
                    window['cpu']['events'].append(dict(proxy=proxy,wr_id=wid,stage=stage,ns=start+offset,value=0))
                window['cpu']['events'].append(dict(proxy=proxy,wr_id=wid,stage='payload_bytes',ns=start,value=459264))
        for proxy in range(4):
            for ns in (100,10000):window['cpu']['events'].append(dict(proxy=proxy,wr_id=2**64-1,stage='poll_heartbeat',ns=ns,value=1))
        rows=summary.cpu_focus(window)
        self.assertEqual(len(rows),24)
        self.assertTrue(all(r['heartbeat_full_bracket'] for r in rows))
        self.assertEqual({r['channel'] for r in rows},set(range(12)))
        self.assertEqual({r['payload_ordinal'] for r in rows},{6,11})
        self.assertTrue(all(r['heartbeat_max_observed_gap_us']==9.9 for r in rows))
        window['cpu']['events']=[e for e in window['cpu']['events'] if e['proxy']!=3]
        with self.assertRaisesRegex(ValueError,'complete payload'):summary.cpu_focus(window)


if __name__=='__main__':unittest.main()
