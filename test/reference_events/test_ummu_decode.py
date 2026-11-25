"""Decode the real fault against supplied definitions; preserve provenance limits."""
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from collect_kernel_sources import module_source_metadata
from decode_ummu_event import decode
from endpoint_state import compare_versions
import endpoint_state

HERE=Path(__file__).resolve().parent


class UmmuTests(unittest.TestCase):
    def test_endpoint_query_reads_identity_directly_and_only_issues_queries(self):
        paths=[];commands=[]
        original_readlink=endpoint_state.os.readlink
        def readlink(path,*args,**kwargs):
            if str(path).startswith('/proc/self/ns/'):return 'ns:[test]'
            return original_readlink(path,*args,**kwargs)
        def read(path):
            paths.append(str(path));return dict(text='ABC\n')
        def command(argv):
            commands.append(argv);return dict(command=argv,rc=0,stdout='srcversion: ABC\n')
        with patch.object(endpoint_state,'read',side_effect=read), \
             patch.object(endpoint_state,'command',side_effect=command), \
             patch.object(endpoint_state.os,'uname',return_value=SimpleNamespace(release='test'),create=True), \
             patch.object(endpoint_state.os,'readlink',side_effect=readlink), \
             patch.object(endpoint_state.glob,'glob',return_value=[]), \
             patch.object(endpoint_state,'module_source_metadata',return_value={'ummu':{'srcversion':'ABC'}}):
            result=endpoint_state.snapshot('udmac0d1e2',Path('/source'))
        self.assertIn('guid',result['device_attrs'])
        self.assertTrue(any(Path(p).as_posix().endswith('/udmac0d1e2/guid') for p in paths))
        self.assertTrue(result['modules']['ummu']['version_comparison']['built_equals_loaded'])
        self.assertTrue(all(c[0]=='modinfo' or c[:2]==['urma_admin','main_ue_eid'] for c in commands))

    def test_real_event_is_permission_fault_on_peer_barrier(self):
        source=HERE/'results/urma_kernel_sources_01/node0/sources/kernel'
        path=HERE/'results/normal_scope_20261124_recovery/normal_scope_urma_02_analysis/node1/connection_diagnosis.json'
        event=json.loads(path.read_text())['ummu_events'][0]
        result=decode(event['raw_words'],source)
        self.assertEqual(result['event_name'],'EVT_P_PERMISSION')
        self.assertEqual(result['code'],19)
        self.assertEqual(result['tid'],189)
        self.assertEqual(result['iova'],'0xfffb90bc7000')
        self.assertTrue(result['privileged'])
        self.assertFalse(result['read'])
        self.assertFalse(result['stall'])
        self.assertEqual(result['tect_tag'],0)
        with self.assertRaisesRegex(ValueError,'eight'):decode(event['raw_words'][:-1],source)

    def test_generated_versions_survive_git_ignore_as_json_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path=root/'drivers/iommu/hisilicon/ummu.mod.c'
            path.parent.mkdir(parents=True);path.write_text('MODULE_INFO(srcversion, "AbC123");\n')
            result=module_source_metadata(root)
            self.assertEqual(result['ummu']['srcversion'],'ABC123')
            self.assertIn('error',result['udma'])
            self.assertEqual(compare_versions('ABC','abc','DEF')['built_equals_loaded'],False)
            self.assertIsNone(compare_versions('ABC','ABC',None)['built_equals_loaded'])
            self.assertTrue(compare_versions('ABC','abc','ABC')['loaded_equals_installed'])


if __name__=='__main__':unittest.main()
