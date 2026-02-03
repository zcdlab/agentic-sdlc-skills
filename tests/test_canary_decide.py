"""Tests for skills/4-ship-gradual-rollout/canary_decide.py (stdlib unittest; data generated in a temp dir)."""
import json
import random
import unittest
from datetime import datetime, timedelta, timezone

from helpers import TmpCase, run, script, write_json

SCRIPT = script("4-ship-gradual-rollout", "canary_decide.py")
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def gen(seed, version, n, minutes=120, success=0.95, lat=(1000, 300), tool_err=0.01, cost=0.02):
    """n requests for one version spread evenly over `minutes`."""
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        ts = T0 + timedelta(minutes=minutes * i / max(1, n - 1))
        calls = rng.randint(2, 6)
        rows.append({"version": version, "ts": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                     "success": rng.random() < success,
                     "latency_ms": max(1.0, rng.gauss(*lat)),
                     "tool_calls": calls,
                     "tool_errors": sum(rng.random() < tool_err for _ in range(calls)),
                     "cost_usd": max(0.001, rng.gauss(cost, cost / 5))})
    return rows


class CanaryCase(TmpCase):
    def go(self, canary_rows, control_rows, metrics, extra_policy=None, canary="2.0", control="1.0", raw_lines=()):
        req = self.tmp / "req.jsonl"
        rows = canary_rows + control_rows
        req.write_text("".join(json.dumps(r) + "\n" for r in rows) + "".join(l + "\n" for l in raw_lines))
        pol = write_json(self.tmp / "pol.json", {"min_requests": 100, "min_minutes": 30, **(extra_policy or {}), "metrics": metrics})
        return run(SCRIPT, req, "--policy", pol, "--canary", canary, "--control", control)

    def same(self, metrics, n=800, **kw):
        return self.go(gen(1, "2.0", n, **kw), gen(2, "1.0", n, **kw), metrics)


class Verdicts(CanaryCase):
    def test_worse_canary_rolls_back(self):
        p = self.go(gen(1, "2.0", 600, success=0.70), gen(2, "1.0", 600, success=0.95),
                    {"success": {"type": "rate", "field": "success", "min": 0.90, "max_drop": 0.02}})
        self.assertExit(p, 1)
        self.assertIn("VERDICT: ROLLBACK", p.stdout)
        self.assertIn("FAIL", p.stdout)

    def test_equal_canary_promotes(self):
        p = self.same({"success": {"type": "rate", "field": "success", "min": 0.80, "max_drop": 0.05}})
        self.assertExit(p, 0)
        self.assertIn("VERDICT: PROMOTE-NEXT-STAGE", p.stdout)
        self.assertNotIn("INCONCLUSIVE", p.stdout)

    def test_better_canary_promotes(self):
        p = self.go(gen(1, "2.0", 800, success=0.98), gen(2, "1.0", 800, success=0.93),
                    {"success": {"type": "rate", "field": "success", "min": 0.90, "max_drop": 0.03}})
        self.assertExit(p, 0)
        self.assertIn("PROMOTE-NEXT-STAGE", p.stdout)

    def test_too_few_requests_holds_even_if_good(self):
        p = self.go(gen(1, "2.0", 60, minutes=120), gen(2, "1.0", 800),
                    {"success": {"type": "rate", "field": "success", "min": 0.50}},
                    extra_policy={"min_requests": 400})
        self.assertExit(p, 3)
        self.assertIn("traffic below policy minimum", p.stdout)
        self.assertIn("VERDICT: HOLD", p.stdout)

    def test_too_few_minutes_holds_even_if_good(self):
        p = self.go(gen(1, "2.0", 800, minutes=10), gen(2, "1.0", 800, minutes=120),
                    {"success": {"type": "rate", "field": "success", "min": 0.50}},
                    extra_policy={"min_requests": 100, "min_minutes": 60})
        self.assertExit(p, 3)
        self.assertIn("VERDICT: HOLD", p.stdout)

    def test_low_traffic_hold_does_not_hide_fail(self):
        # FAIL outranks the traffic-driven INCONCLUSIVE.
        p = self.go(gen(1, "2.0", 60, success=0.2), gen(2, "1.0", 800),
                    {"success": {"type": "rate", "field": "success", "min": 0.90}},
                    extra_policy={"min_requests": 400})
        self.assertExit(p, 1)

    def test_borderline_is_inconclusive_hold(self):
        # Canary mean success ~0.90 against min 0.90: CI straddles the limit.
        p = self.go(gen(1, "2.0", 300, success=0.90), gen(2, "1.0", 300),
                    {"success": {"type": "rate", "field": "success", "min": 0.90}})
        self.assertExit(p, 3)
        self.assertIn("INCONCLUSIVE", p.stdout)

    def test_deterministic_output(self):
        metrics = {"p95_ms": {"type": "p95", "field": "latency_ms", "max_ratio": 1.5},
                   "cost": {"type": "mean", "field": "cost_usd", "max_ratio": 1.2}}
        a = self.same(metrics, n=300)
        b = self.same(metrics, n=300)
        self.assertEqual(a.returncode, b.returncode)
        self.assertEqual(a.stdout, b.stdout)
        self.assertIn("p95_ms", a.stdout)


class MetricTypes(CanaryCase):
    def test_rate_pass_and_fail(self):
        m = {"s": {"type": "rate", "field": "success", "min": 0.90}}
        self.assertExit(self.go(gen(1, "2.0", 800, success=0.99), gen(2, "1.0", 800), m), 0)
        self.assertExit(self.go(gen(1, "2.0", 800, success=0.70), gen(2, "1.0", 800), m), 1)

    def test_ratio_uses_num_over_den(self):
        m = {"tool_err": {"type": "ratio", "num": "tool_errors", "den": "tool_calls", "max": 0.05}}
        good = self.go(gen(1, "2.0", 800, tool_err=0.005), gen(2, "1.0", 800), m)
        self.assertExit(good, 0)
        bad = self.go(gen(1, "2.0", 800, tool_err=0.30), gen(2, "1.0", 800), m)
        self.assertExit(bad, 1)
        self.assertIn("tool_err", bad.stdout)

    def test_p95_max_absolute(self):
        m = {"p95": {"type": "p95", "field": "latency_ms", "max": 2000}}
        self.assertExit(self.go(gen(1, "2.0", 800, lat=(1000, 100)), gen(2, "1.0", 800), m), 0)
        p = self.go(gen(1, "2.0", 800, lat=(5000, 500)), gen(2, "1.0", 800), m)
        self.assertExit(p, 1)

    def test_mean_max_ratio(self):
        m = {"cost": {"type": "mean", "field": "cost_usd", "max_ratio": 1.15}}
        self.assertExit(self.go(gen(1, "2.0", 800, cost=0.02), gen(2, "1.0", 800, cost=0.02), m), 0)
        p = self.go(gen(1, "2.0", 800, cost=0.04), gen(2, "1.0", 800, cost=0.02), m)
        self.assertExit(p, 1)
        self.assertIn("ratio <= 1.15", p.stdout)


class LimitKinds(CanaryCase):
    def test_min(self):
        m = {"s": {"type": "rate", "field": "success", "min": 0.90}}
        p = self.go(gen(1, "2.0", 800, success=0.6), gen(2, "1.0", 800, success=0.6), m)
        self.assertExit(p, 1)
        self.assertIn(">= 90%", p.stdout)

    def test_max(self):
        m = {"e": {"type": "rate", "field": "success", "max": 0.50}}  # abuse: success rate must stay <= 50%
        p = self.go(gen(1, "2.0", 800, success=0.95), gen(2, "1.0", 800), m)
        self.assertExit(p, 1)
        self.assertIn("<= 50%", p.stdout)
        self.assertExit(self.go(gen(1, "2.0", 800, success=0.10), gen(2, "1.0", 800), m), 0)

    def test_max_drop(self):
        m = {"s": {"type": "rate", "field": "success", "max_drop": 0.05}}
        p = self.go(gen(1, "2.0", 800, success=0.70), gen(2, "1.0", 800, success=0.95), m)
        self.assertExit(p, 1)
        self.assertIn("drop <= 5%", p.stdout)
        self.assertExit(self.go(gen(1, "2.0", 800, success=0.95), gen(2, "1.0", 800, success=0.95), m), 0)

    def test_max_rise(self):
        m = {"esc": {"type": "rate", "field": "success", "max_rise": 0.05}}
        p = self.go(gen(1, "2.0", 800, success=0.95), gen(2, "1.0", 800, success=0.60), m)
        self.assertExit(p, 1)
        self.assertIn("rise <= 5%", p.stdout)
        self.assertExit(self.go(gen(1, "2.0", 800, success=0.80), gen(2, "1.0", 800, success=0.80), m), 0)

    def test_max_ratio_on_rate(self):
        m = {"e": {"type": "ratio", "num": "tool_errors", "den": "tool_calls", "max_ratio": 1.5}}
        p = self.go(gen(1, "2.0", 1500, tool_err=0.20), gen(2, "1.0", 1500, tool_err=0.05), m)
        self.assertExit(p, 1)
        self.assertIn("ratio <= 1.5", p.stdout)

    def test_max_ratio_on_p95_and_min_on_mean(self):
        m = {"p95": {"type": "p95", "field": "latency_ms", "max_ratio": 1.2},
             "cost": {"type": "mean", "field": "cost_usd", "min": 0.05}}
        p = self.go(gen(1, "2.0", 600, lat=(3000, 300)), gen(2, "1.0", 600, lat=(1000, 300)), m)
        self.assertExit(p, 1)
        self.assertIn(">= 0.05", p.stdout)


class Edges(CanaryCase):
    def test_unknown_canary_version_fails_clearly(self):
        p = self.go(gen(1, "2.0", 300), gen(2, "1.0", 300),
                    {"s": {"type": "rate", "field": "success", "min": 0.5}}, canary="9.9")
        self.assertExit(p, 2)  # never 1: exit 1 is wired to automatic rollback
        self.assertIn("no rows for one of the versions", p.stderr)
        self.assertNotIn("VERDICT", p.stdout)
        self.assertNotIn("Traceback", p.stderr)

    def test_unknown_control_version_fails_clearly(self):
        p = self.go(gen(1, "2.0", 300), gen(2, "1.0", 300),
                    {"s": {"type": "rate", "field": "success", "min": 0.5}}, control="0.0")
        self.assertExit(p, 2)
        self.assertIn("no rows for one of the versions", p.stderr)

    def test_malformed_lines_and_other_versions_ignored(self):
        other = gen(3, "0.9", 200, success=0.0)
        p = self.go(gen(1, "2.0", 800, success=0.99), gen(2, "1.0", 800) + other,
                    {"s": {"type": "rate", "field": "success", "min": 0.90}},
                    raw_lines=["not json {", ""])
        self.assertExit(p, 0)
        self.assertIn("canary 2.0: 800 requests", p.stdout)
        self.assertIn("control 1.0: 800 requests", p.stdout)

    def test_missing_policy_file_errors(self):
        req = self.tmp / "r.jsonl"
        req.write_text("")
        p = run(SCRIPT, req, "--policy", self.tmp / "nope.json", "--canary", "a", "--control", "b")
        self.assertExit(p, 2)

    def test_metric_field_missing_from_logs_is_input_error_not_rollback(self):
        for t in ("p95", "mean"):
            p = self.go(gen(1, "2.0", 300), gen(2, "1.0", 300),
                        {"lat": {"type": t, "field": "no_such_field", "max": 100}})
            self.assertExit(p, 2)
            self.assertIn("no_such_field", p.stderr)
            self.assertNotIn("Traceback", p.stderr)

    def test_help_documents_exit_codes(self):
        p = run(SCRIPT, "--help")
        self.assertExit(p, 0)
        self.assertIn("PROMOTE-NEXT-STAGE", p.stdout)


if __name__ == "__main__":
    unittest.main()
