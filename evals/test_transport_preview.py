import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from evals.transport import connect_target, preview_mount_args


class PreviewTransportTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.pin_file = Path(directory.name) / 'pins.json'
        self.pin_file.write_text(json.dumps({'site.example.test:443': '8.8.8.8'}))
        env = patch.dict(os.environ, {'CONCORDE_PUBLIC_PREVIEWS_FILE': str(self.pin_file)})
        env.start()
        self.addCleanup(env.stop)

    def test_exact_public_previews_are_pinned(self):
        self.assertEqual(connect_target('site.example.test:443'), ('8.8.8.8', 443))
        self.assertIn('CONCORDE_PUBLIC_PREVIEWS_FILE=/run/public-previews.json', preview_mount_args())

    def test_other_destinations_remain_denied(self):
        for authority in (
            "8.8.8.8:443", "site.example.test:80",
            "other.example.test:443", "127.0.0.1:443",
            "localhost:443", "example.com:443", "site.example.test:443@127.0.0.1",
        ):
            self.assertIsNone(connect_target(authority))

    def test_subscription_routes_unchanged(self):
        self.assertEqual(connect_target("chatgpt.com:443"), ("chatgpt.com", 443))
        self.assertEqual(connect_target("auth.openai.com:443"), ("auth.openai.com", 443))

    def test_private_preview_pin_is_rejected(self):
        self.pin_file.write_text(json.dumps({'site.example.test:443':'127.0.0.1'}))
        with self.assertRaises(ValueError):connect_target('site.example.test:443')


if __name__ == "__main__":
    unittest.main()
