"""Deterministic protocol-2 command harness for the offline container check."""

import json
import os
import subprocess
import sys


if sys.argv[1:] == ["--concorde-capabilities"]:
    print(json.dumps({"protocol": 2, "continuation": True}))
else:
    packet = json.load(sys.stdin)
    outcome = {"summary": "Offline process qualification completed " + packet["phase"]}
    if packet["phase"] == "rectification":
        status = json.loads(subprocess.check_output(
            ["concorde3", "status", os.environ["CONCORDE3_INSTANCE"]], text=True))
        outcome["completion"] = {
            "expected_seq": status["sequence"],
            "continuation": "stop",
            "coverage": "Disposable offline check has no continuing duty",
            "reason": "The bounded process and state checks are complete",
        }
    print(json.dumps(outcome))
