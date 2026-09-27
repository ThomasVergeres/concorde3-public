import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from experiments.discrepancy_repair import validate_diff, isolated_go_command, candidate_token


class CandidateDiffTests(unittest.TestCase):
    def test_candidate_namespace_includes_campaign_not_just_case(self):
        self.assertEqual(candidate_token('/tmp/campaign-a','case-shared'),candidate_token('/tmp/campaign-a','case-shared'))
        self.assertNotEqual(candidate_token('/tmp/campaign-a','case-shared'),candidate_token('/tmp/campaign-b','case-shared'))
    def test_candidate_execution_is_no_network_readonly_and_image_pinned(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root/"source"; source.mkdir()
            output = root/"output"; output.mkdir()
            args = isolated_go_command(source, "sha256:"+"a"*64, ["test", "-race", "./core"])
            self.assertEqual(args[args.index("--network")+1], "none")
            self.assertIn("--read-only", args)
            self.assertIn(f"type=bind,source={source},target=/src,readonly", args)
            self.assertNotIn("/out", " ".join(args))
            self.assertNotIn("auth", " ".join(args))
            built = isolated_go_command(source, "sha256:"+"a"*64, ["build"], output=output)
            self.assertIn(f"type=bind,source={output},target=/out", built)
            with self.assertRaises(ValueError): isolated_go_command(source, "compiler:latest", ["test"])
            with self.assertRaises(ValueError): isolated_go_command(source, "sha256:"+"a"*64, ["test"], output=source)

    def repo(self, root):
        def git(*args):
            return subprocess.check_output(["git", *args], cwd=root, stderr=subprocess.DEVNULL, text=True).strip()
        git("init", "-q"); git("config", "user.email", "fixture@example.invalid")
        git("config", "user.name", "Test Fixture")
        (root/"core").mkdir(); (root/"core/base.go").write_text("package core\n")
        git("add", "."); git("commit", "-qm", "fixture")
        return git("rev-parse", "HEAD")

    def test_new_files_are_validated_and_returned_for_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); base = self.repo(root)
            (root/"core/new_test.go").write_text("package core\n")
            self.assertEqual(validate_diff(root, base), ["core/new_test.go"])

    def test_revisable_practices_are_allowed_without_opening_policy_or_fixtures(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);base=self.repo(root);(root/'core/kits').mkdir()
            for name in ('memory','rectification'):
                (root/f'core/kits/{name}.md').write_text('Editable, proportionate judgment guidance.\n')
            self.assertEqual(validate_diff(root,base),['core/kits/memory.md','core/kits/rectification.md'])
            (root/'core/kits/capabilities.md').write_text('Changed capabilities')
            with self.assertRaisesRegex(RuntimeError,'forbidden path'):validate_diff(root,base)

    def test_practice_contents_are_bounded_text_not_blank_or_binary(self):
        for contents in (b'',b' '*20,b'x'*5001,b'\xff',b'ordinary\x00text'):
            with self.subTest(contents=contents[:10]),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);base=self.repo(root);(root/'core/kits').mkdir()
                (root/'core/kits/memory.md').write_bytes(contents)
                with self.assertRaisesRegex(RuntimeError,'practice'):validate_diff(root,base)

    def test_untracked_out_of_scope_file_cannot_hide_behind_valid_diff(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); base = self.repo(root)
            (root/"core/base.go").write_text("package core\n// changed\n")
            (root/"outside.py").write_text("# unapproved\n")
            with self.assertRaisesRegex(RuntimeError, "forbidden path"):
                validate_diff(root, base)

    def test_candidate_symlink_is_not_compilable_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); base = self.repo(root)
            (root/"core/base.go").unlink()
            (root/"core/base.go").symlink_to("/etc/passwd")
            with self.assertRaisesRegex(RuntimeError, "symlink"):
                validate_diff(root, base)
