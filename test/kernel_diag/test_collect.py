import csv
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
SPEC=importlib.util.spec_from_file_location('kernel_collect',Path(__file__).with_name('collect.py'))
m=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(m)

class KernelTests(unittest.TestCase):
    def test_config_parser_preserves_unset_and_missing(self):
        values=m.config_values('CONFIG_NUMA=y\n# CONFIG_PREEMPT is not set\nCONFIG_LOCALVERSION="-diag"\n')
        self.assertEqual(values,dict(CONFIG_NUMA='y',CONFIG_PREEMPT='n',CONFIG_LOCALVERSION='"-diag"'))
        self.assertNotIn('CONFIG_IOMMU',values)

    def test_two_offline_kernels_builtins_and_runtime_separation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);boot=root/'boot';boot.mkdir();modules=root/'modules';out=root/'result';out.mkdir()
            for release in (m.VERBS,m.URMA):
                (modules/release).mkdir(parents=True)
                (modules/release/'modules.builtin').write_text('kernel/drivers/infiniband/ubcore.ko\n')
            (boot/('config-'+m.VERBS)).write_text('CONFIG_HZ=250\n# CONFIG_PREEMPT is not set\n')
            (boot/('config-'+m.URMA)).write_text('CONFIG_HZ=1000\nCONFIG_PREEMPT=y\n')
            binary=root/'metax.ko';binary.write_bytes(b'fixture')
            calls=[]
            def command(args,timeout=15):
                calls.append(args);self.assertEqual(args[:2],['modinfo','-k'])
                if args[3]=='metax':return dict(rc=0,stdout='filename: '+str(binary)+'\nversion: test\nsrcversion: DISK\nvermagic: '+args[2]+' SMP\n',command=args,stderr='')
                return dict(rc=1,stdout='',stderr='module not found',command=args)
            with patch.object(m.shutil,'which',return_value='modinfo'),patch.object(m,'command',side_effect=command):
                kernels=[m.inspect_kernel(k,out/str(i),modules,boot) for i,k in enumerate((m.VERBS,m.URMA))]
            self.assertEqual(len(calls),2*len(m.MODULES))
            self.assertTrue(kernels[0]['modules']['ubcore']['builtin_listed'])
            self.assertFalse(kernels[0]['modules']['ubcore']['metadata_resolved'])
            self.assertTrue(kernels[1]['modules']['metax']['vermagic_release_matches'])
            runtime=dict(modules={name:dict(present=name=='metax',fields={'srcversion':{'text':'LOADED'}},parameters={}) for name in m.MODULES},atomic_control={'stdout':'0020'})
            m.render(dict(kernels=kernels,running_kernel=m.VERBS,host='fixture',runtime=runtime),out)
            report=(out/'SUMMARY.zh-CN.md').read_text()
            self.assertIn('已加载srcversion与磁盘模块不同',report)
            self.assertIn('bit6=0',report)
            self.assertIn('未解析（需核实',report)
            with (out/'config_diff.csv').open() as stream:diff=list(csv.DictReader(stream))
            self.assertEqual(len(diff),2)
            kernels[1]['config']=None;m.render(dict(kernels=kernels,running_kernel=m.VERBS,host='fixture',runtime=runtime),out)
            self.assertIn('不能输出完整配置差异',(out/'SUMMARY.zh-CN.md').read_text())

    def test_missing_and_bounded_reads(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'value';self.assertIn('error',m.read_file(path));path.write_text('abcdefgh')
            with patch.object(m,'LIMIT',4):self.assertEqual(m.read_file(path),dict(path=str(path),text='abcd',truncated=True))

if __name__=='__main__':unittest.main()
