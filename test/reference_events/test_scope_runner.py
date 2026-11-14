import os
from pathlib import Path
import shutil
import unittest
import test_fence_runner as fixture_module


@unittest.skipUnless(os.name=='posix' and shutil.which('bash'),'Linux shell orchestration')
class ScopeRunnerTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixture_module.FenceRunnerTests();self.fixture.setUp()
        self.addCleanup(self.fixture.temporary.cleanup)
        (self.fixture.root/'reference_events/run_fadd_fence.sh').write_text(Path(__file__).with_name('run_fence_scope.sh').read_text())

    def test_two_smokes_then_exact_abc_cba_without_active_capture(self):
        f=self.fixture
        self.assertNotEqual(f.run_part('performance').returncode,0)
        for part in ('smoke','performance'):
            result=f.run_part(part);self.assertEqual(result.returncode,0,result.stdout)
        runs=[a for a in f.calls() if a['--action']=='run']
        self.assertEqual([a['--normal-fadd-fence-plan'] for a in runs],['peer-off','peer-tail-off','all-on','peer-off','peer-tail-off','peer-tail-off','peer-off','all-on'])
        self.assertTrue(all(a['--reference-events']=='paced' and a['--reference-profile']=='legacy' for a in runs))

    def test_failed_c_stops_and_collects_before_reverse_sequence(self):
        f=self.fixture;self.assertEqual(f.run_part('smoke').returncode,0)
        result=f.run_part('performance',fail='r01_peer_tail_off');self.assertEqual(result.returncode,7,result.stdout)
        runs=[a for a in f.calls() if a['--action']=='run'];self.assertEqual(len(runs),5)
        self.assertIn('r01_peer_tail_off',(f.root/'out/collected.jsonl').read_text())


if __name__=='__main__':unittest.main()
