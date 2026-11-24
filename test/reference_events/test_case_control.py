"""CPU-only regressions for local interface pairing and bounded failed workers."""
import ast
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
import unittest


SOURCE = Path(__file__).resolve().parents[1] / 'verbs_diag/driver.py'
tree = ast.parse(SOURCE.read_text())
tree.body = [node for node in tree.body if isinstance(node, ast.FunctionDef)
             and node.name in ('shared_fingerprint', 'case_descendants', 'signal_case_process', 'wait_for_case')]
namespace = dict(os=os, Path=Path, re=re, signal=signal, subprocess=subprocess, time=time, json=json)
exec(compile(tree, str(SOURCE), 'exec'), namespace)


class CaseControlTests(unittest.TestCase):
    def test_only_local_interface_is_excluded_from_pair_equality(self):
        shared = namespace['shared_fingerprint']
        a = dict(ifname='master_oob', shape=[4096], sources='v1', libraries={'sdk':'abc'}, fence='tail-off')
        b = dict(a, ifname='worker_oob')
        self.assertEqual(shared(a), shared(b))
        for changed in (dict(shape=[128]), dict(sources='v2'), dict(libraries={}), dict(fence='on')):
            self.assertNotEqual(shared(a), shared(dict(b, **changed)))
        self.assertEqual(a['ifname'], 'master_oob')

    @unittest.skipUnless(os.name == 'posix', 'private Linux process groups')
    def test_fatal_completion_stops_unresponsive_worker_and_keeps_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'bench.log'
            with log.open('w') as output:
                child = subprocess.Popen([sys.executable, '-c',
                    'import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); '
                    'print("[URMA_COMPLETION_ERROR] status=9",flush=True); time.sleep(60)'],
                    start_new_session=True, stdout=output, stderr=subprocess.STDOUT)
                try:
                    started = time.monotonic()
                    rc = namespace['wait_for_case'](child, log, 30)
                    self.assertEqual(rc, 1)
                    self.assertLess(time.monotonic()-started, 8)
                    self.assertIsNotNone(child.poll())
                finally:
                    if child.poll() is None:
                        os.killpg(child.pid, signal.SIGKILL); child.wait()
            failure = json.loads((log.parent / 'case_failure.json').read_text())
            self.assertEqual(failure['reason'], 'fatal_benchmark_error')
            self.assertIn('status=9', failure['first_error'])

    @unittest.skipUnless(os.name == 'posix', 'private Linux process groups')
    def test_timeout_and_success_remain_distinct(self):
        for timeout, script, expected in ((0.1, 'import time;time.sleep(60)', 124), (10, 'print("done")', 0)):
            with tempfile.TemporaryDirectory() as tmp:
                log=Path(tmp)/'bench.log'
                with log.open('w') as output:
                    child=subprocess.Popen([sys.executable,'-c',script],start_new_session=True,stdout=output)
                    try:
                        self.assertEqual(namespace['wait_for_case'](child,log,timeout),expected)
                    finally:
                        if child.poll() is None:
                            os.killpg(child.pid,signal.SIGKILL);child.wait()
                self.assertEqual((log.parent/'case_failure.json').exists(),expected!=0)

    @unittest.skipUnless(os.name == 'posix', 'torchrun workers can own another session')
    def test_fatal_cleanup_includes_worker_in_separate_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            log=Path(tmp)/'bench.log'
            worker='import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(60)'
            agent=('import subprocess,sys,time; '
                   'p=subprocess.Popen([sys.executable,"-c",'+repr(worker)+'],start_new_session=True); '
                   'print("CHILD_PID="+str(p.pid),flush=True);time.sleep(0.3); '
                   'print("[URMA_COMPLETION_ERROR] status=9",flush=True);time.sleep(60)')
            with log.open('w') as output:
                child=subprocess.Popen([sys.executable,'-c',agent],start_new_session=True,stdout=output)
                try:
                    self.assertEqual(namespace['wait_for_case'](child,log,10),1)
                finally:
                    if child.poll() is None:
                        os.killpg(child.pid,signal.SIGKILL);child.wait()
            pid=int(re.search(r'CHILD_PID=(\d+)',log.read_text()).group(1))
            failure=json.loads((log.parent/'case_failure.json').read_text())
            self.assertIn(pid,failure['owned_pids'])
            # Allow the kernel to deliver SIGKILL; orphan zombies are already dead.
            for unused in range(20):
                stat=Path('/proc/{}/stat'.format(pid))
                try:
                    if stat.read_text().rsplit(')',1)[1].split()[0]=='Z':break
                except FileNotFoundError:
                    break
                time.sleep(0.05)
            else:
                os.kill(pid,signal.SIGKILL)
                self.fail('private session worker survived failed-case cleanup')


if __name__ == '__main__':
    unittest.main()
