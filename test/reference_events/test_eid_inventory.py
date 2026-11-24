"""CPU fixture library verifies private loading and first-list-entry semantics."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import eid_inventory


class EidInventoryTests(unittest.TestCase):
    def test_preserves_list_position_instead_of_sorting_eid_index(self):
        result=eid_inventory.parse_query('URMA_QUERY_LIBRARY path=/private/liburma.so\n'
            'URMA_QUERY_DEVICE name=udmac0d1e2\n'
            'URMA_QUERY_EID device=udmac0d1e2 position=0 index=7 eid='+('ab'*16)+'\n'
            'URMA_QUERY_EID device=udmac0d1e2 position=1 index=1 eid='+('cd'*16)+'\n')
        self.assertEqual(result['eids'][0]['index'],7)
        self.assertEqual(result['library'],'/private/liburma.so')

    @unittest.skipUnless(os.name=='posix' and shutil.which('cc'),'host fixture shared library')
    def test_compiles_and_queries_private_fixture_without_device_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);include=root/'include';include.mkdir();lib=root/'lib';lib.mkdir()
            header=include/'urma_api.h'
            header.write_text('''#include <stdint.h>
typedef struct { char name[64]; } urma_device_t;
typedef struct { unsigned char raw[16]; } urma_eid_t;
typedef struct { urma_eid_t eid; uint32_t eid_index; } urma_eid_info_t;
int urma_init(void *); int urma_uninit(void);
urma_device_t **urma_get_device_list(int *);
void urma_free_device_list(urma_device_t **);
urma_eid_info_t *urma_get_eid_list(urma_device_t *,uint32_t *);
void urma_free_eid_list(urma_eid_info_t *);
''')
            stub=root/'stub.c';stub.write_text('''#include <urma_api.h>
static urma_device_t dev={"udmac0d1e2"};static urma_device_t *devs[]={&dev};
static urma_eid_info_t eids[]={{{{0xab}},7},{{{0xcd}},1}};
int urma_init(void *p){return 0;} int urma_uninit(void){return 0;}
urma_device_t **urma_get_device_list(int *n){*n=1;return devs;}
void urma_free_device_list(urma_device_t **p){}
urma_eid_info_t *urma_get_eid_list(urma_device_t *p,uint32_t *n){*n=2;return eids;}
void urma_free_eid_list(urma_eid_info_t *p){}
''')
            subprocess.run(['cc','-shared','-fPIC','-I'+str(include),str(stub),'-o',str(lib/'liburma.so')],check=True)
            manifest=root/'manifest.json'
            manifest.write_text(json.dumps(dict(sdk={'urma_api.h':hashlib.sha256(header.read_bytes()).hexdigest()})))
            args=SimpleNamespace(output=root/'out',include_dir=include,lib_dir=lib,
                build_manifest=manifest,device='udmac0d1e2',cc='cc')
            rc=eid_inventory.run(args)
            self.assertEqual(rc,0,(args.output/'eid_inventory.json').read_text()+'\n'+
                ''.join(p.read_text() for p in args.output.glob('*.log')))
            result=json.loads((args.output/'eid_inventory.json').read_text())
            self.assertTrue(result['library_matches_request'])
            self.assertEqual(result['selected_first_eid']['index'],7)
            self.assertEqual(result['selected_first_eid']['eid'],'ab'+'00'*15)
            # Refuse another SDK before compiling or invoking the library.
            header.write_text(header.read_text()+'\n')
            args.output=root/'wrong_sdk'
            with patch.object(eid_inventory.subprocess,'run',side_effect=AssertionError('must not execute')):
                self.assertEqual(eid_inventory.run(args),1)
            self.assertEqual(json.loads((args.output/'eid_inventory.json').read_text())['error'],'SDK_HEADERS_DIFFER_FROM_BENCHMARK_BUILD')


if __name__=='__main__':unittest.main()
