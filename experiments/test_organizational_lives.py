import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from experiments import organizational_lives as lives
from worlds import campaign
from worlds.engine import World


class OrganizationalLivesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "cohort"

    def prepare(self):
        def command(args, **_):
            return "sha256:" + args[3] if args[:3] == ["docker", "image", "inspect"] else "revision"
        with patch.object(lives, "command", side_effect=command):
            return lives.prepare(self.root, "baseline", "candidate")

    def test_four_subjects_matched_calendar_images_caps_and_finite_window(self):
        manifest = self.prepare()
        self.assertEqual(len(manifest["arms"]), 4)
        self.assertEqual(manifest["cutoff"] - manifest["started"], 7200)
        self.assertEqual([s["offset_seconds"] for s in manifest["ordinary_exposure_schedule"]],
                         [0, 1320, 1920, 2820, 3600, 4680, 5640])
        self.assertEqual(len({a["budget_file"] for a in manifest["arms"].values()}), 1)
        for pack in ("market", "consumer"):
            left, right = (manifest["arms"][pack + "-" + arm] for arm in ("baseline", "candidate"))
            self.assertEqual(left["calendar"], right["calendar"])
            self.assertEqual(left["scenario_hash"], right["scenario_hash"])
            self.assertNotEqual(left["image"], right["image"])
        for path in manifest["worlds"].values():
            world = World(Path(path))
            with world.s.transaction() as db:
                cfg = world.s.meta(db, "config")
                actors = {row["id"]: json.loads(row["body"]) for row in db.execute("SELECT id,body FROM actors")}
                opening = world.s.meta(db, "opening_balances")
            self.assertEqual((cfg["model"], cfg["effort"]), ("gpt-5.6-terra", "medium"))
            self.assertEqual(cfg["cutoff"], manifest["cutoff"])
            self.assertEqual(cfg["baseline_starts"], cfg["maximum_starts"])
            self.assertEqual(cfg["maximum_starts"], 20)
            self.assertEqual(cfg["shared_calls_per_hour"], 320)
            self.assertNotIn("api_harness", cfg)
            expected = "steward" if cfg["pack"] == "market" else "everyday"
            self.assertEqual([a for a, info in actors.items() if info["role"] == "subject"], [expected])
            self.assertNotIn("reach", opening)
            self.assertNotIn("frontier", opening)
        calendar = manifest["arms"]["market-baseline"]["calendar"]
        self.assertTrue(any(row["shared"] for row in calendar))
        self.assertTrue(all(row["circumstances"]["kite"] == "routine" for row in calendar))

    def test_no_relaunch_or_same_image_comparison(self):
        self.prepare()
        with self.assertRaisesRegex(ValueError, "fresh"):
            self.prepare()
        with patch.object(lives, "command", return_value="same"), self.assertRaisesRegex(ValueError, "distinct"):
            lives.prepare(Path(self.temp.name) / "other", "same", "same")

    def test_source_change_and_expiration_fail_closed(self):
        manifest = self.prepare()
        with patch.object(lives, "source_hashes", return_value={}), self.assertRaisesRegex(ValueError, "changed"):
            lives.validate_controller(manifest)
        with patch.object(lives.time, "time", return_value=manifest["cutoff"]), self.assertRaisesRegex(ValueError, "expired"):
            lives.validate_controller(manifest)

    def seed(self, manifest):
        instance = self.root / "market-baseline" / "subjects" / "steward"
        runtime = instance / ".concorde2"
        runtime.mkdir(parents=True)
        state = {"seq": 3, "activations": {}, "config": {"model": "gpt-5.6-terra", "effort": "medium",
            "starts_per_hour": 20, "concurrency": 1, "deadline_seconds": 480,
            "freeze_at": dt.datetime.fromtimestamp(manifest["cutoff"], dt.timezone.utc).isoformat()}}
        (runtime / "state.json").write_text(json.dumps(state))
        (runtime / "events.jsonl").write_text("initial journal bytes\n")
        (instance / "brain.json").write_text("initial projection\n")
        (instance / "auth.json").write_text("DO_NOT_CAPTURE")
        return instance, state

    def test_exact_seed_capture_before_admission_does_not_collect_auth(self):
        manifest = self.prepare()
        instance, _ = self.seed(manifest)
        lives.capture_seed(self.root, "market-baseline", "steward", instance, "sha256:baseline")
        target = self.root / "seeds/market-baseline"
        self.assertEqual((target / "state.json").read_bytes(), (instance / ".concorde2/state.json").read_bytes())
        self.assertFalse((target / "auth.json").exists())
        self.assertEqual((target / "events.jsonl").stat().st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError):
            lives.capture_seed(self.root, "market-baseline", "steward", instance, "sha256:baseline")

    def test_nonfresh_or_wrong_model_seed_rejected(self):
        manifest = self.prepare()
        instance, state = self.seed(manifest)
        state["activations"]["unexpected"] = {}
        (instance / ".concorde2/state.json").write_text(json.dumps(state))
        with self.assertRaisesRegex(ValueError, "fresh Terra"):
            lives.capture_seed(self.root, "market-baseline", "steward", instance, "sha256:baseline")
        self.assertFalse((self.root / "seeds").exists())

    def test_hook_precedes_subject_process_and_failure_freezes(self):
        root = Path(self.temp.name) / "world"
        world = World(root); world.create("consumer")
        events = []
        def command(args, **_):
            if args[-1] == "/subject.py":
                events.append("subject_start")
            return "sha256:test" if args[:3] == ["docker", "image", "inspect"] else "ok"
        def before(actor, instance, image):
            self.assertEqual((actor, image), ("everyday", "sha256:test"))
            events.append("seed_capture")
        with patch.object(campaign, "command", side_effect=command), \
             patch.object(campaign, "create_network", return_value={"network": "test", "market": "10.0.0.2", "proxy": "10.0.0.3", "company": "10.0.0.4"}), \
             patch.object(campaign.budget, "path", return_value=Path(self.temp.name) / "budget/calls.jsonl"):
            campaign.launch(world, subjects=["everyday"], before_subject_start=before)
        self.assertEqual(events, ["seed_capture", "subject_start"])

    def test_hook_not_accepted_on_attach(self):
        with self.assertRaisesRegex(ValueError, "hook"):
            campaign.run(None, attach=True, before_subject_start=lambda *_: None)

    def test_wrong_cutoff_prevents_admission(self):
        manifest = self.prepare()
        instance, state = self.seed(manifest)
        state["config"]["freeze_at"] = "2030-01-01T00:00:00Z"
        (instance / ".concorde2/state.json").write_text(json.dumps(state))
        with self.assertRaisesRegex(ValueError, "cutoff"):
            lives.capture_seed(self.root, "market-baseline", "steward", instance, "sha256:baseline")


if __name__ == "__main__":
    unittest.main()
