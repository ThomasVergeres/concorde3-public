"""No-model witness of the exact isolated writer's login-shell Go toolchain."""
import argparse
import hashlib
import json
from pathlib import Path
import time

from evals.lab import command, save
from .isolated_writer import IsolatedWriter


def run(root, image, until):
    root = Path(root).resolve()
    if root.exists(): raise ValueError("fresh witness root required")
    image = command(["docker", "image", "inspect", image, "--format", "{{.Id}}"])
    root.mkdir(parents=True, mode=0o700)
    source = root/"cases/toolchain/source"; source.mkdir(parents=True)
    (source/"go.mod").write_text("module example.invalid/toolchainwitness\n\ngo 1.24\n")
    (source/"fixture_test.go").write_text('package witness\nimport ("testing"; "strings")\nfunc TestToolchain(t *testing.T) { if strings.ToUpper("probe") != "PROBE" { t.Fatal("toolchain witness failed") } }\n')
    writer = IsolatedWriter(root, "no-model-toolchain-witness", source, image, until)
    result = {"model_dispatched": False, "image": image, "until": until,
        "source_revision": command(["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[1]),
        "writer_sha256": hashlib.sha256(Path(__file__).with_name("isolated_writer.py").read_bytes()).hexdigest(),
        "scope": "Mechanical runtime check, not intelligent writer qualification or C3 behavior"}
    try:
        writer.start()
        result["test_output"] = command(["docker", "exec", writer.worker, "/bin/bash", "-lc",
            "gofmt -w fixture_test.go && go test -count=1 ./..."], timeout=max(1, min(120, until-time.time())))
        result["status"] = "mechanically_qualified"
    except Exception as error:
        result.update(status="failed", error=str(error))
    finally:
        result["verified_stopped"] = writer.stop()
        if not result["verified_stopped"]: result["status"] = "cleanup_unverified"
        save(root/"result.json", result)
    print(json.dumps(result), flush=True)
    return result


if __name__ == "__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("root", type=Path); p.add_argument("--image", required=True)
    p.add_argument("--until", type=float, required=True)
    a=p.parse_args(); run(a.root, a.image, a.until)
