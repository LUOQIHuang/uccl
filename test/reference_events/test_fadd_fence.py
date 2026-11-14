import io
import json
import os
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch
from test_window_plan import driver_functions


class FaddFencePlanTests(unittest.TestCase):
    def arguments(self):
        return ['driver','--node-rank','1','--job','fence','--mode','normal','--dispatch-focus','--reference-events','paced',
                '--phase-suite','--api-only','--normal-fadd-fence-plan','peer-off']

    def test_only_node1_is_changed_and_inherited_off_is_cleared(self):
        ns=driver_functions('urma')
        with patch.object(sys,'argv',self.arguments()):args=ns['parser']()
        self.assertEqual(ns['environment'](args)['UCCL_URMA_NORMAL_FADD_FENCE'],'off')
        args.node_rank=0
        self.assertEqual(ns['environment'](args)['UCCL_URMA_NORMAL_FADD_FENCE'],'on')
        args.node_rank=1;args.normal_fadd_fence_plan='default'
        with patch.dict(os.environ,{'UCCL_URMA_NORMAL_FADD_FENCE':'off'}):
            self.assertEqual(ns['environment'](args)['UCCL_URMA_NORMAL_FADD_FENCE'],'on')
        with patch.object(sys,'argv',self.arguments()+['--reference-profile','boundaries']):
            self.assertEqual(ns['parser']().reference_profile,'boundaries')
        with patch.object(sys,'argv',self.arguments()+['--normal-fadd-fence-plan','peer-tail-off']):
            tail_args=ns['parser']()
        self.assertEqual(ns['environment'](tail_args)['UCCL_URMA_NORMAL_FADD_FENCE'],'tail-off')
        tail_args.node_rank=0
        self.assertEqual(ns['environment'](tail_args)['UCCL_URMA_NORMAL_FADD_FENCE'],'on')
        for extra in (['--mode','low_latency'],['--normal-window-plan','peer4'],['--normal-submit-schedule','writes-first'],['--normal-completion','paired'],['--action','build']):
            with patch.object(sys,'argv',self.arguments()+extra),patch('sys.stderr',io.StringIO()),self.assertRaises(SystemExit):ns['parser']()

    def test_preflight_requires_new_module_and_effective_setting(self):
        ns=driver_functions('urma')
        with patch.object(sys,'argv',self.arguments()):args=ns['parser']()
        manifest=dict(sources={},module='ep.so',module_sha='sha',flags={'UCCL_REFERENCE_EVENTS':'1','UCCL_GPU_STAGE_TRACE':'0'})
        ns['support']=NS(read_build=lambda p:(Path('ep.so'),manifest))
        ns['urma_support']=NS(library_manifest=lambda p:{},hardware_check=lambda p:{})
        for version,value in ((0,None),(1,'on'),(1,'off')):
            metadata=dict(phase_diagnostics=True,dispatch_trace_version=1,reference_events_version=1,normal_fadd_fence_version=version,normal_fadd_fence=value,normal_max_inflight=8,loaded_libraries=[])
            ns['subprocess']=NS(run=lambda *a,**kw:NS(returncode=0,stdout='MODE_PARITY_IMPORT_OK '+json.dumps(metadata),stderr=''))
            if version==1 and value=='off':ns['preflight'](args)
            else:
                with self.assertRaisesRegex(RuntimeError,'NORMAL_FADD_FENCE_RUNTIME_MISMATCH'):ns['preflight'](args)

    def test_role_capability_is_required_for_three_way_baselines_too(self):
        ns=driver_functions('urma')
        with patch.object(sys,'argv',self.arguments()+['--normal-fadd-fence-plan','all-on','--require-normal-atomic-roles']):args=ns['parser']()
        ns['support']=NS(read_build=lambda p:(Path('ep.so'),dict(sources={},module_sha='sha',flags={'UCCL_REFERENCE_EVENTS':'1','UCCL_GPU_STAGE_TRACE':'0'})))
        ns['urma_support']=NS(library_manifest=lambda p:{},hardware_check=lambda p:{})
        metadata=dict(normal_fadd_fence_version=1,normal_fadd_fence='on',phase_diagnostics=True,normal_max_inflight=8)
        ns['subprocess']=NS(run=lambda *a,**kw:NS(returncode=0,stdout='MODE_PARITY_IMPORT_OK '+json.dumps(metadata),stderr=''))
        with self.assertRaisesRegex(RuntimeError,'NORMAL_ATOMIC_ROLE_REBUILD_REQUIRED'):ns['preflight'](args)


if __name__=='__main__':unittest.main()
