import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from worlds.forensics import activation_bundle, blob, capture_file, compare_round, decision_bundle, events, replay, snapshot, watch, write_json
from worlds.engine import World


def envelope(seq, changes):
    raw = json.dumps({"seq": seq, "changes": changes}, separators=(",", ":"))
    return ('{"event":'+raw+',"hash":"'+hashlib.sha256(raw.encode()).hexdigest()+'"}\n').encode()


class ForensicsTests(unittest.TestCase):
    def test_closure_is_readonly_and_detects_live_scope(self):
        from unittest.mock import patch
        from worlds.forensics import closure
        for frozen in (False, True):
            with tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);world=World(root/'world');world.create('research')
                if frozen:world.freeze()
                (root/'cohort.json').write_text(json.dumps({'status':'frozen' if frozen else 'running','worlds':{'one':str(root/'world')}}))
                (root/'world/deployment.json').write_text('{"subjects":{}}')
                before=world.s.verify()
                with patch('worlds.forensics.subprocess.check_output',return_value='' if frozen else 'unexpected-live-container\n'):
                    r=closure(root,root/'audit')
                self.assertEqual(bool(r['errors']),not frozen)
                self.assertEqual(world.s.verify(),before)
                with self.assertRaises(ValueError):closure(root,root/'audit')

    def test_watch_rejects_invalid_schedule_before_touching_files(self):
        for start, until, interval in ((1, None, 1), (2, 1, 1), (1, 2, 0), (1, float("inf"), 1)):
            with self.assertRaises(ValueError): watch("unused", "unused", start, until, interval)

    def test_exact_prefix_reconstruction_and_deletion(self):
        a = envelope(1, [{"field": "items", "key": "a", "value": {"text": "initial"}}])
        b = envelope(2, [{"field": "items", "key": "a", "value": {"text": "revised"}}])
        c = envelope(3, [{"field": "items", "key": "a", "delete": True}])
        self.assertEqual(replay(a+b+c, 1)["items"]["a"]["text"], "initial")
        self.assertEqual(replay(a+b+c, 2)["items"]["a"]["text"], "revised")
        self.assertEqual(replay(a+b+c, 3)["items"], {})
        self.assertEqual(replay(a, 1)["programs"], {})
        self.assertEqual(replay(a, 1)["last_urgent"], 0)
        self.assertEqual(replay(a+b+b'{partial', 2)["seq"], 2)

    def test_corruption_gap_or_missing_target_rejected(self):
        a = envelope(1, [{"field": "version", "value": 2}])
        with self.assertRaises(ValueError): replay(a.replace(b'"value":2', b'"value":3'), 1)
        with self.assertRaises(ValueError): replay(a+envelope(3, []), 3)
        with self.assertRaises(ValueError): replay(a, 2)

    def test_content_addressing_truncation_and_symlink_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source = root/"source"; source.write_bytes(b"abcdef")
            first = capture_file(root, source, "source", limit=3)
            self.assertTrue(first["truncated"])
            self.assertEqual((root/first["blob"]).read_bytes(), b"abc")
            source.write_bytes(b"changed")
            self.assertEqual((root/first["blob"]).read_bytes(), b"abc")
            self.assertEqual(blob(root, b"abc")["sha256"], first["sha256"])
            link = root/"link"; link.symlink_to(source)
            with self.assertRaises(OSError): capture_file(root, link, "link")
            self.assertEqual((root/first["blob"]).stat().st_mode & 0o777, 0o600)

    def test_snapshot_never_replaces_existing_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root/"cohort.json").write_text('{"worlds":{}}')
            audit = root/"audit"
            snapshot(root, audit, 0, sessions=False)
            with self.assertRaises(ValueError): snapshot(root, audit, 0, sessions=False)

    def test_incomplete_jsonl_is_explicit(self):
        rows, errors = events(b'{"ok":true}\n{partial')
        self.assertEqual(rows, [{"ok": True}]); self.assertEqual(errors, [2])

    def test_world_backup_and_delta_do_not_mutate_live_world(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); world = World(root/"world"); world.create(seed=3)
            (root/"world"/"subjects").mkdir()
            logs=root/"world/subjects/self/.concorde2/program-logs"
            logs.mkdir(parents=True)
            (logs/"watcher.log").write_text("masked dependency diagnostic")
            (root/"world/subjects/self/.env").write_text("SYNTHETIC_SECRET=not-a-real-key")
            (root/"cohort.json").write_text(json.dumps({"worlds": {"one": str(root/"world")}}))
            audit = root/"audit"
            before = world.s.verify()
            first = snapshot(root, audit, 0, sessions=False)
            self.assertEqual(world.s.verify(), before)
            self.assertFalse(first["errors"])
            captured={f["path"]:f for f in first["worlds"]["one"]["files"]}
            self.assertIn("subjects/self/.concorde2/program-logs/watcher.log",captured)
            self.assertNotIn("subjects/self/.env",captured)
            world.act("northstar", "one", {"op": "remember", "text": "new evidence"})
            snapshot(root, audit, 1, sessions=False)
            delta = compare_round(audit, 1)
            self.assertEqual([e["kind"] for e in delta["worlds"]["one"]["events"]], ["remember"])
            self.assertEqual(compare_round(audit, 0)["worlds"]["one"]["watermark"], first["worlds"]["one"]["event_watermark"])

    def test_bundle_matches_session_and_does_not_invent_reasoning(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); prefix = "subjects/self/.concorde2/"
            history = envelope(1, [{"field": "activations", "key": "act.1", "value": {"id": "act.1", "session": "session.1"}}])
            state = replay(history, 1)
            inputs = {prefix+"events.jsonl": history, prefix+"state.json": json.dumps(state).encode(),
                      **{prefix+"contexts/act.1."+phase+".json": b'{"sequence":1}' for phase in
                         ("work","rectification","work.return1","rectification.return1")}}
            session = b'{"type":"session_meta","payload":{"id":"session.1"}}\n{"type":"response_item","payload":{"type":"reasoning","summary":[],"encrypted_content":"opaque"}}\n'
            write_json(root/"rounds/00.json", {"worlds": {"world": {
                "files": [{"path": name, **blob(root, data)} for name,data in inputs.items()],
                "sessions": [{"actor": "self", "path": "session.jsonl", **blob(root, session)}]}}})
            result = activation_bundle(root, 0, "world", "self", "act.1")
            self.assertEqual(result["session_count"], 1)
            self.assertEqual(result["reasoning"]["records"], 1)
            self.assertEqual(result["reasoning"]["readable_summaries"], 0)
            saved = json.loads(Path(result["bundle"]).read_text())
            self.assertEqual(saved["phases"]["work"]["pre_state"], state)
            self.assertEqual(set(saved["phases"]),{"work","rectification","work.return1","rectification.return1"})

    def test_counterpart_bundle_uses_actor_not_latest_unrelated_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); world = World(root/"world"); world.create(seed=3)
            (root/"world"/"subjects").mkdir()
            (root/"cohort.json").write_text(json.dumps({"worlds": {"one": str(root/"world")}}))
            first = world.reserve_call("northstar")
            world.finish_call(first["id"], "completed", {"fixture": True})
            unrelated = world.reserve_call("juniper")
            world.finish_call(unrelated["id"], "completed", {"fixture": True})
            with world.s.transaction() as db:
                seq = world.s.event(db, "northstar", "decision", {"results": [], "note": "fixture"})
            snapshot(root, root/"audit", 0, sessions=False)
            result = decision_bundle(root/"audit", 0, "one", seq)
            self.assertEqual(result["call"], first["id"])
            self.assertNotEqual(result["call"], unrelated["id"])
            with self.assertRaises(ValueError): decision_bundle(root/"audit", 0, "one", 1)
