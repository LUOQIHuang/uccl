"""Classify real uploaded failures and keep old boot errors out of run deltas."""
from contextlib import redirect_stdout
import io
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from connection_diag import command, diagnose, read

HERE = Path(__file__).resolve().parent


class ConnectionTests(unittest.TestCase):
    def test_real_uploaded_bind_failure_both_nodes(self):
        for rank, host in ((0, 'master'), (1, 'worker1')):
            source = HERE / 'results/urma_ref_smoke128_01' / ('node' + str(rank))
            with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()):
                root = Path(tmp)
                (root / 'normal').mkdir()
                shutil.copyfile(source / 'normal/bench.log', root / 'normal/bench.log')
                shutil.copyfile(source / ('dmesg_' + host + '.txt'), root / 'dmesg_after.txt')
                result = diagnose(root, root)
                self.assertEqual(result['classification'], 'URMA_BIND_FAILED')
                self.assertTrue(result['evidence']['tp_exchange'])
                self.assertEqual(result['dmesg_scope'], 'full_snapshot_no_exact_delta')

    def test_exact_delta_excludes_old_activation_failure(self):
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()):
            root = Path(tmp)
            old = '[10] failed to activate ubase dev\n'
            (root / 'dmesg_before.txt').write_text(old)
            (root / 'dmesg_after.txt').write_text(old + '[20] unrelated event\n')
            result = diagnose(root, root)
            self.assertFalse(result['evidence']['device_activation'])
            self.assertEqual(result['dmesg_scope'], 'appended_during_run')
            self.assertEqual(result['classification'], 'INSPECT_RUN_STATUS')

    def test_missing_commands_and_read_limits_are_recorded(self):
        with patch('subprocess.run', side_effect=FileNotFoundError('missing')):
            self.assertIn('error', command(['absent_tool']))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'large'
            path.write_bytes(b'x' * 100)
            self.assertEqual(read(path, 12), dict(text='x' * 12, truncated=True))


if __name__ == '__main__':
    unittest.main()
