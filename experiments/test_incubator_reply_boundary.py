import unittest
from .incubator_reply_boundary import rule


class ReplyBoundaryTests(unittest.TestCase):
    def test_rejects_nonestablished_only_on_named_bridge(self):
        r=rule('br-123456abcdef','daylight')
        self.assertEqual(r[:2],['-i','br-123456abcdef'])
        self.assertEqual(r[2:7],['-m','conntrack','!','--ctstate','ESTABLISHED'])
        self.assertEqual(r[-2:],['-j','REJECT'])
        self.assertNotIn('ACCEPT',r)
    def test_no_broad_or_injected_interface(self):
        for bridge in ('eth0','+','br-1234','br-123456abcdef -j ACCEPT'):
            with self.assertRaises(ValueError):rule(bridge,'daylight')
        with self.assertRaises(ValueError):rule('br-123456abcdef','arbitrary')
    def test_graduation_inherited_names_remain_narrowly_scoped(self):
        for name in ('keel','mosaic','weft'):
            r=rule('br-123456abcdef',name)
            self.assertEqual(r[:2],['-i','br-123456abcdef'])
            self.assertEqual(r[-2:],['-j','REJECT'])
