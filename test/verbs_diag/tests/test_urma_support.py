"""CPU-only checks; never imports torch or touches real GPU/PCI devices."""
import ast
import importlib.util
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

DIAG = Path(__file__).resolve().parents[1]
REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(DIAG))
import urma_support as support

class SupportTests(unittest.TestCase):
    def test_cpus(self):
        self.assertEqual(support.parse_cpus('4,5,6,7'), [4,5,6,7])
        for value in ('4,4', '-1,2', 'bad', ''):
            with self.assertRaises(ValueError):
                support.parse_cpus(value)

    def test_utils_changes_only_start_call(self):
        source = (REPO/'ep/bench/utils.py').read_text(encoding='utf-8')
        expected = ast.parse(source)
        changed = 0
        for node in ast.walk(expected):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id=='proxy' and node.func.attr=='start_dual':
                node.func = ast.Name(id='_diag_start_proxy', ctx=ast.Load())
                node.args = [ast.Name(id='proxy', ctx=ast.Load())]
                changed += 1
        self.assertEqual(changed, 1)
        self.assertEqual(ast.dump(expected), ast.dump(ast.parse(support.prepare_utils(source))))
        with self.assertRaises(ValueError):
            support.prepare_utils('unexpected source')

    def test_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with self.assertRaises(ValueError):
                support.library_manifest(directory)
            (directory/'liburma.so').write_bytes(b'library')
            (directory/'liburma.so.0').write_bytes(b'library')
            result = support.library_manifest(directory)
            self.assertEqual(result['liburma.so'], result['liburma.so.0'])

    def test_proxy_inheritance_and_restore(self):
        with tempfile.TemporaryDirectory() as temporary:
            control = support.ProxyAffinity([4], True, Path(temporary)/'starts.jsonl')
            state = dict(mask={0,1,2,3,4}, tids={100})
            class Tasks:
                def iterdir(self):
                    return [SimpleNamespace(name=str(tid)) for tid in state['tids']]
            class Proxy:
                def start_dual(self):
                    self.inherited = set(state['mask'])
                    state['tids'].add(101)
            proxy = Proxy()
            def set_mask(tid, mask):
                state['mask'] = set(mask)
            with patch.object(support, 'Path', return_value=Tasks()), patch.object(support.os, 'sched_getaffinity', side_effect=lambda tid:set(state['mask']), create=True), patch.object(support.os, 'sched_setaffinity', side_effect=set_mask, create=True):
                control.start(proxy)
            self.assertEqual(proxy.inherited, {4})
            self.assertEqual(state['mask'], {0,1,2,3,4})
            control.verify([dict(tid=101,allowed='4')])
            with self.assertRaises(RuntimeError):
                control.verify([dict(tid=101,allowed='0-4')])
            with self.assertRaises(RuntimeError):
                control.verify([])

    def test_restore_on_start_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            control = support.ProxyAffinity([4], True, Path(temporary)/'starts.jsonl')
            state = dict(mask={0,1,2,3,4})
            class Tasks:
                def iterdir(self):
                    return [SimpleNamespace(name='100')]
            def set_mask(tid,mask):
                state['mask'] = set(mask)
            proxy = SimpleNamespace(start_dual=lambda: (_ for _ in ()).throw(RuntimeError('start failed')))
            with patch.object(support, 'Path', return_value=Tasks()), patch.object(support.os, 'sched_getaffinity', side_effect=lambda tid:set(state['mask']), create=True), patch.object(support.os, 'sched_setaffinity', side_effect=set_mask, create=True):
                with self.assertRaises(RuntimeError):
                    control.start(proxy)
            self.assertEqual(state['mask'], {0,1,2,3,4})

    def test_hardware_readonly_and_atomic_gate(self):
        args = SimpleNamespace(expected_kernel='test-kernel', urma_device='udmac0d1e2', gpu_pci='0000:a1:00.0', atomic_port='0000:a0:08.0')
        class HardwarePath:
            def __init__(self,path): self.path=str(path)
            def __truediv__(self,name): return HardwarePath(self.path+'/'+str(name))
            def exists(self): return True
            def is_dir(self): return True
            def is_file(self): return False
            def resolve(self): return self
            def __str__(self): return self.path
            def read_text(self):
                if self.path.endswith('runpm_link'): return '0\n'
                if self.path.endswith('runtime_status'): return 'active\n'
                raise AssertionError(self.path)
        calls=[]
        def command(argv, **kwargs):
            calls.append(argv)
            if argv[0]=='setpci': return SimpleNamespace(returncode=0,stdout='0040\n')
            if argv[0]=='journalctl': return SimpleNamespace(returncode=0,stdout='')
            raise AssertionError(argv)
        with patch.object(support.os,'uname',return_value=SimpleNamespace(release='test-kernel'),create=True), patch.object(support,'Path',side_effect=HardwarePath), patch.object(support.subprocess,'run',side_effect=command):
            self.assertEqual(support.hardware_check(args)['atomic_control'],'0040')
        self.assertEqual(calls[0],['setpci','-s','0000:a0:08.0','CAP_EXP+28.w'])
        self.assertTrue(all('=' not in arg for arg in calls[0]))
        with patch.object(support.os,'uname',return_value=SimpleNamespace(release='test-kernel'),create=True), patch.object(support,'Path',side_effect=HardwarePath), patch.object(support.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='0000\n')):
            with self.assertRaisesRegex(RuntimeError,'ATOMIC_REQUEST_NOT_ENABLED'):
                support.hardware_check(args)

if __name__=='__main__':
    unittest.main()
