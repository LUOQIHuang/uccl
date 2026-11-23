import collections
import copy
import unittest
from audit_boundary_fifo import replay,occupancy,observe_interval


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

    def test_overlapping_publication_is_censored_not_zero_queueing(self):
        groups,rings=replay(self.window(),2)
        for start,end in ((30,25),(30,30)):
            row=observe_interval(rings[0,0],groups,0,start,end)
            self.assertFalse(row['interval_observed'])
            self.assertEqual(row['full_pct'],'')
            self.assertEqual(row['published_to_poll_us'],'')
        row=observe_interval(rings[0,0],groups,0,25,75)
        self.assertTrue(row['interval_observed'])

    def test_write_head_and_atomic_head_are_separate_blockers(self):
        window=self.window();window['cpu']['events'][1].update(stage='payload_bytes',value=8192)
        groups,rings=replay(window,2)
        row=occupancy(rings[0,0],groups,0,25,75,2)
        self.assertEqual(row['payload_head_no_observed_cqe_pct'],100)
        self.assertEqual(row['atomic_head_no_observed_cqe_pct'],0)
        late=occupancy(rings[0,0],groups,0,75,80,2)
        self.assertEqual(late['payload_head_no_observed_cqe_pct'],80)


if __name__=='__main__':unittest.main()
