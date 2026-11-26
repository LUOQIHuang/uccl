"""Check that a new observation follows the pending head, including head changes."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from fifo_blockers import replay_ring, split_wait
import overlay


class FifoBlockerTests(unittest.TestCase):
    def test_wait_tracks_actual_head_and_observation_gaps(self):
        events = [dict(wr_id=i, ns=i + 1, kind=1) for i in range(8)]
        events += [dict(wr_id=8, ns=20, kind=3), dict(wr_id=0, ns=90, kind=2),
                   dict(wr_id=8, ns=94, kind=1)]
        events += [dict(wr_id=i, ns=100 + i, kind=2) for i in range(1, 9)]
        collected, ready, transitions = replay_ring(events)
        target = dict(post_begin_ns=29, post_end_ns=30, last_invalid_begin_ns=63, first_valid_end_ns=69, sdk_return_ns=81)
        parts = list(split_wait(ready[8], collected[8]['ns'], transitions, {0:target}))
        self.assertEqual([p['head_wr_id'] for p in parts], [0, 1])
        self.assertEqual([parts[0][key] for key in ('target_before_post','target_sdk_submit','target_cqe_invalid',
                         'target_cqe_valid_before_sdk','target_sdk_returned','unresolved')], [9,1,33,12,9,6])
        self.assertEqual(parts[1]['credit_available'], 4)
        with self.assertRaisesRegex(ValueError, 'non-consuming'):
            replay_ring(events[:7] + events[8:])

    @unittest.skipUnless(os.name == 'posix' and shutil.which('c++'), 'Linux C++ compiler')
    def test_generated_native_probe_keeps_all_seven_writes(self):
        generated = overlay.compose('blockers')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('fadd_path_probe.hpp', 'cq_ready_probe.hpp'):
                (root/name).write_text(generated['ep/include/'+name])
            (root/'fixture.cpp').write_text('''#include "fadd_path_probe.hpp"
#include <cassert>
#include <iostream>
using namespace uccl::fadd_path;
int main(){
  assert(!should_peek(4));
  begin(7);
  fifo_observe(0,1,459264,0);
  assert(should_peek(8));
  fifo_observe(8,3,0,0);
  assert(!should_peek(8));
  for(unsigned i=0;i<11;++i){
    auto s=post_begin();
    issued(s,100,200,2*i,true,false,459264,0,nullptr,0,0,0);
    post_end(s,0);
    auto p=poll_begin(100);
    if(i>=7){assert(!p.frame);continue;}
    auto t=poll_end(p,100,1);
    completed(p,100,2*i,t,0);
  }
  std::cout<<end();
}
''')
            subprocess.run(['c++','-std=c++17','-pthread',str(root/'fixture.cpp'),'-o',str(root/'fixture')],check=True)
            data = json.loads(subprocess.check_output([str(root/'fixture')]))
        self.assertEqual(data['target_payload'], 7)
        self.assertEqual([p['write_id'] for p in data['streams'][0]['pairs']], list(range(0,14,2)))
        self.assertTrue(all(p['finished'] for p in data['streams'][0]['pairs']))
        self.assertEqual(len(data['streams'][0]['fifo']), 2)


if __name__ == '__main__':
    unittest.main()
