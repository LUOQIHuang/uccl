"""Hardware-free regression coverage for failure reporting and process cleanup."""
import json
from pathlib import Path
import socket
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import run


class HarnessTests(unittest.TestCase):
    def test_validation_does_not_trust_all_ok(self):
        good = ('ALL_OK\nWRITE-verify       : PASS\n'
                'FADD-oldval seq    : PASS\nFADD-oldval pipe   : PASS\n')
        self.assertEqual(run.verdict(good, '', [0, 0]), [])
        self.assertTrue(run.verdict(good + 'FADD-oldval pipe : FAIL\n', '', [0]))
        self.assertTrue(run.verdict(good, 'CE status=4 (NON-SUCCESS)', [0, 0]))
        self.assertTrue(run.verdict(good, '', [124]))
        self.assertTrue(run.verdict('ALL_OK', '', [0]))

    def test_reject_unsafe_ranges(self):
        for args in (['--windows', '33'], ['--sizes', '32769'], ['--iters', '0'],
                     ['--suite', 'ep', '--nnodes', '1']):
            with self.subTest(args=args), self.assertRaises(SystemExit):
                run.parse(args)

    def test_missing_tool_timeout_and_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = run.Runner(run.parse(['--output', str(Path(tmp) / 'report')]))
            missing = r.command('missing', ['/does-not-exist/cc'])
            self.assertEqual(missing['exit_code'], 127)
            timed = r.command('timeout', [sys.executable, '-c', 'import time; time.sleep(60)'], timeout=.1)
            self.assertEqual(timed['exit_code'], 124)
            self.assertTrue(all(p.poll() is not None for p in r.children))
            self.assertEqual(r.finish(), 1)
            with tarfile.open(str(r.out) + '.tar.gz') as tar:
                self.assertTrue(any(x.endswith('REPORT.md') for x in tar.getnames()))
            self.assertEqual(len(json.loads((r.out / 'results.json').read_text())), 2)

    def test_local_pair_and_metric_capture(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Use a real TCP pair to verify readiness, both logs and collection.
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            port = min(max(port - 1, 1024), 65000)
            r = run.Runner(run.parse(['--output', str(Path(tmp) / 'pair'), '--backends', 'verbs',
                                     '--sizes', '8', '--windows', '1', '--repeats', '1', '--port', str(port)]))
            fake = r.out / 'verbs'
            fake.write_text('''#!/usr/bin/env python3
import os, socket, sys
s = socket.socket()
if '-s' in sys.argv:
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(('127.0.0.1', int(os.environ['BASELINE_PORT'])))
    s.listen(1)
    print('BASELINE_LISTEN', flush=True)
    c, _ = s.accept()
    c.recv(1)
    c.close()
else:
    s.connect((os.environ['BASELINE_PEER'], int(os.environ['BASELINE_PORT'])))
    s.sendall(b'x')
    print('WRITE-latency 8 : p50=1.0 p99=2.0 us')
    print('WRITE-verify       : PASS')
    print('FADD-oldval seq    : PASS')
    print('FADD-oldval pipe   : PASS')
    print('ALL_OK')
s.close()
''')
            fake.chmod(0o755)
            with patch.object(r, 'build_micro', return_value=True):
                r.micro()
            self.assertEqual(r.finish(), 0)
            self.assertEqual(len(r.metrics), 1)
            self.assertTrue(r.metrics[0]['valid'])


if __name__ == '__main__':
    unittest.main()
