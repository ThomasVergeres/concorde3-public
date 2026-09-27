import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import unittest

from experiments.discrepancy_transport import process_start_ticks, terminate_group


def executing(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] not in {"Z", "X"}
    except FileNotFoundError:
        return False


class MetaDescendantTests(unittest.TestCase):
    def test_parent_exit_is_not_proof_that_its_ignoring_child_stopped(self):
        script = "import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); print(\\\"ready\\\",flush=True); time.sleep(60)'],stdout=subprocess.PIPE,text=True); p.stdout.readline(); print(p.pid,flush=True); time.sleep(60)"
        parent = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE,
            text=True, start_new_session=True)
        child = None
        try:
            child = int(parent.stdout.readline())
            ticks = process_start_ticks(parent.pid)
            self.assertTrue(terminate_group(parent, ticks, grace=.2))
            self.assertFalse(executing(child), "leader stopped but model child still executing")
        finally:
            if child is not None and executing(child): os.kill(child, signal.SIGKILL)
            if parent.poll() is None: parent.kill()
            parent.wait(timeout=5); parent.stdout.close()
