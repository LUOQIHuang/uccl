"""Classify real uploaded failures and keep old boot errors out of run deltas."""
from contextlib import redirect_stdout
import io
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from connection_diag import command, diagnose, log_delta, read

HERE = Path(__file__).resolve().parent


class ConnectionTests(unittest.TestCase):
    def test_latest_permission_failure_uses_overlapping_journal_and_correlates_peer_address(self):
        source=HERE/'results/normal_scope_20261124_recovery/normal_scope_urma_02_connect'
        for rank, new_lines in ((0,139),(1,48)):
            with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()):
                root=Path(tmp)
                result=diagnose(root,source/('node'+str(rank)),
                    source/'console'/('node'+str(rank))/'connection', source/('node'+str(1-rank)))
                self.assertEqual(result['dmesg_scope'],'unavailable_see_kernel_log')
                self.assertEqual(result['kernel_log']['source'],'benchmark_journal_tail')
                self.assertEqual(result['kernel_log']['scope'],'overlapping_tail_added_lines')
                self.assertEqual(len((root/'kernel_delta.txt').read_text(encoding='utf-8').splitlines()),new_lines)
                self.assertEqual((root/'dmesg_delta.txt').read_text(),'')
                self.assertIn('dmesg_before',result['kernel_log']['collection_errors'])
                if rank==1:
                    self.assertEqual(len(result['ummu_events']),1)
                    event=result['ummu_events'][0]
                    self.assertEqual(event['code'],'0x13')
                    self.assertEqual(len(event['matches']),1)
                    match=event['matches'][0]
                    self.assertEqual(match['owner'],'peer')
                    self.assertEqual(match['registration']['node'],'node0')
                    self.assertEqual(match['registration']['size'],64)
                    self.assertEqual(match['registration']['kind'],'host')
                    self.assertEqual(int(match['word'],16),int('fffb90bc7000',16))

    def test_failed_kernel_reads_are_unavailable_and_changed_boot_is_not_a_delta(self):
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()):
            root=Path(tmp)
            for phase in ('before','after'):
                (root/('connection_'+phase+'.json')).write_text(json.dumps(dict(
                    boot_id=dict(text='boot1'),commands=[dict(command=['dmesg'],rc=1,
                    stdout='',stderr='Operation not permitted')])))
            result=diagnose(root,root)
            self.assertEqual(result['kernel_log']['scope'],'kernel_log_unavailable')
            for phase,boot in (('before','boot1'),('after','boot2')):
                (root/('connection_'+phase+'.json')).write_text(json.dumps(dict(
                    boot_id=dict(text=boot),commands=[dict(command=['dmesg'],rc=0,
                    stdout='old\nnew\n' if phase=='after' else 'old\n')])))
            self.assertEqual(diagnose(root,root)['kernel_log']['scope'],'different_boot_no_run_delta')

    def test_tail_overlap_keeps_duplicates_and_unknown_history_unscoped(self):
        self.assertEqual(log_delta('a\nb\nb\n','b\nb\nc\n'),('c\n','overlapping_tail_added_lines'))
        self.assertEqual(log_delta('a\n','z\n'),('z\n','full_snapshot_no_exact_delta'))

    def test_uploaded_scope_smoke_fails_in_internal_ack_not_fp8(self):
        source = HERE / 'results/normal_scope_20261123/normal_scope_urma_01_smoke128'
        sdk = HERE.parents[1] / 'test/fadd_path/results/urma_cqe_visibility_02/node1/sdk/urma_opcode.h'
        manifest = json.loads((source/'node1/build_manifest.json').read_text())
        raw = sdk.read_bytes().replace(b'\r\n', b'\n')
        self.assertEqual(hashlib.sha256(raw).hexdigest(), manifest['sdk']['urma_opcode.h'])
        self.assertIn(b'URMA_CR_ACK_TIMEOUT_ERR', raw)
        for rank, classification in ((1, 'URMA_ACK_TIMEOUT'), (0, 'DISPATCH_CPU_TIMEOUT_CHECK_PEER')):
            with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()):
                root = Path(tmp)
                result = diagnose(root, source / ('node' + str(rank)))
                self.assertEqual(result['classification'], classification)
                if rank == 1:
                    self.assertEqual(result['completion']['status'], '9')
                    self.assertTrue(result['internal_control_atomic'])
                    self.assertTrue(result['initial_barrier_candidate'])
                    self.assertEqual(result['completion']['wr_id'], str(2**64-1))

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
