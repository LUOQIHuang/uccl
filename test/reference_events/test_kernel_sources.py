"""Verify source collection scope, preservation and exclusion of binaries/links."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import collect_kernel_sources as collector


class SourceTests(unittest.TestCase):
    def test_collects_only_selected_sources_and_preserves_existing_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'tree'
            for rel in ('drivers/iommu/hisilicon/interrupt.c','drivers/ub/urma/hw/udma/udma_main.c',
                        'drivers/ub/urma/hw/udma/binary.o','unrelated/private.c'):
                path=root/rel;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'fixture')
            out=Path(tmp)/'out'
            result=collector.collect_sources(root,out)
            self.assertEqual(len(result['files']),2)
            self.assertFalse((out/'sources/kernel/unrelated').exists())
            self.assertFalse((out/'sources/kernel/drivers/ub/urma/hw/udma/binary.o').exists())
            with self.assertRaises(FileExistsError):collector.collect_sources(root,out)

    def test_enforces_aggregate_size_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'tree';path=root/'drivers/iommu/hisilicon/interrupt.c'
            path.parent.mkdir(parents=True);path.write_bytes(b'too big')
            with patch.object(collector,'MAX_BYTES',1),self.assertRaisesRegex(RuntimeError,'COPY_LIMIT'):
                collector.collect_sources(root,Path(tmp)/'out')


if __name__=='__main__':unittest.main()
