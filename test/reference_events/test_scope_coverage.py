"""Exercise the real shell schedule with fake workers; never start a benchmark."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


@unittest.skipUnless(os.name == 'posix' and shutil.which('bash'), 'Linux shell orchestration')
class ScopeCoverageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        here=self.root/'test/reference_events';here.mkdir(parents=True)
        (here/'run_boundaries.sh').write_text(Path(__file__).with_name('run_boundaries.sh').read_text())
        (here/'run.sh').write_text('''#!/usr/bin/env bash
exec "${URMA_DIAG_PYTHON}" "$(dirname "$0")/fake_worker.py" "$@"
''')
        (here/'fake_worker.py').write_text('''import json,os,sys
from pathlib import Path
a=dict(zip(sys.argv[1::2],sys.argv[2::2]))
with open(os.environ['SCOPE_CALLS'],'a') as f:f.write(json.dumps(a)+'\\n')
if os.environ.get('SCOPE_FAIL') and a['--job'].endswith(os.environ['SCOPE_FAIL']):sys.exit(7)
case=Path(a['--output'])/a['--job']/('node'+a['--node-rank'])
case/= 'normal' if a['--backend']=='urma' else 'r01_baseline/normal'
case.mkdir(parents=True,exist_ok=True)
(case/'status.json').write_text(json.dumps(dict(rc=0,checks=dict(correct=True))))
''')
        collect=self.root/'test/gpu_stage_recovery';collect.mkdir()
        (collect/'collect.py').write_text('''import json,os,sys
with open(os.environ['SCOPE_COLLECTS'],'a') as f:f.write(json.dumps(sys.argv[1:])+'\\n')
''')
        self.env=dict(os.environ,URMA_DIAG_PYTHON=sys.executable,VERBS_DIAG_PYTHON=sys.executable,
            SCOPE_CALLS=str(self.root/'calls'),SCOPE_COLLECTS=str(self.root/'collects'))

    def run_part(self, part, backend='urma', fail='', extra=()):
        env=dict(self.env,SCOPE_FAIL=fail)
        return subprocess.run(['bash',str(self.root/'test/reference_events/run_boundaries.sh'),
            '--part',part,'--backend',backend,'--node-rank','1','--job','scope',
            '--build-dir',str(self.root/'build'),'--output',str(self.root/'results'),
            '--destination',str(self.root/'dest'),'--normal-fadd-fence-plan',
            'peer-tail-off' if backend=='urma' else 'default']+list(extra),env=env,
            stdout=subprocess.PIPE,stderr=subprocess.STDOUT,universal_newlines=True)

    def calls(self):
        p=self.root/'calls'
        return [json.loads(s) for s in p.read_text().splitlines()] if p.exists() else []

    def test_coverage_requires_smoke_then_five_fixed_cases(self):
        self.assertNotEqual(self.run_part('coverage').returncode,0)
        self.assertEqual(self.calls(),[])
        for part in ('smoke','coverage'):
            r=self.run_part(part);self.assertEqual(r.returncode,0,r.stdout)
        calls=self.calls()
        self.assertEqual([a['--step'] for a in calls],['trace','batch','paced','trace','paced','batch'])
        self.assertEqual([a['--job'] for a in calls],['scope_'+s for s in
            ('smoke128','batch_pre','paced','trace','post','batch_post')])
        self.assertTrue(all(a['--normal-fadd-fence-plan']=='peer-tail-off' for a in calls))
        self.assertEqual(len((self.root/'collects').read_text().splitlines()),6)

    def test_verbs_keeps_original_semantics(self):
        for part in ('smoke','coverage'):
            r=self.run_part(part,'verbs');self.assertEqual(r.returncode,0,r.stdout)
        self.assertTrue(all(a['--normal-fadd-fence-plan']=='default' for a in self.calls()))

    def test_failure_collects_before_stopping(self):
        self.assertEqual(self.run_part('smoke').returncode,0)
        r=self.run_part('coverage',fail='_trace');self.assertEqual(r.returncode,7,r.stdout)
        self.assertEqual(self.calls()[-1]['--job'],'scope_trace')
        self.assertIn('scope_trace',(self.root/'collects').read_text().splitlines()[-1])

    def test_original_formal_still_has_three_cases(self):
        for part in ('smoke','formal'):
            r=self.run_part(part);self.assertEqual(r.returncode,0,r.stdout)
        self.assertEqual([a['--job'] for a in self.calls()],
            ['scope_'+s for s in ('smoke128','paced','trace','post')])

    def test_explicit_local_topology_and_timeout_are_forwarded(self):
        extra=['--ifname','worker_oob','--gpu-pci','0000:21:00.0','--atomic-port','auto','--case-timeout','300']
        r=self.run_part('smoke',extra=extra);self.assertEqual(r.returncode,0,r.stdout)
        for key,value in zip(extra[::2],extra[1::2]):
            self.assertEqual(self.calls()[0][key],value)


if __name__ == '__main__':
    unittest.main()
