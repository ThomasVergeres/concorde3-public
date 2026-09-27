import tempfile
import unittest
from pathlib import Path

from evals import lab


class LabSessionTests(unittest.TestCase):
    def test_private_session_mount_excludes_auth_and_survives_container(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)/"subject"
            workspace.mkdir()
            source, target, readonly = lab.session_mount(workspace)
            self.assertEqual(source, workspace/".concorde2/harness-sessions")
            self.assertEqual(target, "/home/node/.codex/sessions")
            self.assertFalse(readonly)
            self.assertEqual(source.stat().st_mode & 0o777, 0o700)
            self.assertFalse((source/"auth.json").exists())

    def test_symlink_session_storage_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)/"subject"
            (workspace/".concorde2").mkdir(parents=True)
            outside=Path(tmp)/"outside"; outside.mkdir()
            (workspace/".concorde2/harness-sessions").symlink_to(outside)
            with self.assertRaises(ValueError): lab.session_mount(workspace)
