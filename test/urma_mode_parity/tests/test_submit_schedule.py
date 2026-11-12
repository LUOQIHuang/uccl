import importlib.util
import io
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(HERE))
spec=importlib.util.spec_from_file_location('submit_driver',HERE/'driver.py')
driver=importlib.util.module_from_spec(spec);spec.loader.exec_module(driver)


class SubmitScheduleTests(unittest.TestCase):
    def parse(self, extra):
        with patch.object(sys,'argv',['driver','--job','schedule','--node-rank','0']+extra):
            return driver.parser()

    def test_guarded_args_and_clean_environment(self):
        base=['--mode','normal','--phase-suite','--api-only','--dispatch-focus','--reference-events','paced','--normal-submit-schedule','writes-first']
        a=self.parse(base)
        with patch.dict(os.environ,{'UCCL_URMA_NORMAL_SUBMIT_SCHEDULE':'lane-mixed'}):
            self.assertEqual(driver.environment(a)['UCCL_URMA_NORMAL_SUBMIT_SCHEDULE'],'writes-first')
        with patch.dict(os.environ,{'UCCL_URMA_NORMAL_SUBMIT_SCHEDULE':'writes-first'}):
            self.assertEqual(driver.environment(self.parse([]))['UCCL_URMA_NORMAL_SUBMIT_SCHEDULE'],'lane-mixed')
        for extra in [['--mode','both'],['--mode','low_latency'],['--normal-completion','paired'],['--normal-write-order','strong'],['--action','micro'],['--action','build']]:
            with patch('sys.stderr',io.StringIO()),self.assertRaises(SystemExit):self.parse(base+extra)

    def test_preflight_requires_real_schedule_capability(self):
        a=self.parse(['--mode','normal','--phase-suite','--api-only','--dispatch-focus','--reference-events','paced','--normal-submit-schedule','writes-first'])
        for value in [None,'lane-mixed','writes-first']:
            metadata=dict(phase_diagnostics=True,normal_submit_schedule=value,dispatch_trace_version=1,reference_events_version=1)
            probe=SimpleNamespace(returncode=0,stdout='MODE_PARITY_IMPORT_OK '+json.dumps(metadata),stderr='')
            with patch.object(driver.support,'read_build',return_value=(Path('module.so'),{'flags':{'UCCL_REFERENCE_EVENTS':'1'}})),patch.object(driver.urma_support,'library_manifest',return_value={}),patch.object(driver.urma_support,'hardware_check',return_value={}),patch.object(driver.subprocess,'run',return_value=probe):
                if value=='writes-first':
                    self.assertEqual(driver.preflight(a)[2]['runtime']['normal_submit_schedule'],'writes-first')
                else:
                    with self.assertRaisesRegex(RuntimeError,'NORMAL_SUBMIT_SCHEDULE_REQUIRES_REBUILD'):driver.preflight(a)


if __name__=='__main__':unittest.main()
