import json
from pathlib import Path
import shutil
import struct
import tempfile
import unittest
from unittest.mock import patch

from collect_codegen import collect, digest, elf, images


def fixture_elf(machine=183, payload=b'kernel metadata', bits=64):
    names = b'\0.shstrtab\0.note.test\0'
    header_fmt = '<HHIQQQIHHHHHH' if bits == 64 else '<HHIIIIIHHHHHH'
    section_fmt = '<IIQQQQIIQQ' if bits == 64 else '<IIIIIIIIII'
    hsize = 16 + struct.calcsize(header_fmt)
    ssize = struct.calcsize(section_fmt)
    name_offset = hsize
    payload_offset = name_offset + len(names)
    shoff = payload_offset + len(payload)
    ident = b'\x7fELF' + bytes([2 if bits == 64 else 1, 1, 1]) + bytes(9)
    header = struct.pack(header_fmt, 1, machine, 1, 0, 0, shoff, 0, hsize, 0, 0, ssize, 3, 1)
    sections = [bytes(ssize), struct.pack(section_fmt, 1, 3, 0, 0, name_offset, len(names), 0, 0, 1, 0),
                struct.pack(section_fmt, 11, 7, 0, 0, payload_offset, len(payload), 0, 0, 4, 0)]
    return ident + header + names + payload + b''.join(sections)


class CodegenTests(unittest.TestCase):
    def test_embedded_elf_offsets_bounds_and_both_word_sizes(self):
        for bits in (32, 64):
            child = fixture_elf(machine=224, bits=bits)
            data = fixture_elf(payload=b'prefix'+child)
            found = images(data)
            self.assertEqual(len(found), 2)
            self.assertEqual(found[0]['size'], len(data))
            self.assertEqual(found[1]['machine'], 224)
            self.assertEqual(found[1]['bits'], bits)
            self.assertEqual(found[1]['size'], len(child))
            self.assertEqual(found[1]['sections'][2]['name'], '.note.test')
            self.assertEqual(len(images(data, max_images=1)), 1)
        self.assertEqual(images(b'bad\x7fELF\xff\x01'), [])
        with self.assertRaises(ValueError):
            elf(data[:-1])

    def test_collect_preserves_inputs_checks_identity_and_never_requires_objects(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for kind, trace in (('base', False), ('roles', True)):
                build = root/kind
                build.mkdir()
                (build/'ep.so').write_bytes(fixture_elf())
                flags = dict(UCCL_GPU_STAGE_TRACE=str(int(trace)), USE_URMA='1')
                if trace: flags['UCCL_GPU_STAGE_ROLES_ONLY']='1'
                manifest = dict(module='ep.so', module_sha=digest(build/'ep.so'), sources={'a':'same'}, flags=flags)
                (build/'manifest.json').write_text(json.dumps(manifest))
            before = [digest(root/k/'ep.so') for k in ('base','roles')]
            with patch('collect_codegen.shutil.which', return_value=None):
                collect(root/'base', root/'roles', root/'out', root/'no_compiler')
            report = json.loads((root/'out/codegen.json').read_text())
            self.assertEqual(len(report['artifacts']), 6)
            self.assertEqual(sum(x.get('missing',False) for x in report['artifacts']), 4)
            self.assertEqual(before, [digest(root/k/'ep.so') for k in ('base','roles')])
            self.assertFalse(list((root/'out').rglob('*.elf')))
            (root/'roles/ep.so').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'SHA mismatch'):
                collect(root/'base',root/'roles',root/'bad',root/'no_compiler')

    @unittest.skipUnless(shutil.which('readelf'), 'system readelf unavailable')
    def test_system_readelf_accepts_extracted_fixture(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'image.elf';p.write_bytes(fixture_elf())
            result=subprocess.run(['readelf','-SW',str(p)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
            self.assertEqual(result.returncode,0)
            self.assertIn('.shstrtab',result.stdout)


if __name__ == '__main__':
    unittest.main()
