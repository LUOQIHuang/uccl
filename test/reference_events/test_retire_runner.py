import os,shutil,unittest
from pathlib import Path
import test_fence_runner as fixtures


@unittest.skipUnless(os.name=='posix' and shutil.which('bash'),'Linux shell orchestration')
class RetireRunnerTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.FenceRunnerTests();self.f.setUp();self.addCleanup(self.f.temporary.cleanup)
        (self.f.root/'reference_events/run_fadd_fence.sh').write_text(Path(__file__).with_name('run_retire_ab.sh').read_text())

    def test_fixed_abba_after_current_module_boot_smoke(self):
        self.assertNotEqual(self.f.run_part('performance').returncode,0)
        for part in ('smoke','performance'):
            result=self.f.run_part(part);self.assertEqual(result.returncode,0,result.stdout)
        runs=[a for a in self.f.calls() if a['--action']=='run']
        self.assertEqual([a['--normal-retire-plan'] for a in runs],['peer-ready','ordered','peer-ready','peer-ready','ordered'])
        self.assertTrue(all(a['--normal-fadd-fence-plan']=='all-on' and a['--reference-events']=='paced' for a in runs))

    def test_failure_collects_and_stops(self):
        self.assertEqual(self.f.run_part('smoke').returncode,0)
        result=self.f.run_part('performance',fail='r01_ready');self.assertEqual(result.returncode,7,result.stdout)
        self.assertEqual(len([a for a in self.f.calls() if a['--action']=='run']),3)
