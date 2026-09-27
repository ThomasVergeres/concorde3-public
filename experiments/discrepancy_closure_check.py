"""Independent read-only audit of finite campaign closure; originals stay untouched."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import time

from evals.lab import save
from .discrepancy_campaign import closure_issues


class ReadOnlyStore:
    def __init__(self, path): self.path = Path(path).resolve()

    def rows(self, table, where="1", args=()):
        if table not in {"rounds", "reviews", "calls", "jobs"}: raise ValueError("unsupported audit table")
        with sqlite3.connect(self.path.as_uri()+"?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            return [dict(r) for r in db.execute(f"SELECT * FROM {table} WHERE {where}", args)]


def check(root):
    root = Path(root).resolve(); issues = {}; manifest = json.loads((root/"cohort.json").read_text())
    if manifest["status"] not in {"closed", "closed_requires_review"}:
        issues["controller_status"] = manifest["status"]
    try: closure = json.loads((root/"closure.json").read_text())
    except (OSError, ValueError): closure = None
    freezes = sorted((root/"freeze-receipts").glob("*.json"))
    freeze = None
    if freezes:
        try: freeze = json.loads(freezes[-1].read_text())
        except (OSError, ValueError): pass
    issues.update(closure_issues(root, ReadOnlyStore(root/"discrepancies.sqlite"), closure, freeze))
    final = None
    try: final = json.loads((root/"final-report.json").read_text())
    except (OSError, ValueError): issues["final_report"] = "missing or unreadable"
    if final and final.get("errors"): issues["original_report_errors"] = final["errors"]
    return {"at": time.time(), "root": str(root), "cutoff": manifest.get("cutoff"),
        "original_status": manifest["status"], "issues": issues,
        "status": "requires_review" if issues else "closure_evidence_complete",
        "limitation": "Read-only audit of captured closure and current queue/inventory, not fresh process termination, original-report rewrite or business readiness."}


if __name__ == "__main__":
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__); p.add_argument("root", type=Path)
    p.add_argument("--output", type=Path); a = p.parse_args()
    if a.output and a.output.exists(): raise ValueError("preserve existing independent closure audit")
    result = check(a.root)
    if a.output: save(a.output, result)
    print(json.dumps(result, indent=2))
