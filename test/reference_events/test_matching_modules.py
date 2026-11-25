from pathlib import Path
import tempfile
import unittest
from collect_matching_modules import candidates,copy_sources


class MatchingTests(unittest.TestCase):
    def test_version_match_is_required_and_generated_files_stay_in_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'tree';module=root/'urma/hw/udma';module.mkdir(parents=True)
            (module/'udma.mod.c').write_text('MODULE_INFO(srcversion,"ABC123");')
            (module/'udma_main.c').write_text('/* source */')
            (module/'udma.o').write_bytes(b'binary')
            rows=candidates([root],{'udma':'ABC123'})
            self.assertEqual(sum(r['matched'] for r in rows),1)
            self.assertFalse(any(r['matched'] for r in candidates([root],{'udma':'DIFFERENT'})))
            files=copy_sources(module,Path(tmp)/'out')
            self.assertEqual(list(files),['udma_main.c'])


if __name__=='__main__':unittest.main()
