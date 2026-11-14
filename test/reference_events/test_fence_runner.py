"""Exercise the real shell sequence without importing torch or starting GPUs."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


@unittest.skipUnless(os.name == 'posix' and shutil.which('bash'), 'Linux shell orchestration')
class FenceRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for name in ('reference_events', 'urma_mode_parity', 'gpu_stage_recovery', 'build'):
            (self.root/name).mkdir()
        source = Path(__file__).with_name('run_fadd_fence.sh')
        (self.root/'reference_events/run_fadd_fence.sh').write_text(source.read_text())
        (self.root/'urma_mode_parity/run.sh').write_text(
            '#!/usr/bin/env bash\nexec "$URMA_DIAG_PYTHON" "$(dirname "$0")/fake.py" "$@"\n')
        (self.root/'build/manifest.json').write_text('{"module_sha":"same"}')
        (self.root/'urma_mode_parity/fake.py').write_text('''
import json,os,sys
from pathlib import Path
a={};it=iter(sys.argv[1:]);flags={'--phase-suite','--api-only','--dispatch-focus','--require-normal-atomic-roles'}
for key in it:a[key]=True if key in flags else next(it)
root=Path(a['--output']);root.mkdir(parents=True,exist_ok=True)
with (root/'calls.jsonl').open('a') as f:f.write(json.dumps(a)+'\\n')
if a['--action']=='check':sys.exit(0)
rank=int(a['--node-rank']);node=root/a['--job']/('node%d'%rank);case=node/'normal';case.mkdir(parents=True)
failed=os.environ.get('FAIL_SUFFIX') and a['--job'].endswith(os.environ['FAIL_SUFFIX'])
def save(path,value):path.write_text(json.dumps(value))
save(case/'status.json',dict(rc=7 if failed else 0,checks=dict(correctness=not failed,normal_atomic_roles=True)))
save(case/'runtime.json',dict(module_sha='same',normal_atomic_role_version=1,normal_fadd_fence_version=1,normal_fadd_fence=('tail-off' if a['--normal-fadd-fence-plan']=='peer-tail-off' else 'off') if rank and a['--normal-fadd-fence-plan'] in ('peer-off','peer-tail-off') else 'on',reference_boundary_version=1,reference_profile=a['--reference-profile']))
save(node/'setup.json',dict(error=None,fingerprint=dict(normal_fadd_fence_plan=a['--normal-fadd-fence-plan'],shape=[int(a['--normal-tokens']),128,7168,256,8],reference_profile=a['--reference-profile'],reference_events=a['--reference-events'])))
save(node/'host.json',dict(boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip()))
sys.exit(7 if failed else 0)
''')
        (self.root/'gpu_stage_recovery/collect.py').write_text('''
import json,sys
from pathlib import Path
root=Path(sys.argv[1]).parent
with (root/'collected.jsonl').open('a') as f:f.write(json.dumps(sys.argv[1:])+'\\n')
''')

    def run_part(self, part, rank=1, fail=None):
        import sys
        env=dict(os.environ,URMA_DIAG_PYTHON=sys.executable)
        if fail:env['FAIL_SUFFIX']=fail
        command=['bash',str(self.root/'reference_events/run_fadd_fence.sh'),
                 '--node-rank',str(rank),'--build-dir',str(self.root/'build'),
                 '--job','ab','--part',part,'--output',str(self.root/'out'),
                 '--destination',str(self.root/'dest')]
        return subprocess.run(command,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,universal_newlines=True)

    def calls(self):
        return [json.loads(line) for line in (self.root/'out/calls.jsonl').read_text().splitlines()]

    def test_both_nodes_execute_smoke_and_exact_abba(self):
        for rank in (0,1):
            for part in ('smoke','performance'):
                result=self.run_part(part,rank)
                self.assertEqual(result.returncode,0,result.stdout)
            runs=[a for a in self.calls() if a['--node-rank']==str(rank) and a['--action']=='run']
            self.assertEqual([a['--job'] for a in runs],['ab_smoke128','ab_r01_all_on','ab_r01_peer_off','ab_r02_peer_off','ab_r02_all_on'])
            self.assertEqual([a['--normal-fadd-fence-plan'] for a in runs],['peer-off','all-on','peer-off','peer-off','all-on'])
            self.assertEqual([(a['--normal-tokens'],a['--warmups'],a['--samples']) for a in runs],[('128','1','3')]+[('4096','5','10')]*4)
        before=len(self.calls())
        self.assertNotEqual(self.run_part('performance').returncode,0)
        self.assertEqual(len(self.calls()),before)  # No overwrite.

    def test_module_or_boot_change_refuses_formal_run(self):
        result=self.run_part('smoke');self.assertEqual(result.returncode,0,result.stdout)
        node=self.root/'out/ab_smoke128/node1'
        for path,key,value in ((node/'normal/runtime.json','module_sha','changed'),(node/'host.json','boot_id','changed')):
            original=path.read_text();data=json.loads(original);data[key]=value;path.write_text(json.dumps(data))
            result=self.run_part('performance');self.assertNotEqual(result.returncode,0,result.stdout)
            self.assertEqual(len(self.calls()),2)
            path.write_text(original)

    def test_observation_requires_its_own_active_boundary_smoke_and_six_cases(self):
        result=self.run_part('smoke');self.assertEqual(result.returncode,0,result.stdout)
        self.assertNotEqual(self.run_part('observe').returncode,0)
        result=self.run_part('observe-smoke');self.assertEqual(result.returncode,0,result.stdout)
        result=self.run_part('observe');self.assertEqual(result.returncode,0,result.stdout)
        runs=[a for a in self.calls() if a['--action']=='run' and a['--reference-profile']=='boundaries']
        self.assertEqual([a['--job'] for a in runs],['ab_observe_smoke128']+['ab_'+plan+'_'+step for plan in ('all_on','peer_off') for step in ('paced','trace','post')])
        self.assertEqual([a['--reference-events'] for a in runs],['on','paced','on','paced','paced','on','paced'])
        self.assertEqual([a['--normal-fadd-fence-plan'] for a in runs],['peer-off']+['all-on']*3+['peer-off']*3)

    def test_failed_b_is_collected_and_stops_before_second_pair(self):
        result=self.run_part('smoke');self.assertEqual(result.returncode,0,result.stdout)
        result=self.run_part('performance',fail='r01_peer_off')
        self.assertEqual(result.returncode,7,result.stdout)
        runs=[a['--job'] for a in self.calls() if a['--action']=='run']
        self.assertEqual(runs,['ab_smoke128','ab_r01_all_on','ab_r01_peer_off'])
        collected=(self.root/'out/collected.jsonl').read_text().splitlines()
        self.assertEqual(len(collected),3)
        self.assertIn('ab_r01_peer_off',collected[-1])

    def test_split_observation_does_not_start_b_until_a_passes(self):
        result=self.run_part('observe-smoke');self.assertEqual(result.returncode,0,result.stdout)
        self.assertNotEqual(self.run_part('observe-b').returncode,0)
        result=self.run_part('observe-a');self.assertEqual(result.returncode,0,result.stdout)
        runs=[a['--job'] for a in self.calls() if a['--action']=='run']
        self.assertEqual(runs,['ab_observe_smoke128','ab_all_on_paced','ab_all_on_trace','ab_all_on_post'])
        result=self.run_part('observe-b');self.assertEqual(result.returncode,0,result.stdout)
        runs=[a['--job'] for a in self.calls() if a['--action']=='run']
        self.assertEqual(runs[-3:],['ab_peer_off_paced','ab_peer_off_trace','ab_peer_off_post'])


if __name__=='__main__':unittest.main()
