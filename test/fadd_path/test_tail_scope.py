"""Verify the isolated receiver/sender recipe without requiring a GPU."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import analyze
import analyze_tail_scope as tail
import overlay
import runner
from test_probe import dense_record


def isolated_source(path):
    source = subprocess.check_output(['git','-C',str(overlay.REPO),'show',overlay.TAIL_BASE+':'+path]).decode()
    if path in overlay.TARGETS:
        source = overlay.changes(path, source)
    return overlay.tail_changes(path, source)


class TailScopeTest(unittest.TestCase):
    def test_receiver_and_sender_probes_are_disjoint_and_keep_calls_paired(self):
        namespace = {}
        exec(compile(isolated_source('test/reference_events/event_runtime.py'), '<tail-runtime>', 'exec'), namespace)
        for active, rank in ((True,0),(True,1),(False,0),(False,1)):
            with self.subTest(active=active, rank=rank), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp)
                steps = []
                class Event:
                    def __init__(self, enable_timing): pass
                    def record(self): pass
                    def elapsed_time(self, end): return 8.0
                class Capture:
                    def __init__(self, path): pass
                    def begin(self): steps.append('gpu-begin')
                    def end(self): steps.append('gpu-end'); return {}
                torch = SimpleNamespace(cuda=SimpleNamespace(synchronize=lambda:None, current_device=lambda:0,Event=Event),
                                        empty=lambda *a,**k:SimpleNamespace(zero_=lambda:None), int='int')
                dist = SimpleNamespace(get_rank=lambda:rank, barrier=lambda:steps.append('barrier'))
                def finish():
                    steps.append('cpu-end')
                    return '{}'
                ep = SimpleNamespace(__file__='module.so', get_fadd_path_version=lambda:1,
                                     fadd_path_begin=lambda:steps.append('cpu-begin'), fadd_path_end=finish)
                with patch.dict(os.environ, FADD_PATH_TAIL_SCOPE='1', FADD_PATH_CAPTURE=str(int(active)), UCCL_REFERENCE_PROFILE='legacy'):
                    namespace['measure'](torch,dist,ep,path,lambda:steps.append('dispatch'),tail.LABEL,0,2,
                                         capture_factory=Capture,record_events=False)
                    self.assertTrue(namespace['validate_control'](path,'paced',0,2,labels={tail.LABEL}))
                self.assertEqual((path/'tail_receiver.jsonl').is_file(),active and rank==0)
                self.assertEqual((path/'fadd_path.jsonl').is_file(),active and rank==1)
                self.assertFalse((path/'event_trace.jsonl').exists())
                expected = {0:['barrier','gpu-begin','barrier','dispatch','barrier','gpu-end','barrier'],
                            1:['barrier','barrier','cpu-begin','dispatch','barrier','cpu-end','barrier']}
                self.assertEqual(steps,2*(expected[rank] if active else ['barrier','barrier','dispatch','barrier','barrier']))

    def test_bounds_are_retained_for_result_seen_after_cqe(self):
        pair, polls = dense_record()
        pair.update(fence=0, result_lower=9500, result_upper=10200)
        with self.assertRaisesRegex(ValueError,'fence/STRONG'):
            analyze.pair_metrics(pair,polls,'urma',1)
        analyze.pair_metrics(pair,polls,'urma',1,expected_fence=0)
        bounds = analyze.tail_result_bounds(pair,polls)
        self.assertEqual(bounds['result_to_cqe_lower_us'],0)
        self.assertEqual(bounds['result_to_cqe_upper_us'],0.5)
        self.assertFalse(bounds['result_observed_before_cqe'])

    def test_scope_commands_keep_fence_order_and_control_scope(self):
        self.assertEqual([(plan,active) for _,plan,active in runner.TAIL_RUNS],
                         [('all-on',False),('peer-tail-off',False),('all-on',True),
                          ('peer-tail-off',True),('peer-tail-off',False),('all-on',False)])
        for path in ('ep/bench/test_internode.py','test/urma_mode_parity/worker.py','test/urma_mode_parity/driver.py'):
            compile(isolated_source(path),path,'exec')

    def test_receiver_checks_every_channel_and_keeps_local_spans(self):
        with tempfile.TemporaryDirectory() as tmp:
            case = Path(tmp)
            records = [dict(event=e,block=2*i+int(e!=2),slot=i,host_ns=(e+1)*1000+i,
                            device_before=1,device_after=1) for e in (0,1,2) for i in range(12)]
            def export(records):
                windows = [dict(index=i,label=tail.LABEL,us=8000,gpu=dict(version=1,epoch=i+1,
                                clock='CLOCK_MONOTONIC_RAW',dropped=0,counts=[12,12,12,0,0,0],
                                clock_stats=dict(updates=10,scheduler_error=0,max_gap_ns=200),
                                records=[dict(epoch=i+1,**r) for r in records])) for i in range(10)]
                (case/'tail_receiver.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in windows))
            api = {i:8000 for i in range(10)}
            export(records)
            result = tail.receiver_rows(case,api)
            self.assertEqual(len(result),9)
            self.assertEqual(result[0]['first_write_to_last_visible_us'],1.011)
            export(records[:-1])
            with self.assertRaisesRegex(ValueError,'missing sender/receiver channel'):
                tail.receiver_rows(case,api)


if __name__=='__main__':
    unittest.main()
