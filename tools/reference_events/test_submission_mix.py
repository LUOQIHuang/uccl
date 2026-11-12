import unittest
from audit_submission_mix import chains


class SubmissionMixTests(unittest.TestCase):
    def test_calls_are_grouped_per_proxy_and_ends_are_checked(self):
        events=[dict(proxy=0,stage='post_begin',wr_id=1,value=0),
                dict(proxy=1,stage='post_begin',wr_id=3,value=0),
                dict(proxy=0,stage='post_begin',wr_id=2,value=34),
                dict(proxy=0,stage='post_end',wr_id=1,value=0),
                dict(proxy=1,stage='post_end',wr_id=3,value=0),
                dict(proxy=0,stage='post_end',wr_id=2,value=0)]
        result=list(chains(events))
        self.assertEqual([(proxy,[r['wr_id'] for r in chain]) for proxy,chain in result],[(0,[1,2]),(1,[3])])
        events[-1]['wr_id']=99
        with self.assertRaisesRegex(ValueError,'chain boundaries'):list(chains(events))


if __name__=='__main__':unittest.main()
