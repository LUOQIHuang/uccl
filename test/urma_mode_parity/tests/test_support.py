"""CPU-only provenance and result collection tests."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(HERE))
import support
spec=importlib.util.spec_from_file_location('parity_driver',HERE/'driver.py')
driver=importlib.util.module_from_spec(spec); spec.loader.exec_module(driver)

class SupportTests(unittest.TestCase):
    def test_build_provenance_and_binary_changes_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); module=root/'module.so'; module.write_bytes(b'new binary')
            data=dict(module='module.so',module_sha=support.sha(module),sources={'a.cpp':'hash'})
            (root/'manifest.json').write_text(json.dumps(data))
            with patch.object(support,'source_manifest',return_value={'a.cpp':'hash'}):
                self.assertEqual(support.read_build(root)[0],module)
                module.write_bytes(b'old binary')
                with self.assertRaisesRegex(RuntimeError,'SHA_MISMATCH'): support.read_build(root)
            module.write_bytes(b'new binary')
            with patch.object(support,'source_manifest',return_value={'a.cpp':'changed'}):
                with self.assertRaisesRegex(RuntimeError,'SOURCE_CHANGED'): support.read_build(root)

    def test_digest_is_order_independent(self):
        self.assertEqual(support.digest({'b':2,'a':1}),support.digest({'a':1,'b':2}))

    def test_production_build_rejects_cpu_fixture_sdk(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            (root/'urma_api.h').write_text('header')
            (root/'urma_types.h').write_text('CPU test placeholder; NOT vendor ABI')
            with self.assertRaisesRegex(RuntimeError,'MOCK_SDK_REJECTED'):
                driver.sdk_directory(SimpleNamespace(urma_include_dir=root))

    def test_collector_excludes_native_and_cache_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); case=root/'results/test/node0/normal'; case.mkdir(parents=True)
            (case/'bench.log').write_text('log'); (case/'status.json').write_text('{}')
            (case/'module.so').write_bytes(b'binary')
            (case/'cache').mkdir(); (case/'cache/temporary.json').write_text('{}')
            args=SimpleNamespace(output=root/'results',job='test',node_rank=0)
            with patch.object(support,'REPO',root/'repo'):
                driver.collect(args)
                target=root/'repo/test/bench_results/urma_mode_parity/test/node0/normal'
                self.assertTrue((target/'bench.log').is_file())
                self.assertFalse((target/'module.so').exists())
                self.assertFalse((target/'cache').exists())
                (case/'bench.log').write_text('different')
                with self.assertRaisesRegex(RuntimeError,'RESULT_CONFLICT'): driver.collect(args)

    def test_peer_fingerprint_uses_digests_not_full_duplicate_source_lists(self):
        # Larger trees must fit the existing 64KiB control handshake.
        sources={f'long/path/source_{i}.cpp':'f'*64 for i in range(5000)}
        payload=dict(sources_hash=support.digest(sources),build_hash=support.digest(sources))
        self.assertLess(len(json.dumps(payload)),65536)

if __name__=='__main__': unittest.main()
