import unittest

from worlds.reporting_runtime_witness import disabled_cognition_command


class RuntimeWitnessCommandTests(unittest.TestCase):
    def test_retains_mounts_and_network_but_never_runs_subject_entrypoint(self):
        args = ["docker", "run", "-d", "--name", "c3-world-test-steward", "--network", "private",
                "--mount", "type=bind,source=/private/self,target=/instance", "--entrypoint", "python3", "sha256:image", "/subject.py"]
        changed = disabled_cognition_command(args)
        self.assertEqual(changed[:-2], args[:-1])
        self.assertEqual(changed[-2:], ["-c", "import time; time.sleep(180)"])
        self.assertEqual(args[-1], "/subject.py")

    def test_changed_subject_entrypoint_fails_closed_before_dispatch(self):
        args = ["docker", "run", "-d", "--name", "c3-world-test-steward", "--entrypoint", "concorde3", "sha256:image", "run", "/instance"]
        with self.assertRaisesRegex(ValueError, "unrecognized"):
            disabled_cognition_command(args)

    def test_non_launch_commands_remain_unchanged(self):
        args = ["docker", "image", "inspect", "image", "--format", "{{.Id}}"]
        self.assertEqual(disabled_cognition_command(args), args)

    def test_known_world_gateway_remains_unchanged(self):
        args = ["docker", "run", "-d", "--name", "c3-world-test-world", "--entrypoint", "python3", "image",
                "-m", "worlds.cli", "serve", "/data", "--host", "0.0.0.0", "--port", "8080"]
        self.assertEqual(disabled_cognition_command(args), args)
