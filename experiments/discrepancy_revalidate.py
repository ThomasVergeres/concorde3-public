"""Read-only revalidation of original responses; never rewrite campaign history."""
import argparse
from collections import Counter
import json
from pathlib import Path
import sqlite3

from .discrepancy_engine import validate_review


def inspect(root, rounds):
    root = Path(root).resolve()
    with sqlite3.connect((root/"discrepancies.sqlite").as_uri()+"?mode=ro", uri=True) as db:
        for number in rounds:
            calls = db.execute("SELECT id FROM calls WHERE purpose=?", (f"review:{number}",)).fetchall()
            if len(calls) != 1:
                yield {"round": number, "status": "unavailable", "reason": "no unique original review call"}
                continue
            try:
                packet = json.loads((root/"reviews"/f"{number:02d}"/"packet.json").read_text())
                response = json.loads((root/"meta-calls"/calls[0][0]/"result.json").read_text())
                value = validate_review(response, packet)
                yield {"round": number, "validation_status": value["validation_status"],
                       "valid_judgments": len(value["judgments"]), "valid_positives": len(value["positives"]),
                       "rejected": len(value["rejected_items"]),
                       "reasons": dict(Counter(i["reason"] for i in value["rejected_items"]))}
            except (OSError, ValueError) as error:
                yield {"round": number, "status": "unavailable", "reason": type(error).__name__}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--round", type=int, action="append", dest="rounds")
    args = parser.parse_args()
    for row in inspect(args.root, args.rounds or range(1, 19)):
        print(json.dumps(row, sort_keys=True))
