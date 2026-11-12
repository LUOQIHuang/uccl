from contextlib import redirect_stdout
import copy
import ctypes
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace as NS
from event_runtime import BoundaryTrace,Trace,BOUNDARY_NAMES,validate,validate_control,control_marker
from test_reference import window,dump,lines,LABELS
from test_window_plan import driver_functions


def boundary_window(label,index=0,tokens=4096):
    data=window(label,index);gpu=data['gpu'];gpu.update(profile='dispatch_boundaries_v1',records=[],counts=[0]*12)
    order={0:0,1:1,10:2,11:3,3:4,4:5,5:6,6:7,7:8,2:9,8:10,9:11}
    for channel in range(12):
        for event in range(12 if tokens==4096 else 10):
            odd=event in (0,1,3,4,9,10,11);warp=0 if event==0 or not odd else (8 if event==9 else 7)
            ns=1000+order[event]*100
            gpu['records'].append(dict(event=event,slot=channel,name=BOUNDARY_NAMES[event],host_ns=ns,
                device_before=1000+order[event]*100,device_after=1010+order[event]*100,
                block=channel*2+int(odd),warp=warp,epoch=index+1))
            gpu['counts'][event]+=1
    data['cpu']['heartbeat_stride']=128
    data['cpu']['events'].append(dict(proxy=0,wr_id=2**64-1,qp=0,ns=100,stage='poll_heartbeat',value=1))
    return data


class BoundaryTests(unittest.TestCase):
    def test_abi_and_profile_coverage(self):
        self.assertEqual(ctypes.sizeof(Trace),1769504)
        self.assertEqual(ctypes.sizeof(BoundaryTrace),3539000)
        with tempfile.TemporaryDirectory() as temporary:
            case=Path(temporary);data=[boundary_window(label,i) for label in LABELS for i in range(3)]
            lines(case/'event_trace.jsonl',data);self.assertTrue(validate(case,3,'boundaries'))
            with self.assertRaisesRegex(ValueError,'profile/ABI'):validate(case,3)
            for mutate in [lambda w:w['gpu']['records'].pop(),lambda w:w['gpu']['records'][0].update(warp=9),lambda w:w['cpu'].pop('heartbeat_stride')]:
                bad=copy.deepcopy(data);mutate(bad[0]);lines(case/'event_trace.jsonl',bad)
                with self.assertRaises(ValueError):validate(case,3,'boundaries')
            lines(case/'event_trace.jsonl',[boundary_window(label,i,128) for label in LABELS for i in range(3)])
            self.assertTrue(validate(case,3,'boundaries',128))

    def test_control_marks_boundary_profile_without_capture(self):
        with tempfile.TemporaryDirectory() as temporary,patch.dict(os.environ,{'UCCL_REFERENCE_PROFILE':'boundaries'}):
            case=Path(temporary)
            for label in LABELS:control_marker(case,label,'paced',5,10)
            self.assertTrue(validate_control(case,'paced',5,10,'boundaries'))
            with self.assertRaises(ValueError):validate_control(case,'paced',5,10)

    def test_parser_profile_and_preflight_capability_on_both_backends(self):
        for backend in ('urma','verbs'):
            ns=driver_functions(backend)
            argv=['driver','--node-rank','0','--job','bound','--mode','normal','--dispatch-focus','--reference-events','on','--reference-profile','boundaries']
            argv+=['--phase-suite','--api-only'] if backend=='urma' else ['--variant','current','--no-profile','--repeats','1']
            with patch.object(sys,'argv',argv):args=ns['parser']()
            self.assertEqual(ns['environment'](args)['UCCL_REFERENCE_PROFILE'],'boundaries')
            for extra in (['--normal-window-plan','peer4'],['--reference-events','off']):
                with patch.object(sys,'argv',argv+extra),patch('sys.stderr',io.StringIO()),self.assertRaises(SystemExit):ns['parser']()
            manifest=dict(sources={},module='ep.so',module_sha='sha',flags={'UCCL_REFERENCE_EVENTS':'1','UCCL_GPU_STAGE_TRACE':'0'})
            if backend=='urma':
                ns['support']=NS(read_build=lambda p:(Path('ep.so'),manifest));ns['urma_support']=NS(library_manifest=lambda p:{},hardware_check=lambda a:{})
            else:ns['common']=NS(sources=lambda:{},sha=lambda p:'sha',digest=lambda d:'hash')
            for version in (0,1):
                metadata=dict(phase_diagnostics=True,dispatch_trace_version=1,reference_events_version=1,reference_profile='boundaries',reference_boundary_version=version,loaded_libraries=[])
                prefix='MODE_PARITY_IMPORT_OK ' if backend=='urma' else 'VERBS_IMPORT_OK '
                ns['subprocess']=NS(run=lambda *a,**kw:NS(returncode=0,stdout=prefix+json.dumps(metadata),stderr=''))
                with patch.object(Path,'read_text',return_value=json.dumps(manifest)):
                    if version:ns['preflight'](args)
                    else:
                        with self.assertRaisesRegex(RuntimeError,'BOUNDARY_PROFILE_REQUIRES_REBUILD'):ns['preflight'](args)


if __name__=='__main__':unittest.main()
