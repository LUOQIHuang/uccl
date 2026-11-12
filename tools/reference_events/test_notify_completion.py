import copy
import unittest
from audit_notify_completion import pairs,TOKEN_BYTES


class NotifyCompletionTests(unittest.TestCase):
    def window(self):
        events=[]
        for wid,stages in [(10,{'payload_bytes':(5,32*TOKEN_BYTES),'post_begin':(9,0),'post_end':(10,0),'cqe':(100,0)}),
                           (11,{'atomic_value':(19,32),'post_begin':(19,0),'post_end':(20,0),'cqe':(500,0)})]:
            for stage,(ns,value) in stages.items():events.append(dict(proxy=0,wr_id=wid,stage=stage,ns=ns,value=value,qp=1))
        return dict(index=1,cpu=dict(origin_ns=0,events=events))

    def test_preposted_notify_gap_and_late_post_are_both_retained(self):
        window=self.window();row=pairs(window)[0]
        self.assertTrue(row['atomic_posted_before_write_cqe'])
        self.assertEqual(row['completion_gap_us'],.4)
        next(e for e in window['cpu']['events'] if e['wr_id']==11 and e['stage']=='post_end')['ns']=200
        self.assertFalse(pairs(window)[0]['atomic_posted_before_write_cqe'])

    def test_rejects_bad_correspondence_and_split_physical_posts(self):
        for field,value in [('qp',2),('wr_id',12)]:
            window=self.window()
            next(e for e in window['cpu']['events'] if e['wr_id']==11 and e['stage']=='post_end')[field]=value
            with self.assertRaises(ValueError):pairs(window)
        window=self.window();next(e for e in window['cpu']['events'] if e['stage']=='atomic_value')['value']=31
        with self.assertRaisesRegex(ValueError,'token count'):pairs(window)
        window=self.window();window['cpu']['events'].append(copy.deepcopy(next(e for e in window['cpu']['events'] if e['stage']=='post_end')))
        with self.assertRaisesRegex(ValueError,'physical'):pairs(window)


if __name__=='__main__':unittest.main()
