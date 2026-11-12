import ast
import argparse
import io
import json
import os
import re
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from types import SimpleNamespace as NS

def driver_functions(backend):
    path=Path(__file__).resolve().parents[1]/('verbs_baseline' if backend=='verbs' else 'urma_mode_parity')/'driver.py'
    source=ast.parse(path.read_text())
    source.body=[n for n in source.body if isinstance(n,ast.FunctionDef) and n.name in ('parser','environment','preflight','build_flags')]
    namespace=dict(argparse=argparse,Path=Path,os=os,re=re,HERE=path.parent,sys=sys,json=json,BUILD_FLAGS={})
    exec(compile(source,str(path),'exec'),namespace)
    return namespace


class WindowPlanTests(unittest.TestCase):
    def test_preflight_rejects_missing_or_ignored_effective_window(self):
        for backend in ('verbs','urma'):
            ns=driver_functions(backend)
            argv=['driver','--node-rank','1','--job','window','--mode','normal','--dispatch-focus','--reference-events','paced','--normal-window-plan','peer4']
            argv+=['--variant','current','--no-profile','--repeats','1'] if backend=='verbs' else ['--phase-suite','--api-only']
            with patch.object(sys,'argv',argv):args=ns['parser']()
            manifest=dict(sources={},module='ep.so',module_sha='sha',flags={'UCCL_REFERENCE_EVENTS':'1','UCCL_GPU_STAGE_TRACE':'0'})
            if backend=='urma':
                ns['support']=NS(read_build=lambda p:(Path('ep.so'),manifest))
                ns['urma_support']=NS(library_manifest=lambda p:{},hardware_check=lambda p:{})
            else:
                ns['common']=NS(sources=lambda:{},sha=lambda p:'sha',digest=lambda d:'hash')
            for value in (None,8,4):
                metadata=dict(phase_diagnostics=True,dispatch_trace_version=1,reference_events_version=1,normal_max_inflight=value,loaded_libraries=[])
                prefix='MODE_PARITY_IMPORT_OK ' if backend=='urma' else 'VERBS_IMPORT_OK '
                ns['subprocess']=NS(run=lambda *a,**kw:NS(returncode=0,stdout=prefix+json.dumps(metadata),stderr=''))
                with patch.object(Path,'read_text',return_value=json.dumps(manifest)):
                    if value==4:ns['preflight'](args)
                    else:
                        with self.assertRaisesRegex(RuntimeError,'NORMAL_WINDOW_RUNTIME_MISMATCH_OR_REBUILD_REQUIRED'):ns['preflight'](args)

    def test_both_drivers_choose_only_node1_and_clear_inherited_tuning(self):
        for backend in ('verbs','urma'):
            ns=driver_functions(backend)
            base=['driver','--node-rank','1','--job','window','--mode','normal','--dispatch-focus','--reference-events','paced','--normal-window-plan','peer4']
            base+=['--variant','current','--no-profile','--repeats','1'] if backend=='verbs' else ['--phase-suite','--api-only']
            with patch.object(sys,'argv',base):args=ns['parser']()
            self.assertEqual(ns['environment'](args)['UCCL_IB_MAX_INFLIGHT_NORMAL'],'4')
            args.node_rank=0
            self.assertEqual(ns['environment'](args)['UCCL_IB_MAX_INFLIGHT_NORMAL'],'8')
            args.node_rank=1;args.normal_window_plan='default'
            self.assertEqual(ns['environment'](args)['UCCL_IB_MAX_INFLIGHT_NORMAL'],'8')
            for extra in (['--mode','low_latency'],['--action','build']):
                with patch.object(sys,'argv',base+extra),patch('sys.stderr',io.StringIO()),self.assertRaises(SystemExit):ns['parser']()


if __name__=='__main__':unittest.main()
