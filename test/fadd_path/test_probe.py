"""CPU-only checks for stage bounds, censoring and isolated source overlays."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
import io
from unittest.mock import patch

import analyze
import overlay


def record():
    pair=dict(cq=1,lane=1,write_id=110,atomic_id=111,bytes=459264,operand=32,
              write_post_begin=100,write_post_end=200,atomic_post_begin=300,atomic_post_end=400,
              write_sdk_return=1000,atomic_sdk_return=10000,write_handled=1020,atomic_handled=10020,
              write_poll_seq=1,atomic_poll_seq=130,initial_zero=True,finished=True,status=0,fence=1,order=2,
              result_lower=3000,result_upper=4000,result_value=160,result_at_completion=160)
    polls=[dict(cq=1,seq=1,begin=900,end=1000,count=1),dict(cq=1,seq=65,begin=7000,end=7100,count=0),
           dict(cq=1,seq=129,begin=9500,end=9600,count=0),dict(cq=1,seq=130,begin=0,end=10000,count=1)]
    return pair,polls


def dense_record():
    p,_=record();p.update(write_poll_seq=1,atomic_poll_seq=4,result_lower=3000,result_upper=4000)
    polls=[dict(cq=1,seq=1,begin=900,end=1000,count=1),
           dict(cq=1,seq=2,begin=2000,end=2500,count=0),
           dict(cq=1,seq=3,begin=5000,end=6000,count=0),
           dict(cq=1,seq=4,begin=9000,end=10000,count=1)]
    return p,polls


class ProbeTests(unittest.TestCase):
    def test_dense_partitions_same_request_before_and_after_seen_result(self):
        p,q=dense_record();r=analyze.pair_metrics(p,q,'urma',1)
        self.assertEqual(r['dense_elapsed_us'],9)
        self.assertEqual(r['dense_sdk_us'],2.5)
        self.assertEqual(r['dense_between_sdk_us'],6.5)
        self.assertEqual(r['after_seen_elapsed_us'],6)
        self.assertEqual(r['after_seen_sdk_us'],2)
        self.assertEqual(r['after_seen_between_sdk_us'],4)
        self.assertEqual(r['after_seen_max_between_sdk_us'],3)

    def test_dense_missing_or_overlapping_calls_are_not_filled_in(self):
        p,q=dense_record()
        with self.assertRaisesRegex(ValueError,'missing SDK'):analyze.pair_metrics(p,q[:2]+q[3:],'urma',1)
        q[2]['begin']=2400
        with self.assertRaisesRegex(ValueError,'overlap'):analyze.pair_metrics(p,q,'urma',1)

    def test_dense_same_sdk_batch_is_zero_not_device_zero(self):
        p,q=dense_record();p.update(atomic_sdk_return=1000,atomic_handled=1020,atomic_poll_seq=1,
                                   result_upper=1010)
        r=analyze.pair_metrics(p,q[:1],'urma',1)
        self.assertEqual(r['dense_elapsed_us'],0)
        self.assertEqual(r['dense_between_sdk_us'],0)
        self.assertIsNone(r['after_seen_elapsed_us'])

    def test_dense_marker_missing_preserves_sdk_partition_and_censoring(self):
        p,q=dense_record();p.update(result_upper=10020)
        r=analyze.pair_metrics(p,q,'urma',1)
        self.assertEqual(r['dense_elapsed_us'],9)
        self.assertIsNone(r['after_seen_sdk_us'])

    def test_dense_header_is_pinned_and_default_remains_sparse(self):
        self.assertIn('kStride=1, kLimit=4096',overlay.probe_header(1))
        self.assertIn('kStride=64, kLimit=4096',overlay.probe_header(64))
        with self.assertRaises(ValueError):overlay.probe_header(2)

    def test_response_bounds_and_empty_after_local_return(self):
        p,q=record();r=analyze.pair_metrics(p,q,'urma')
        self.assertEqual(r['gap_us'],9)
        self.assertEqual((r['before_return_lower_us'],r['before_return_upper_us']),(2,3))
        self.assertEqual((r['after_return_lower_us'],r['after_return_upper_us']),(6,7))
        self.assertEqual(r['empty_polls_after_return'],2)
        self.assertEqual(r['last_empty_tail_upper_us'],.5)
        self.assertEqual(r['max_poll_checkpoint_gap_us'],6)
        self.assertEqual(r['sdk_poll_calls_between'],129)
        self.assertEqual(r['sdk_empty_calls_between'],128)

    def test_late_marker_is_censored_not_relabelled_as_hardware_time(self):
        p,q=record();p['result_upper']=10010
        r=analyze.pair_metrics(p,q,'urma')
        self.assertEqual(r['marker_status'],'not_seen_before_cqe')
        self.assertIsNone(r['before_return_upper_us'])
        p['result_at_completion']=0;r=analyze.pair_metrics(p,q,'urma')
        self.assertEqual(r['marker_status'],'return_value_zero_unusable')

    def test_shared_verbs_scratch_cannot_be_a_per_request_marker(self):
        p,q=record();p['fence']=0;p['initial_zero']=False
        row=analyze.pair_metrics(p,q,'verbs')
        self.assertEqual(row['marker_status'],'shared_verbs_scratch_not_sampled')
        self.assertIsNone(row['after_return_upper_us'])
        self.assertEqual(row['gap_us'],9)

    def test_bad_pair_or_missing_poll_fails(self):
        p,q=record();p['atomic_id']=112
        with self.assertRaisesRegex(ValueError,'adjacent'):analyze.pair_metrics(p,q,'urma')
        p,q=record()
        with self.assertRaisesRegex(ValueError,'actual SDK poll'):analyze.pair_metrics(p,q[:-1],'urma')

    def test_reverse_completion_order_is_retained(self):
        p,q=record();p.update(write_sdk_return=11000,write_handled=11020,write_poll_seq=131)
        q.append(dict(cq=1,seq=131,begin=0,end=11000,count=1))
        r=analyze.pair_metrics(p,q,'urma')
        self.assertEqual(r['gap_us'],-1)
        self.assertEqual(r['marker_status'],'negative_completion_gap')

    def test_full_report_preserves_failed_perturbation_gate(self):
        for impact,poll_stride in ((1.0,64),(1.2,64),(1.0,1),(1.2,1)):
            with tempfile.TemporaryDirectory() as temporary,redirect_stdout(io.StringIO()):
                root=Path(temporary)/'job';backend='urma';folder=root/backend;folder.mkdir(parents=True)
                for rank in (0,1):(folder/('overlay_node%d.json'%rank)).write_text(json.dumps(dict(version=1,poll_stride=poll_stride)))
                for rank in (0,1):
                    for stage in ('pre','probe','post'):
                        case=folder/'cases'/('job_urma_'+stage)/('node%d'%rank)/'normal';case.mkdir(parents=True)
                        if stage=='probe':
                            windows=[]
                            for index in range(10):
                                streams=[]
                                for proxy in range(4):
                                    p,q=dense_record() if poll_stride==1 else record()
                                    streams.append(dict(proxy=proxy,dropped=0,errors=0,pairs=[p],polls=q))
                                windows.append(dict(version=1,clock='CLOCK_MONOTONIC_RAW',target_payload=6,ring=0,
                                                    poll_stride=poll_stride,label=analyze.LABEL,index=index,streams=streams))
                            (case/'fadd_path.jsonl').write_text(''.join(json.dumps(w)+'\n' for w in windows))
                def case_data(location,rank,*args,**kwargs):
                    stage=location.name.rsplit('_',1)[-1]
                    return dict(case=location/('node%d'%rank)/'normal',identity={'host':'host%d'%rank},
                        runtime=dict(module_sha='fixture',fadd_path_version=1),c=dict(fadd_path_capture=stage=='probe'),
                        means={label:dict(mean_us=10000*(impact if stage=='probe' and label==analyze.LABEL else 1),cv_pct=.2)
                               for label in analyze.base.ALL_LABELS})
                with patch.object(analyze.base,'case_data',side_effect=case_data):
                    decision=analyze.summarize(root,backend,root/'summary',poll_stride)
                self.assertEqual(decision['mechanism_usable'],impact==1)
                self.assertTrue((root/'summary/pairs.csv').exists())
                if poll_stride==1:self.assertIn('after_seen_between_sdk_us',(root/'summary/pairs.csv').read_text())

    def test_overlay_anchors_match_pinned_sources_and_do_not_edit_checkout(self):
        for name in overlay.TARGETS:
            source=subprocess.check_output(['git','-C',str(overlay.REPO),'show',overlay.BASE+':'+name]).decode()
            modified=overlay.changes(name,source)
            self.assertNotEqual(source,modified)
            if name.endswith('.py'):compile(modified,name,'exec')
        # The actual Normal/LL GPU kernels and active measurement code stay at
        # the pinned commit; only a new standalone test directory is added.
        for name in ('ep/src/internode.cu','ep/src/internode_ll.cu','ep/src/urma_transport.cpp',
                     'ep/include/verbs_stage_diag.hpp','test/reference_events/event_runtime.py'):
            expected=subprocess.check_output(['git','-C',str(overlay.REPO),'show',overlay.BASE+':'+name]).decode().replace('\r\n','\n')
            self.assertEqual((overlay.REPO/name).read_text(),expected)

    @unittest.skipUnless(os.name=='posix' and shutil.which('c++'),'native CPU compiler')
    def test_native_export_matches_python_decoder(self):
        native=Path(__file__).with_name('native')
        for stride in (64,1):
            with tempfile.TemporaryDirectory() as temporary:
                folder=Path(temporary);binary=folder/'probe_test'
                (folder/'probe.hpp').write_text(overlay.probe_header(stride))
                shutil.copy2(str(native/'probe_test.cpp'),str(folder/'probe_test.cpp'))
                subprocess.run(['c++','-std=c++17','-O2','-pthread','-Wall','-Wextra','-Werror',
                                '-I'+str(folder),str(folder/'probe_test.cpp'),'-o',str(binary)],check=True)
                window=json.loads(subprocess.check_output([str(binary)]).decode())
                self.assertEqual(window['poll_stride'],stride)
                stream=window['streams'][0]
                row=analyze.pair_metrics(stream['pairs'][0],stream['polls'],'urma',stride)
                self.assertEqual(row['marker_status'],'bounded')
                if stride==1:
                    self.assertAlmostEqual(row['dense_sdk_us']+row['dense_between_sdk_us'],row['gap_us'])


if __name__=='__main__':unittest.main()
