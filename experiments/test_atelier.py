import unittest
from experiments.atelier import MISSION,CAPABILITIES


class AtelierTests(unittest.TestCase):
    def test_goal_fits_small_node(self):
        self.assertLess(len(MISSION.encode()),4500)

    def test_boundaries_and_human_evidence(self):
        self.assertIn('No public hosting',MISSION)
        self.assertIn('not actual human preference evidence',MISSION)
        self.assertIn('prescribed',MISSION)

    def test_browser_capability_is_discoverable(self):
        self.assertIn('/instance/CAPABILITIES.md',MISSION)
        self.assertIn('/usr/bin/chromium',CAPABILITIES)
        self.assertIn('playwright-core',CAPABILITIES)


if __name__=='__main__': unittest.main()
