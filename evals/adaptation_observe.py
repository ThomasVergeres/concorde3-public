"""Host-only, durable snapshots for adaptive-reconsideration episodes."""
import hashlib
import json
import os
from pathlib import Path
import stat

from .cases import stamp
from .grade import product_correct


def read_attachment(workspace, ref):
    """Bounded text; no symlink traversal, special files, or executable replay."""
    parts = ref.split("/") if isinstance(ref, str) else []
    if len(parts) < 2 or parts[0] != "artifacts" or any(p in ("", ".", "..") for p in parts):
        return {"readable": False, "error": "outside receiving interface"}
    directory = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, "rb") as f:
            info = os.fstat(f.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 65536:
                return {"readable": False, "error": "not bounded regular text"}
            raw = f.read(65537)
        if len(raw) > 65536:
            return {"readable": False, "error": "oversized receiving artifact"}
        return {"readable": True, "sha256": hashlib.sha256(raw).hexdigest(), "text": raw.decode("utf-8")}
    except (OSError, UnicodeError) as error:
        return {"readable": False, "error": type(error).__name__}
    finally:
        os.close(directory)


def parse(record):
    try:
        return json.loads(record["text"]) if record.get("readable") else None
    except ValueError:
        return None


def exposed_output(event, message_id):
    """Count returned content, never merely a command/argument naming a source."""
    item = event.get("item", {})
    if event.get("type") != "item.completed":
        return False
    if item.get("type") == "command_execution" and item.get("exit_code") == 0:
        output = item.get("aggregated_output", "")
    elif item.get("type") == "mcp_tool_call" and not item.get("error"):
        output = json.dumps(item.get("result"), ensure_ascii=False)
    else:
        return False
    return message_id in output


def collect_adaptation(workspace, base, stats, stopping, facts):
    from .lab import read_json, save
    last = None
    last_seq = None
    offsets = {}
    stats["adaptation_samples"] = []
    stats["adaptation_exposure"] = []
    try:
        while not stopping.is_set():
            at = stamp()
            reply = parse(read_attachment(workspace, "artifacts/reply.json"))
            refs = reply.get("attachments", []) if isinstance(reply, dict) else []
            attachments = {r: read_attachment(workspace, r) for r in refs[:32] if isinstance(r, str)} if isinstance(refs, list) else {}
            sample = {"reply": reply, "attachments": attachments,
                      "work": read_attachment(workspace, "artifacts/work.json"),
                      "accepted_correct": product_correct(parse(read_attachment(workspace, "artifacts/accepted.json")), facts["accepted_product"])}
            changed = sample != last
            if changed:
                stats["adaptation_samples"].append({"at": at, **sample})
                last = sample
            try:
                state = read_json(workspace / ".concorde2/state.json", workspace)
                seq = state["seq"]
                if seq != last_seq:
                    save(base / "adaptation-state" / f"{seq:08d}.json", {"observed_at": at, "state": state})
                    last_seq = seq
                    changed = True
            except FileNotFoundError:
                pass  # Before driver initialization, not missing run evidence.
            for path in (workspace / ".concorde2/harness-logs").glob("*.jsonl"):
                # Runtime owns these logs; validate containment and refuse links.
                if path.is_symlink() or not path.resolve().is_relative_to(workspace.resolve()):
                    continue
                with path.open() as stream:
                    stream.seek(offsets.get(str(path), 0))
                    for _ in range(2000):
                        start = stream.tell()
                        line = stream.readline(2_000_000)
                        if not line or not line.endswith("\n"):
                            stream.seek(start)
                            break
                        try:
                            event = json.loads(line)
                        except ValueError:
                            continue
                        for marker in (facts["message_id"], facts.get("followup_message_id")):
                            if marker and exposed_output(event, marker):
                                stats["adaptation_exposure"].append({"observed_at": at, "log": path.name,
                                    "byte_offset": start, "event_sha256": hashlib.sha256(line.encode()).hexdigest(),
                                    "item_id": event["item"].get("id"), "message_id": marker,
                                    "basis": "feedback identifier in completed tool output; not proof of comprehension"})
                                changed = True
                    offsets[str(path)] = stream.tell()
            stats["adaptation_observed_until"] = at
            # Persist controller observations while running, not only at teardown.
            # A killed runner cannot silently lose its whole causal timeline.
            if changed:
                save(base / "adaptation-observation.json", {k: v for k, v in stats.items() if k.startswith("adaptation_")})
            stopping.wait(.5)
    except Exception as error:
        stats.setdefault("adaptation_errors", []).append(f"{type(error).__name__}: {error}")
    finally:
        save(base / "adaptation-observation.json", {k: v for k, v in stats.items() if k.startswith("adaptation_")})
