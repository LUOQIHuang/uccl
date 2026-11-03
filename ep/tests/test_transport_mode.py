"""CPU-only checks of the benchmark's actual Buffer/Proxy mode validation."""
import ast
import contextlib
import io
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

EP=Path(__file__).resolve().parents[1]

def validation():
    tree=ast.parse((EP/'bench/buffer.py').read_text(encoding='utf-8'))
    cls=next(node for node in tree.body if isinstance(node,ast.ClassDef) and node.name=='Buffer')
    cls.body=[node for node in cls.body if isinstance(node,ast.FunctionDef) and node.name=='validate_transport_mode']
    module=ast.parse(''); module.body=[cls]
    namespace=dict(os=os,ep=SimpleNamespace(get_urma_mode_version=lambda:2))
    exec(compile(module,'buffer_mode_test','exec'),namespace)
    return namespace['Buffer'],namespace

class ModeTests(unittest.TestCase):
    def test_both_modes_and_mismatches(self):
        Buffer,namespace=validation()
        for ll in (False,True):
            buffer=Buffer(); buffer.low_latency_mode=ll
            buffer.proxies=[SimpleNamespace(use_normal_mode=lambda:not ll)]
            with patch.dict(os.environ,{'UCCL_EP_TRANSPORT':'urma'}),contextlib.redirect_stdout(io.StringIO()) as output:
                buffer.validate_transport_mode(ll)
                self.assertIn('TRANSPORT_MODE_OK',output.getvalue())
                with self.assertRaises(RuntimeError): buffer.validate_transport_mode(not ll)
                buffer.proxies=[SimpleNamespace(use_normal_mode=lambda:ll)]
                with self.assertRaises(RuntimeError): buffer.validate_transport_mode(ll)
        namespace['ep']=SimpleNamespace(get_urma_mode_version=lambda:1)
        buffer=Buffer(); buffer.low_latency_mode=False
        buffer.proxies=[SimpleNamespace(use_normal_mode=lambda:True)]
        with patch.dict(os.environ,{'UCCL_EP_TRANSPORT':'urma'}):
            with self.assertRaisesRegex(RuntimeError,'rebuilt'): buffer.validate_transport_mode(False)

    def test_benchmarks_select_their_corresponding_modes(self):
        for name,ll in [('test_internode.py',False),('test_low_latency.py',True)]:
            tree=ast.parse((EP/'bench'/name).read_text(encoding='utf-8'))
            calls=[node for node in ast.walk(tree) if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=='validate_transport_mode']
            self.assertEqual(len(calls),1)
            self.assertEqual(ast.literal_eval(calls[0].keywords[0].value),ll)

if __name__=='__main__': unittest.main()
