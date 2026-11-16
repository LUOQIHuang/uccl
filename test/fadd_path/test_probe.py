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


class ProbeTests(unittest.TestCase):
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
        for impact in (1.0,1.2):
            with tempfile.TemporaryDirectory() as temporary,redirect_stdout(io.StringIO()):
                root=Path(temporary)/'job';backend='urma';folder=root/backend;folder.mkdir(parents=True)
                for rank in (0,1):(folder/('overlay_node%d.json'%rank)).write_text('{"version":1}')
                for rank in (0,1):
                    for stage in ('pre','probe','post'):
                        case=folder/'cases'/('job_urma_'+stage)/('node%d'%rank)/'normal';case.mkdir(parents=True)
                        if stage=='probe':
                            windows=[]
                            for index in range(10):
                                streams=[]
                                for proxy in range(4):
                                    p,q=record();streams.append(dict(proxy=proxy,dropped=0,errors=0,pairs=[p],polls=q))
                                windows.append(dict(version=1,clock='CLOCK_MONOTONIC_RAW',target_payload=6,ring=0,
                                                    poll_stride=64,label=analyze.LABEL,index=index,streams=streams))
                            (case/'fadd_path.jsonl').write_text(''.join(json.dumps(w)+'\n' for w in windows))
                def case_data(location,rank,*args,**kwargs):
                    stage=location.name.rsplit('_',1)[-1]
                    return dict(case=location/('node%d'%rank)/'normal',identity={'host':'host%d'%rank},
                        runtime=dict(module_sha='fixture',fadd_path_version=1),c=dict(fadd_path_capture=stage=='probe'),
                        means={label:dict(mean_us=10000*(impact if stage=='probe' and label==analyze.LABEL else 1),cv_pct=.2)
                               for label in analyze.base.ALL_LABELS})
                with patch.object(analyze.base,'case_data',side_effect=case_data):
                    decision=analyze.summarize(root,backend,root/'summary')
                self.assertEqual(decision['mechanism_usable'],impact==1)
                self.assertTrue((root/'summary/pairs.csv').exists())

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
        with tempfile.TemporaryDirectory() as temporary:
            binary=Path(temporary)/'probe_test'
            subprocess.run(['c++','-std=c++17','-O2','-pthread','-Wall','-Wextra','-Werror',
                            '-I'+str(native),str(native/'probe_test.cpp'),'-o',str(binary)],check=True)
            window=json.loads(subprocess.check_output([str(binary)]).decode())
            stream=window['streams'][0]
            row=analyze.pair_metrics(stream['pairs'][0],stream['polls'],'urma')
            self.assertEqual(row['marker_status'],'bounded')


if __name__=='__main__':unittest.main()
