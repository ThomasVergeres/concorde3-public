"""Offline regressions: transport loss must not kill local work."""
import pathlib
import re
import subprocess
import unittest

ROOT = pathlib.Path(__file__).parent
SCRIPT = (ROOT / 'codex-remote-daemon-repair').read_text()
UNIT = (ROOT / 'codex-remote-control-supervisor.service').read_text()


class SupervisorTests(unittest.TestCase):
    def check_liveness(self, pid_valid, rpc_valid):
        function = SCRIPT.split('daemon_running() {', 1)[1].split('\n}\n', 1)[0]
        shell = '''
CODEX=unused
DAEMON_DIR=/unused
pid_record_is_valid() { return PID; }
timeout() { echo '{"status":"STATUS"}'; }
daemon_running() { BODY
}
daemon_running
'''.replace('PID', '0' if pid_valid else '1').replace('STATUS', 'running' if rpc_valid else 'stopped').replace('BODY', function)
        return subprocess.run(['bash', '-c', shell], capture_output=True).returncode

    def test_live_process_survives_broken_rpc(self):
        self.assertEqual(self.check_liveness(True, False), 0)

    def test_rpc_can_establish_liveness_without_pid_record(self):
        self.assertEqual(self.check_liveness(False, True), 0)

    def test_missing_process_and_rpc_allow_recovery(self):
        self.assertNotEqual(self.check_liveness(False, False), 0)

    def test_no_transport_or_auth_restart_or_stop(self):
        self.assertIsNone(re.search(r'app-server daemon (stop|restart)', SCRIPT))

    def test_missing_updater_does_not_trigger_bootstrap(self):
        self.assertIn('if ! daemon_running; then', SCRIPT)
        self.assertNotIn('|| ! updater_running', SCRIPT)

    def test_observer_restart_preserves_children(self):
        self.assertIn('KillMode=process', UNIT)
        self.assertNotIn('ExecStop=', UNIT)

    def test_shell_syntax(self):
        subprocess.run(['bash', '-n', str(ROOT / 'codex-remote-daemon-repair')], check=True)


if __name__ == '__main__':
    unittest.main()
