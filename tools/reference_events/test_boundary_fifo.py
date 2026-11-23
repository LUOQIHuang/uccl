import collections
import copy
import unittest
from audit_boundary_fifo import replay,occupancy,observe_interval,blocking_segments,blocking_summary


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

    def lifecycle_window(self):
        window=self.window()
        window['cpu']['events'][5]['ns']=60
        window['cpu']['events'] += [dict(proxy=0,wr_id=1,stage='post_begin',ns=25,value=0),
                                  dict(proxy=0,wr_id=1,stage='post_end',ns=35,value=0)]
        return window

    def test_head_lifecycle_partition_covers_time_without_double_counting(self):
        groups,rings=replay(self.lifecycle_window(),2)
        rows=blocking_segments(rings[0,0],groups,0,15,81,2)
        durations=collections.Counter()
        for row in rows:durations[row['state']]+=row['duration_ns']
        self.assertEqual(dict(durations),dict(credit_available=6,full_head_pre_submit=5,
            full_head_submission_span=10,full_atomic_wait_cqe=25,full_head_cqe_observed_unretired=20))
        self.assertEqual(sum(durations.values()),66)
        self.assertTrue(all(r['head_wr_id']==1 for r in rows if r['state'].startswith('full_')))

    def test_missing_stage_is_unresolved_not_called_pre_submit(self):
        groups,rings=replay(self.window(),2)
        rows=blocking_segments(rings[0,0],groups,0,25,75,2)
        self.assertEqual({r['state'] for r in rows},{'full_unresolved_network_head'})
        bad=self.lifecycle_window();bad['cpu']['events'][-1]['ns']=70
        groups,rings=replay(bad,2)
        with self.assertRaisesRegex(ValueError,'non-monotonic'):
            blocking_segments(rings[0,0],groups,0,25,75,2)

    def test_partition_summary_includes_zero_states_and_censoring(self):
        common=dict(node=0,label='bf16',channel=0,payload_ordinal=6)
        observations=[dict(common,index=1,publication_ns=0,poll_ns=10,interval_observed=True),
                      dict(common,index=2,publication_ns=0,poll_ns=90,interval_observed=True),
                      dict(common,index=3,publication_ns=10,poll_ns=5,interval_observed=False)]
        segments=[dict(common,index=1,state='full_atomic_wait_cqe',duration_ns=10),
                  dict(common,index=2,state='credit_available',duration_ns=90)]
        rows=blocking_summary(observations,segments)
        atomic=next(r for r in rows if r['state']=='full_atomic_wait_cqe')
        self.assertEqual(atomic['pooled_interval_share_pct'],10)
        self.assertEqual(atomic['interval_share_median_pct'],50)
        self.assertEqual(atomic['observed_intervals'],2)
        self.assertEqual(atomic['censored_intervals'],1)
        self.assertEqual(sum(r['pooled_interval_share_pct'] for r in rows),100)
        with self.assertRaisesRegex(ValueError,'incomplete'):
            blocking_summary(observations,segments[:1])


if __name__=='__main__':unittest.main()
