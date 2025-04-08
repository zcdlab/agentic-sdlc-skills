"""Tests for skills/5-prod-set-up-alerts/alert_backtest.py (stdlib unittest; data generated in a temp dir)."""
import json
import random
import re
import unittest
from datetime import datetime, timedelta, timezone

from helpers import TmpCase, run, script, write_json, write_jsonl

SCRIPT = script("5-prod-set-up-alerts", "alert_backtest.py")
T0 = datetime(2026, 2, 1, tzinfo=timezone.utc)


def iso(minutes):
    return (T0 + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def gen(seed=1, minutes=720, step=1, rate=0.02, cost=0.01):
    """One request every `step` minutes, with a low base override rate."""
    rng = random.Random(seed)
    return [{"ts": iso(m), "human_override": rng.random() < rate,
             "cost_usd": round(rng.uniform(0.008, 0.012), 4), "tool_calls": rng.randint(0, 3)}
            for m in range(0, minutes, step)]


def mutate(rows, start, end, **fields):
    for r in rows:
        m = (datetime.fromisoformat(r["ts"].replace("Z", "+00:00")) - T0).total_seconds() / 60
        if start <= m < end:
            r.update(fields)
    return rows


def rate_rule(**kw):
    return {"name": "override", "type": "rate", "field": "human_override", "window_min": 60, "min_n": 20,
            "page_lb_above": 0.12, **kw}


class BacktestCase(TmpCase):
    def bt(self, rules, rows, incidents=None, cmd="backtest", *extra, step_min=5, confirm=2):
        cfg = rules if isinstance(rules, dict) else {"step_min": step_min, "confirm": confirm, "rules": rules}
        rp = write_json(self.tmp / "rules.json", cfg)
        lp = write_jsonl(self.tmp / "req.jsonl", rows)
        args = [cmd, rp, lp]
        if incidents is not None:
            args += ["--incidents", write_json(self.tmp / "inc.json",
                                               [{"start": iso(a), "end": iso(b), "name": n} for a, b, n in incidents])]
        return run(SCRIPT, *args, *extra)

    @staticmethod
    def minutes_to_page(p, name):
        m = re.search(rf"CAUGHT {name}: first page (\d+) min after start", p.stdout)
        assert m, p.stdout
        return int(m.group(1))


class Backtest(BacktestCase):
    def test_planted_jump_is_caught_with_time_to_page(self):
        rows = mutate(gen(), 300, 420, human_override=True)
        p = self.bt([rate_rule()], rows, [(300, 420, "spike")])
        self.assertExit(p, 0)
        self.assertIn("CAUGHT spike: first page", p.stdout)
        self.assertIn("min after start (override)", p.stdout)
        self.assertGreater(self.minutes_to_page(p, "spike"), 0)
        self.assertLess(self.minutes_to_page(p, "spike"), 60)
        self.assertIn("PAGE", p.stdout)
        self.assertIn("0 outside known incidents", p.stdout)

    def test_incident_without_signal_is_missed_exit_1(self):
        p = self.bt([rate_rule()], gen(), [(300, 420, "silent-bug")])
        self.assertExit(p, 1)
        self.assertIn("MISSED silent-bug: no page", p.stdout)

    def test_mixed_caught_and_missed_exit_1(self):
        rows = mutate(gen(), 100, 200, human_override=True)
        p = self.bt([rate_rule()], rows, [(100, 200, "real"), (500, 600, "ghost")])
        self.assertExit(p, 1)
        self.assertIn("CAUGHT real", p.stdout)
        self.assertIn("MISSED ghost", p.stdout)

    def test_false_pages_per_week_counted(self):
        rows = mutate(gen(), 100, 200, human_override=True)
        rows = mutate(rows, 500, 600, human_override=True)  # a second spike that is not a known incident
        p = self.bt([rate_rule()], rows, [(100, 200, "real")])
        self.assertExit(p, 0)
        m = re.search(r"(\d+) pages over ([\d.]+) days; (\d+) outside known incidents = ([\d.]+) false pages/week", p.stdout)
        self.assertIsNotNone(m, p.stdout)
        self.assertEqual(m.group(3), "1")
        days = float(m.group(2))
        self.assertAlmostEqual(float(m.group(4)), 1 / days * 7, delta=0.1)

    def test_no_incidents_flag_counts_all_pages(self):
        rows = mutate(gen(), 100, 200, human_override=True)
        p = self.bt([rate_rule()], rows)
        self.assertExit(p, 0)
        self.assertIn("(no --incidents: all pages counted)", p.stdout)
        self.assertIn("1 outside known incidents", p.stdout)

    def test_quiet_log_no_pages(self):
        p = self.bt([rate_rule()], gen())
        self.assertExit(p, 0)
        self.assertIn("0 pages over", p.stdout)
        self.assertIn("0.0 false pages/week", p.stdout)

    def test_page_within_slack_after_incident_is_not_false(self):
        # Window rule keeps paging up to ~60 min after the incident ends; that lag is covered by the 60-min slack.
        rows = mutate(gen(), 300, 330, human_override=True)
        p = self.bt([rate_rule(window_min=30, min_n=10)], rows, [(300, 330, "short")])
        self.assertExit(p, 0)
        self.assertIn("0 outside known incidents", p.stdout)


class Confirm(BacktestCase):
    def blip_rows(self):
        rows = gen(rate=0.0)
        for r in rows:
            r["tool_calls"] = 1
        # one request at an off-grid time (62.5 min) -> exactly one 5-min check sees it
        rows.append({"ts": (T0 + timedelta(minutes=62, seconds=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                     "human_override": False, "cost_usd": 0.01, "tool_calls": 9})
        return rows

    def rule(self):
        return {"name": "loops", "type": "count", "where": {"tool_calls": {"ge": 8}}, "window_min": 5, "page_ge": 1}

    def test_confirm_one_pages_on_single_breach(self):
        p = self.bt([self.rule()], self.blip_rows(), confirm=1)
        self.assertIn("1 pages over", p.stdout)
        self.assertIn("1 matching requests in 5m", p.stdout)

    def test_confirm_two_requires_consecutive_breaches(self):
        p = self.bt([self.rule()], self.blip_rows(), confirm=2)
        self.assertIn("0 pages over", p.stdout)

    def test_confirm_two_pages_when_breach_is_sustained(self):
        rows = self.blip_rows()
        mutate(rows, 60, 80, tool_calls=9)
        p = self.bt([self.rule()], rows, confirm=2)
        self.assertIn("1 pages over", p.stdout)

    def test_confirm_delays_page_by_one_step(self):
        rows = mutate(gen(rate=0.0), 300, 420, human_override=True)
        t1 = self.minutes_to_page(self.bt([rate_rule()], rows, [(300, 420, "x")], confirm=1), "x")
        t3 = self.minutes_to_page(self.bt([rate_rule()], rows, [(300, 420, "x")], confirm=3), "x")
        self.assertEqual(t3 - t1, 10)  # two extra 5-minute steps


class RuleSettings(BacktestCase):
    def sparse_all_true(self):
        return [{"ts": iso(m), "human_override": True, "cost_usd": 0.01, "tool_calls": 0} for m in range(0, 300, 5)]

    def test_min_n_suppresses_tiny_windows(self):
        rows = self.sparse_all_true()  # 12 rows per 60 min window, all true
        self.assertIn("0 pages over", self.bt([rate_rule(min_n=20)], rows).stdout)
        self.assertNotIn("0 pages over", self.bt([rate_rule(min_n=5)], rows).stdout)

    def test_lag_min_shifts_window(self):
        rows = mutate(gen(rate=0.0), 300, 480, human_override=True)
        inc = [(300, 480, "x")]
        t0 = self.minutes_to_page(self.bt([rate_rule(window_min=30, min_n=10)], rows, inc), "x")
        t90 = self.minutes_to_page(self.bt([rate_rule(window_min=30, min_n=10, lag_min=90)], rows, inc), "x")
        self.assertGreaterEqual(t90 - t0, 85)

    def test_rate_uses_wilson_lower_bound_not_point_estimate(self):
        # 3/10 = 30% point estimate, but its Wilson lower bound (~11%) is under the 0.15 limit -> no page.
        rows = [{"ts": iso(m), "human_override": (m // 5) < 3} for m in range(0, 50, 5)]  # 10 rows, 3 true
        small = self.bt([rate_rule(window_min=60, min_n=10, page_lb_above=0.15)], rows, confirm=1)
        self.assertIn("0 pages over", small.stdout)
        # Same 30% rate over 100 rows: the lower bound (~22%) clears 0.15 -> page. Sample size matters.
        rows2 = [{"ts": iso(m), "human_override": (m // 5) % 10 < 3} for m in range(0, 500, 5)]
        big = self.bt([rate_rule(window_min=600, min_n=50, page_lb_above=0.15)], rows2, confirm=1)
        self.assertNotIn("0 pages over", big.stdout)
        self.assertIn("lower bound", big.stdout)

    def test_mean_rule(self):
        rows = mutate(gen(), 300, 420, cost_usd=0.05)
        rule = {"name": "cost", "type": "mean", "field": "cost_usd", "window_min": 30, "min_n": 10, "page_above": 0.022}
        p = self.bt([rule], rows, [(300, 420, "pricey")])
        self.assertExit(p, 0)
        self.assertIn("CAUGHT pricey", p.stdout)
        self.assertIn("mean ", p.stdout)
        self.assertExit(self.bt([rule], gen(), [(300, 420, "pricey")]), 1)

    def test_count_rule_with_where(self):
        rows = mutate(gen(), 300, 400, tool_calls=9)
        rule = {"name": "loops", "type": "count", "where": {"tool_calls": {"ge": 8}}, "window_min": 30, "page_ge": 5}
        p = self.bt([rule], rows, [(300, 400, "loops")])
        self.assertExit(p, 0)
        self.assertIn("matching requests in 30m", p.stdout)
        self.assertExit(self.bt([rule], gen(), [(300, 400, "loops")]), 1)

    def test_count_where_ignores_rows_missing_field(self):
        rows = [{"ts": iso(m), "tool_calls": None} for m in range(0, 200)]
        rows.append({"ts": iso(200), "other": 1})
        rule = {"name": "loops", "type": "count", "where": {"tool_calls": {"ge": 0}}, "window_min": 30, "page_ge": 1}
        self.assertIn("0 pages over", self.bt([rule], rows).stdout)

    def test_silence_rule(self):
        rows = [r for r in gen() if not 300 <= int(r["ts"][11:13]) * 60 + int(r["ts"][14:16]) < 420]
        rule = {"name": "silence", "type": "silence", "page_gap_min": 60}
        p = self.bt([rule], rows, [(300, 420, "outage")])
        self.assertExit(p, 0)
        self.assertIn("CAUGHT outage", p.stdout)
        self.assertIn("no requests for", p.stdout)
        self.assertExit(self.bt([rule], gen(), [(300, 420, "outage")]), 1)

    def test_freshness_rule_field_goes_null(self):
        rows = mutate(gen(), 300, 420, human_override=None)
        rule = {"name": "labels", "type": "freshness", "field": "human_override", "page_gap_min": 60}
        p = self.bt([rule], rows, [(300, 420, "dead-feed")])
        self.assertExit(p, 0)
        self.assertIn("CAUGHT dead-feed", p.stdout)
        self.assertIn("no human_override values for", p.stdout)
        # the silence rule must NOT fire here: requests keep flowing
        sil = {"name": "silence", "type": "silence", "page_gap_min": 60}
        self.assertExit(self.bt([sil], rows, [(300, 420, "dead-feed")]), 1)

    def test_ticket_severity_never_counts_as_page(self):
        rows = mutate(gen(), 300, 420, human_override=True)
        p = self.bt([rate_rule(severity="ticket")], rows, [(300, 420, "spike")])
        self.assertExit(p, 1)
        self.assertIn("TICKET", p.stdout)
        self.assertIn("MISSED spike", p.stdout)
        self.assertIn("0 pages over", p.stdout)

    def test_ticket_does_not_count_as_false_page(self):
        rows = mutate(gen(), 300, 420, human_override=True)
        p = self.bt([rate_rule(severity="ticket")], rows)
        self.assertExit(p, 0)
        self.assertIn("0 pages over", p.stdout)

    def test_unknown_rule_type_errors(self):
        p = self.bt([{"name": "w", "type": "wat", "field": "human_override", "window_min": 10}], gen(minutes=60))
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("unknown rule type wat", p.stderr)


class Inject(BacktestCase):
    def inj(self, rows, *args, rules=None, incidents=None, trials=8, minutes=90):
        return self.bt(rules or [rate_rule()], rows, incidents, "inject", *args, "--trials", str(trials),
                       "--minutes", str(minutes))

    def test_large_change_detected_exit_0(self):
        p = self.inj(gen(minutes=900), "--change", "human_override=rate:+0.4")
        self.assertExit(p, 0)
        m = re.search(r"detected (\d+)/(\d+) = (\d+)%", p.stdout)
        self.assertIsNotNone(m, p.stdout)
        self.assertGreaterEqual(int(m.group(3)), 80)
        self.assertIn("time to page median", p.stdout)
        self.assertIn("injected human_override=rate:+0.4 for 90 min, 8 random windows", p.stdout)

    def test_tiny_change_not_detected_exit_1(self):
        p = self.inj(gen(minutes=900), "--change", "human_override=rate:+0.002")
        self.assertExit(p, 1)
        self.assertIn("detected 0/8 = 0%", p.stdout)
        self.assertIn("missed window starting", p.stdout)

    def test_multiplier_change_on_mean_rule(self):
        rule = {"name": "cost", "type": "mean", "field": "cost_usd", "window_min": 30, "min_n": 10, "page_above": 0.022}
        p = self.inj(gen(minutes=900), "--change", "cost_usd=x3", rules=[rule])
        self.assertExit(p, 0)
        self.assertIn("detected 8/8", p.stdout)

    def test_windows_avoid_known_incidents(self):
        rows = gen(minutes=720)
        # incident at 240-480 min; windows must start outside (240-90-120, 480+120) = (30, 600) minutes
        p = self.inj(rows, "--change", "human_override=rate:+0.001", incidents=[(240, 480, "known")], trials=6, minutes=90)
        starts = re.findall(r"missed window starting (\S+) \(hour", p.stdout)
        self.assertEqual(len(starts), 5)  # output caps missed list at 5
        for s in starts:
            mins = (datetime.strptime(s, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc) - T0).total_seconds() / 60
            self.assertTrue(mins <= 30 or mins >= 600, f"window at {mins} min overlaps incident exclusion zone")

    def test_incident_in_log_gets_no_credit(self):
        # A real incident exists; injection with a tiny change must still not report detections from it.
        rows = mutate(gen(minutes=900), 400, 520, human_override=True)
        p = self.inj(rows, "--change", "human_override=rate:+0.001", incidents=[(400, 520, "known")])
        self.assertExit(p, 1)
        self.assertIn("detected 0/8", p.stdout)

    def test_same_seed_is_deterministic(self):
        rows = gen(minutes=900)
        a = self.inj(rows, "--change", "human_override=rate:+0.3", "--seed", "7")
        b = self.inj(rows, "--change", "human_override=rate:+0.3", "--seed", "7")
        self.assertEqual(a.stdout, b.stdout)

    def test_inject_without_change_errors(self):
        p = self.inj(gen(minutes=300))
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("inject needs at least one --change", p.stderr)


class MissingField(BacktestCase):
    RULE = {"name": "ghost", "type": "rate", "field": "no_such_field", "window_min": 60, "page_lb_above": 0.1}

    def test_backtest_warns_once(self):
        p = self.bt([self.RULE], gen(minutes=300))
        self.assertEqual(p.stderr.count("WARN rule ghost: field 'no_such_field' is in no log row"), 1, p.stderr)
        self.assertIn("0 pages over", p.stdout)

    def test_inject_warns_exactly_once_across_trials(self):
        # regression: episodes() runs once for the baseline and once per trial; the WARN must not repeat.
        p = self.bt([self.RULE, rate_rule()], gen(minutes=600), None, "inject",
                    "--change", "human_override=rate:+0.4", "--trials", "6", "--minutes", "90")
        self.assertExit(p, 0)
        self.assertEqual(p.stderr.count("WARN"), 1, p.stderr)
        self.assertIn("no_such_field", p.stderr)

    def test_count_where_field_missing_warns(self):
        rule = {"name": "loops", "type": "count", "where": {"nope": {"ge": 1}}, "window_min": 10, "page_ge": 1}
        p = self.bt([rule], gen(minutes=120))
        self.assertEqual(p.stderr.count("field 'nope'"), 1)

    def test_present_field_does_not_warn(self):
        self.assertEqual(self.bt([rate_rule()], gen(minutes=300)).stderr, "")


if __name__ == "__main__":
    unittest.main()
