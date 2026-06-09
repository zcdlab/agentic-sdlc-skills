"""Tests for skills/6-fix-roll-back-agent/onset.py (stdlib unittest; data generated in a temp dir)."""
import random
import re
import unittest
from datetime import datetime, timedelta, timezone

from helpers import TmpCase, run, script, write_jsonl

SCRIPT = script("6-fix-roll-back-agent", "onset.py")
T0 = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)


def iso(minutes):
    return (T0 + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def series(seed=1, n=24, step_at=12, lo=0.70, hi=0.95, noise=0.005, requests=None, versions=None):
    """n 10-minute buckets; level `hi` before bucket `step_at`, `lo` from it on."""
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        v = (hi if i < step_at else lo) + rng.uniform(-noise, noise)
        r = {"ts": iso(10 * i), "success_rate": round(v, 4)}
        if requests:
            r["requests"] = requests[i]
        if versions:
            r["version"] = versions[0] if i < step_at else versions[1]
        rows.append(r)
    return rows


class OnsetCase(TmpCase):
    def metrics(self, rows):
        return write_jsonl(self.tmp / "m.jsonl", rows)

    def events(self, lines, name="events.log"):
        p = self.tmp / name
        p.write_text("".join(l + "\n" for l in lines))
        return str(p)

    def onset(self, rows, *args, events=None, **kw):
        extra = ["--events", *events] if events else []
        return run(SCRIPT, self.metrics(rows), "--metric", "success_rate", *extra, *args, **kw)


class StepChange(OnsetCase):
    def test_step_located_with_levels(self):
        p = self.onset(series())
        self.assertExit(p, 0)
        m = re.search(r"success_rate: ([\d.]+) before -> ([\d.]+) after", p.stdout)
        self.assertIsNotNone(m, p.stdout)
        self.assertAlmostEqual(float(m.group(1)), 0.95, delta=0.01)
        self.assertAlmostEqual(float(m.group(2)), 0.70, delta=0.01)
        self.assertIn(f"last good bucket {iso(110)}", p.stdout)
        self.assertIn(f"first bad bucket {iso(120)}", p.stdout)
        self.assertIn("(12/24 buckets before)", p.stdout)
        self.assertNotIn("NO CLEAR STEP", p.stdout)

    def test_step_at_other_position(self):
        p = self.onset(series(step_at=7))
        self.assertIn(f"first bad bucket {iso(70)}", p.stdout)
        self.assertIn("(7/24 buckets before)", p.stdout)

    def test_unsorted_metrics_are_sorted(self):
        rows = series()
        random.Random(5).shuffle(rows)
        p = self.onset(rows)
        self.assertIn(f"first bad bucket {iso(120)}", p.stdout)

    def test_flat_noisy_metric_has_no_clear_step(self):
        rng = random.Random(3)
        rows = [{"ts": iso(10 * i), "success_rate": round(0.9 + rng.uniform(-0.05, 0.05), 4)} for i in range(30)]
        p = self.onset(rows)
        self.assertExit(p, 0)
        self.assertIn("NO CLEAR STEP", p.stdout)

    def test_fewer_than_four_buckets_exits_nonzero(self):
        p = self.onset(series(n=3, step_at=2))
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("need at least 4 buckets", p.stderr)

    def test_rows_without_metric_do_not_count(self):
        rows = series(n=6, step_at=3) + [{"ts": iso(999), "other": 1}] * 5
        p = self.onset(rows)
        self.assertExit(p, 0)
        self.assertIn("/6 buckets", p.stdout)

    def test_weight_is_respected(self):
        # One low outlier bucket among 0.90s: giving it huge weight changes the fitted split.
        lvl2 = [0.90] * 9 + [0.30] + [0.90] * 10
        rows2 = [{"ts": iso(10 * i), "success_rate": v, "requests": 1} for i, v in enumerate(lvl2)]
        w_rows = [dict(r, requests=(10000 if i == 9 else 1)) for i, r in enumerate(rows2)]
        unw = self.onset(rows2)
        wtd = self.onset(w_rows, "--weight", "requests")
        self.assertExit(unw, 0)
        self.assertExit(wtd, 0)
        self.assertNotEqual(unw.stdout.splitlines()[0], wtd.stdout.splitlines()[0])
        # ignoring --weight on the weighted file must equal the unweighted result
        self.assertEqual(self.onset(w_rows).stdout, unw.stdout)

    def test_versions_in_metrics_are_reported(self):
        p = self.onset(series(versions=("v1", "v2")))
        self.assertIn("before ['v1'], after ['v2']", p.stdout)


class Events(OnsetCase):
    def test_suspects_in_window_and_before_listed(self):
        ev = self.events([
            f"{iso(-300)} deploy v0.9 old release",
            f"{iso(60)} config flip prompt_cache=on",
            f"{iso(115)} env change MODEL_TEMP=1.0",
            f"{iso(118)} deploy v2.0",
            f"{iso(200)} deploy v2.1 hotfix",
            "free text with no timestamp",
        ])
        p = self.onset(series(), events=[ev])
        self.assertExit(p, 0)
        out = p.stdout
        sus = out.split("suspects (between last good and first bad bucket):")[1].split("before the window")[0]
        self.assertIn("env change MODEL_TEMP=1.0", sus)
        self.assertIn("deploy v2.0", sus)
        self.assertNotIn("prompt_cache", sus)
        self.assertNotIn("v2.1", sus)
        before = out.split("before the window (metric was still fine after these):")[1].split("after the window")[0]
        self.assertIn("config flip prompt_cache=on", before)
        self.assertIn("deploy v0.9", before)
        self.assertIn(f"{ev}:3", sus)  # path:line reference
        after = out.split("after the window:")[1]
        self.assertIn("deploy v2.1 hotfix", after)

    def test_event_on_first_bad_bucket_is_suspect_but_on_good_is_before(self):
        ev = self.events([f"{iso(110)} at-good-bucket", f"{iso(120)} at-bad-bucket"])
        out = self.onset(series(), events=[ev]).stdout
        sus = out.split("suspects (between last good and first bad bucket):")[1].split("before the window")[0]
        self.assertIn("at-bad-bucket", sus)
        self.assertNotIn("at-good-bucket", sus)

    def test_no_suspect_in_window_prints_none_message(self):
        ev = self.events([f"{iso(30)} deploy v1.0", f"{iso(300)} deploy v3.0"])
        p = self.onset(series(), events=[ev])
        self.assertExit(p, 0)
        self.assertIn("suspects: NONE logged in that window", p.stdout)
        self.assertIn("unlogged change", p.stdout)

    def test_out_of_order_line_warns(self):
        ev = self.events([f"{iso(50)} first", f"{iso(40)} second goes back in time", f"{iso(115)} third"])
        p = self.onset(series(), events=[ev])
        self.assertExit(p, 0)
        self.assertIn(f"WARN {ev}:2 is earlier than the line above it", p.stdout)
        self.assertEqual(p.stdout.count("WARN"), 1)

    def test_in_order_log_has_no_warn(self):
        ev = self.events([f"{iso(40)} a", f"{iso(50)} b"])
        self.assertNotIn("WARN", self.onset(series(), events=[ev]).stdout)

    def test_multiple_event_files_merged_in_time_order(self):
        e1 = self.events([f"{iso(114)} from-first-file"], "a.log")
        e2 = self.events([f"{iso(112)} from-second-file"], "b.log")
        out = self.onset(series(), events=[e1, e2]).stdout
        self.assertLess(out.index("from-second-file"), out.index("from-first-file"))

    def test_no_events_file_prints_no_suspect_section(self):
        out = self.onset(series()).stdout
        self.assertNotIn("suspects", out)

    def test_timestamps_with_offsets_and_seconds(self):
        ev = self.events(["2026-03-01T14:55:30+00:00 offset-event"])  # iso(175) -> after window
        p = self.onset(series(), events=[ev])
        self.assertExit(p, 0)
        self.assertIn("offset-event", p.stdout.split("after the window:")[1])


class Target(OnsetCase):
    def deploys(self):
        return self.events([
            f"{iso(0)} deploy v1.3.0",
            f"{iso(40)} config tweak A",
            f"{iso(80)} deploy v1.4.0",
            f"{iso(100)} env change B",
            f"{iso(118)} deploy v1.5.0",
            f"{iso(150)} upstream change C",
        ])

    def test_target_from_deploy_lines(self):
        p = self.onset(series(), "--target", "v1.4.0", events=[self.deploys()])
        self.assertExit(p, 0)
        t = p.stdout.split("target v1.4.0:")[1]
        self.assertIn(f"last deployed {iso(80)}", t)
        self.assertIn("2 non-deploy change(s) since", t)
        self.assertIn("env change B", t)
        self.assertIn("upstream change C", t)
        self.assertNotIn("config tweak A", t)
        self.assertNotIn("deploy v1.5.0", t)

    def test_target_uses_last_of_repeated_deploys(self):
        ev = self.events([f"{iso(0)} deploy v1.3.0", f"{iso(30)} config X", f"{iso(60)} deploy v1.3.0", f"{iso(90)} config Y"])
        t = self.onset(series(), "--target", "v1.3.0", events=[ev]).stdout.split("target v1.3.0:")[1]
        self.assertIn(f"last deployed {iso(60)}", t)
        self.assertIn("1 non-deploy change(s)", t)
        self.assertIn("config Y", t)
        self.assertNotIn("config X", t)

    def test_target_falls_back_to_metrics_version_without_v(self):
        rows = series(versions=("1.3.0", "1.4.0"))  # metrics lack the leading "v"; last 1.3.0 bucket = iso(110)
        ev = self.events([f"{iso(50)} config before", f"{iso(115)} env after"])
        p = self.onset(rows, "--target", "v1.3.0", events=[ev])
        self.assertExit(p, 0)
        t = p.stdout.split("target v1.3.0:")[1]
        self.assertIn(f"last live in metrics at {iso(110)}", t)
        self.assertIn("env after", t)
        self.assertNotIn("config before", t)

    def test_target_falls_back_to_metrics_version_with_v(self):
        rows = series(versions=("v1.3.0", "v1.4.0"))
        ev = self.events([f"{iso(115)} env after"])
        t = self.onset(rows, "--target", "v1.3.0", events=[ev]).stdout.split("target v1.3.0:")[1]
        self.assertIn(f"last live in metrics at {iso(110)}", t)
        self.assertIn("1 non-deploy change(s)", t)

    def test_target_bare_arg_matches_v_prefixed_metrics(self):
        # --target 1.3.0 also matches metrics version "1.3.0"; metrics beat deploy lines when present.
        rows = series(versions=("1.3.0", "1.4.0"))
        ev = self.events([f"{iso(0)} deploy 1.3.0", f"{iso(115)} env after"])
        t = self.onset(rows, "--target", "1.3.0", events=[ev]).stdout.split("target 1.3.0:")[1]
        self.assertIn("last live in metrics at", t)

    def test_custom_deploy_pattern(self):
        ev = self.events([f"{iso(20)} rollout of build=abc123 done", f"{iso(60)} config Z"])
        p = self.onset(series(), "--target", "abc123", "--deploy-pattern", r"build=(\S+)", events=[ev])
        t = p.stdout.split("target abc123:")[1]
        self.assertIn(f"last deployed {iso(20)}", t)

    def test_unknown_target_message(self):
        ev = self.events([f"{iso(20)} deploy v1.0"])
        p = self.onset(series(), "--target", "v9.9", events=[ev])
        self.assertExit(p, 0)
        self.assertIn("target v9.9: no deploy or metrics of it", p.stdout)


if __name__ == "__main__":
    unittest.main()
