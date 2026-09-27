import unittest

from evals.interrupted_usage import phase_floor


class PartialUsageTests(unittest.TestCase):
    def event(self, at, count):
        return {"timestamp": f"2026-09-11T00:00:{at:02d}Z", "payload": {"type": "token_count",
            "info": {"total_token_usage": {"input_tokens": count, "cached_input_tokens": count // 2, "output_tokens": 3}}}}

    def test_phase_floor_excludes_later_reset_and_uses_last_not_sum(self):
        from evals.interrupted_usage import seconds
        events = [self.event(1, 100), self.event(2, 120), self.event(2, 120), self.event(3, 10)]
        result = phase_floor(events, seconds("2026-09-11T00:00:01Z"), seconds("2026-09-11T00:00:03Z"))
        self.assertEqual(result["last"]["usage"]["input"], 120)
        self.assertEqual(result["observations"], 3)

    def test_no_guess_when_counters_reset_or_are_invalid(self):
        from evals.interrupted_usage import seconds
        lo, hi = seconds("2026-09-11T00:00:00Z"), seconds("2026-09-11T00:00:10Z")
        for events in ([], [self.event(1, 100), self.event(2, 10)], [self.event(1, -1)]):
            with self.assertRaises(ValueError): phase_floor(events, lo, hi)


if __name__ == "__main__": unittest.main()
