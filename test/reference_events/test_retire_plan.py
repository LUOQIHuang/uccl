import io,os,sys,unittest
import json
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
from test_window_plan import driver_functions


class RetirePlanTests(unittest.TestCase):
    def test_old_or_ignored_policy_is_rejected_even_for_baseline(self):
        ns=driver_functions('urma')
        argv=['driver','--node-rank','1','--job','retire','--mode','normal','--phase-suite','--api-only','--dispatch-focus','--reference-events','paced','--require-normal-retire']
        with patch.object(sys,'argv',argv):args=ns['parser']()
        ns['support']=NS(read_build=lambda p:(Path('ep.so'),dict(sources={},module_sha='sha',flags={'UCCL_REFERENCE_EVENTS':'1','UCCL_GPU_STAGE_TRACE':'0'})))
        ns['urma_support']=NS(library_manifest=lambda p:{},hardware_check=lambda p:{})
        for version,policy in ((0,None),(1,'ready')):
            metadata=dict(normal_fifo_retire_version=version,normal_fifo_retire=policy,queue_backend='fifo',phase_diagnostics=True)
            ns['subprocess']=NS(run=lambda *a,**kw:NS(returncode=0,stdout='MODE_PARITY_IMPORT_OK '+json.dumps(metadata),stderr=''))
            with self.assertRaisesRegex(RuntimeError,'NORMAL_FIFO_RETIRE_REBUILD_OR_RUNTIME_MISMATCH'):ns['preflight'](args)
    def test_node_selection_environment_and_incompatible_combinations(self):
        ns=driver_functions('urma')
        argv=['driver','--node-rank','1','--job','retire','--mode','normal','--phase-suite','--api-only','--dispatch-focus','--reference-events','paced','--normal-retire-plan','peer-ready']
        with patch.object(sys,'argv',argv):args=ns['parser']()
        self.assertEqual(ns['environment'](args)['UCCL_URMA_NORMAL_RETIRE'],'ready')
        args.node_rank=0;self.assertEqual(ns['environment'](args)['UCCL_URMA_NORMAL_RETIRE'],'ordered')
        args.node_rank=1;args.normal_retire_plan='ordered'
        with patch.dict(os.environ,{'UCCL_URMA_NORMAL_RETIRE':'ready'}):self.assertEqual(ns['environment'](args)['UCCL_URMA_NORMAL_RETIRE'],'ordered')
        for extra in (['--normal-fadd-fence-plan','peer-off'],['--normal-window-plan','peer4'],['--normal-submit-schedule','writes-first'],['--reference-events','on'],['--reference-profile','boundaries'],['--mode','low_latency']):
            with patch.object(sys,'argv',argv+extra),patch('sys.stderr',io.StringIO()),self.assertRaises(SystemExit):ns['parser']()
