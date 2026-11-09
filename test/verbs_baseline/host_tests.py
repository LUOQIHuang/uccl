"""CPU checks against installed real verbs headers; GPU/driver boundaries simulated."""
import os
from pathlib import Path
import subprocess
import tempfile
HERE=Path(__file__).resolve().parent;REPO=HERE.parents[1]
with tempfile.TemporaryDirectory(prefix='uccl-verbs-host-') as tmp:
    compiler=os.environ.get('CXX','g++');target=Path(tmp)/'diag_test'
    flags=[compiler,'-std=c++17','-pthread','-Wall','-Wextra','-Werror','-I'+str(REPO/'ep/include')]
    subprocess.run(flags+[str(REPO/'ep/tests/verbs_diag_test.cpp'),'-libverbs','-o',str(target)],check=True)
    subprocess.run([str(target)],check=True)
    subprocess.run(flags+['-DUSE_DMABUF','-I'+str(REPO/'ep/tests/urma_support'),'-include',str(REPO/'ep/tests/urma_support/ep_shim.hpp'),'-fsyntax-only',str(REPO/'ep/src/verbs_write_micro.cpp')],check=True)
    print('Verbs native micro host syntax passed; no MetaX/hardware validation')
