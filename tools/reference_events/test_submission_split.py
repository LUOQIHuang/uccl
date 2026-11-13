import unittest
from unittest.mock import patch
import audit_notify_submission as audit


class SubmissionSplitTests(unittest.TestCase):
    def fixture(self):
        stages=('poll','decoded','prepare','post_begin','post_end')
        events=[dict(proxy=0,wr_id=11,stage=s,ns=t) for s,t in zip(stages,(2000,2100,3000,3500,4000))]
        pair=dict(proxy=0,atomic_wr_id=11,write_post_end_ns=1000,post_end_gap_us=3)
        return dict(cpu=dict(events=events)),pair

    def test_additive_intervals_and_signed_overlap(self):
        window,pair=self.fixture()
        with patch.object(audit.notify,'pairs',return_value=[pair]):
            row=audit.split(window)[0]
            self.assertAlmostEqual(sum(v for k,v in row.items() if k!='post_end_gap_us'),3)
            pair.update(write_post_end_ns=2500,post_end_gap_us=1.5)
            self.assertEqual(audit.split(window)[0]['write_post_end_to_atomic_poll_us'],-.5)

    def test_missing_or_duplicate_stage_rejected(self):
        for duplicate in (False,True):
            window,pair=self.fixture()
            if duplicate:window['cpu']['events'].append(window['cpu']['events'][0])
            else:window['cpu']['events'].pop(0)
            with patch.object(audit.notify,'pairs',return_value=[pair]),self.assertRaisesRegex(ValueError,'one observation'):
                audit.split(window)


if __name__=='__main__':unittest.main()
