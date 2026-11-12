import collections
import copy
import unittest
from audit_boundary_fifo import replay,occupancy


class FifoOwnershipTests(unittest.TestCase):
    def window(self):
        # A later WRITE completes at 30 but retains its slot behind an atomic
        # until 80. Hardware completion does not itself release FIFO ownership.
        return {'cpu':{'origin_ns':0,'events':[
            dict(proxy=0,wr_id=1,stage='collect',ns=10,value=0),
            dict(proxy=0,wr_id=1,stage='atomic_value',ns=11,value=32),
            dict(proxy=0,wr_id=2,stage='collect',ns=20,value=0),
            dict(proxy=0,wr_id=2,stage='payload_bytes',ns=21,value=8192),
            dict(proxy=0,wr_id=2,stage='cqe',ns=30,value=0),
            dict(proxy=0,wr_id=1,stage='cqe',ns=79,value=0),
            dict(proxy=0,wr_id=1,stage='ack_slot',ns=80,value=0),
            dict(proxy=0,wr_id=2,stage='ack_slot',ns=81,value=0)]}}

    def test_completed_follower_does_not_release_credit(self):
        groups,rings=replay(self.window(),2)
        row=occupancy(rings[0,0],groups,0,25,75,2)
        self.assertEqual(row['pending_at_publication'],2)
        self.assertEqual(row['full_pct'],100)
        self.assertEqual(row['atomic_head_full_pct'],100)
        self.assertEqual(row['atomic_head_no_observed_cqe_pct'],100)
        tail=occupancy(rings[0,0],groups,0,75,80,2)
        self.assertEqual(tail['atomic_head_no_observed_cqe_pct'],80)

    def test_rejects_missing_out_of_order_over_limit_or_unretired_events(self):
        window=self.window()
        bad=copy.deepcopy(window);bad['cpu']['events'][6]['wr_id']=2
        with self.assertRaisesRegex(ValueError,'out-of-order'):replay(bad,2)
        with self.assertRaisesRegex(ValueError,'exceeds'):replay(window,1)
        bad=copy.deepcopy(window);bad['cpu']['events'].pop()
        with self.assertRaisesRegex(ValueError,'incomplete'):replay(bad,2)


if __name__=='__main__':unittest.main()
