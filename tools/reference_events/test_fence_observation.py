import unittest
from summarize_fence_observation import evidence_gate


class ObservationGateTests(unittest.TestCase):
    def test_small_gain_needs_reproduction_and_small_differential_probe_effect(self):
        self.assertTrue(evidence_gate([-3.35,-3.52],-3.4,True)['eligible_for_mechanism_review'])
        for changes,trace,quality in (([-3.35,-3.52],0,True),([-3.4,1],-3.4,True),([-3.4,-3.4],-3.4,False)):
            self.assertFalse(evidence_gate(changes,trace,quality)['eligible_for_mechanism_review'])


if __name__=='__main__':unittest.main()
