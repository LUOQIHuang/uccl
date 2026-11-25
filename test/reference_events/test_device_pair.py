import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from test_window_plan import driver_functions
from test_case_control import namespace


class DevicePairTests(unittest.TestCase):
    def test_plan_validates_local_devices_and_preserves_shared_workload_checks(self):
        ns=driver_functions('urma')
        base=['driver','--job','pair','--mode','normal','--urma-device-plan','202c1-204c0']
        for rank,device in ((0,'udmac1d1e2'),(1,'udmac0d1e2')):
            argv=base+['--node-rank',str(rank),'--urma-device',device]
            with patch.object(sys,'argv',argv):args=ns['parser']()
            self.assertEqual(ns['environment'](args)['UCCL_URMA_DEV_NAME'],device)
            with patch.object(sys,'argv',argv+['--urma-device','wrong']),patch('sys.stderr',io.StringIO()),self.assertRaises(SystemExit):ns['parser']()
        shared=namespace['shared_fingerprint']
        a=dict(device='udmac1d1e2',urma_device_plan='202c1-204c0',shape=[128],sources='v1')
        b=dict(a,device='udmac0d1e2')
        self.assertEqual(shared(a),shared(b))
        self.assertNotEqual(shared(a),shared(dict(b,shape=[4096])))
        self.assertNotEqual(shared(dict(a,urma_device_plan='same')),shared(dict(b,urma_device_plan='same')))

    @unittest.skipUnless(os.name=='posix' and shutil.which('bash'),'Linux orchestration')
    def test_fixed_wrapper_selects_user_requested_pair_and_only_one_control(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'try_device_pair.sh').write_text(Path(__file__).with_name('try_device_pair.sh').read_text())
            (root/'recover.sh').write_text('exec "$URMA_DIAG_PYTHON" "$(dirname "$0")/capture.py" "$@"\n')
            (root/'run_boundaries.sh').write_text('exec "$URMA_DIAG_PYTHON" "$(dirname "$0")/capture.py" "$@"\n')
            (root/'capture.py').write_text('import json,sys;print(json.dumps(dict(zip(sys.argv[1::2],sys.argv[2::2]))))\n')
            for rank,device in ((0,'udmac1d1e2'),(1,'udmac0d1e2')):
                env=dict(os.environ,URMA_DIAG_PYTHON=sys.executable)
                r=subprocess.run(['bash',str(root/'try_device_pair.sh'),'--node-rank',str(rank)],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
                self.assertEqual(r.returncode,0,r.stderr)
                args=json.loads(r.stdout.splitlines()[-1])
                self.assertEqual(args['--urma-device'],device)
                self.assertEqual(args['--urma-device-plan'],'202c1-204c0')
                self.assertEqual(args['--step'],'paced')
                self.assertEqual(args['--case-timeout'],'300')
                self.assertEqual(args['--normal-fadd-fence-plan'],'peer-tail-off')
                for part in ('smoke','coverage'):
                    r=subprocess.run(['bash',str(root/'try_device_pair.sh'),'--node-rank',str(rank),'--part',part],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True)
                    self.assertEqual(r.returncode,0,r.stderr)
                    args=json.loads(r.stdout.splitlines()[-1])
                    self.assertEqual(args['--part'],part)
                    self.assertEqual(args['--urma-device'],device)
                    self.assertEqual(args['--urma-device-plan'],'202c1-204c0')


if __name__=='__main__':unittest.main()
