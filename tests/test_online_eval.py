"""Tests for skills/5-prod-measure-quality/online_eval.py (sample, calibrate, report). Data is generated in-test."""
import json
import random
import unittest
from pathlib import Path

from helpers import TmpCase, run, script, write_json, write_jsonl

PATH = script("5-prod-measure-quality", "online_eval.py")


def make_requests(n=600, seed=1):
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        rows.append({"request_id": f"r{i:04d}", "ts": f"2026-03-0{rng.randint(1, 5)}T{rng.randint(0, 23):02d}:00:00Z",
                     "predicted": rng.choice(["legal", "billing", "other"]),
                     "ticket": rng.choice(["please help", "my lawyer says", "refund me", "hello"]),
                     "version": rng.choice(["v1", "v2"])})
    return rows


REST = [{"name": "rest", "rate": 0.2}]


class SampleTests(TmpCase):
    def sample(self, rows, strata, *extra):
        req = write_jsonl(self.tmp / "req.jsonl", rows)
        st = write_json(self.tmp / "strata.json", strata)
        p = run(PATH, "sample", req, "--strata", st, *extra)
        self.assertExit(p, 0)
        return p, [json.loads(l) for l in p.stdout.splitlines() if l.strip()]

    def test_deterministic_same_ids_across_runs(self):
        rows = make_requests()
        p1, s1 = self.sample(rows, REST)
        p2, s2 = self.sample(rows, REST)
        self.assertEqual(p1.stdout, p2.stdout)
        self.assertGreater(len(s1), 60)
        self.assertLess(len(s1), 180)  # ~20% of 600
        self.assertIn("sampled per stratum: rest=", p1.stderr)

    def test_different_salt_changes_sample(self):
        rows = make_requests()
        _, a = self.sample(rows, REST, "--salt", "alpha")
        _, b = self.sample(rows, REST, "--salt", "beta")
        ia, ib = {r["request_id"] for r in a}, {r["request_id"] for r in b}
        self.assertNotEqual(ia, ib)

    def test_sample_is_independent_of_row_order(self):
        rows = make_requests()
        _, a = self.sample(rows, REST)
        random.Random(9).shuffle(rows)
        _, b = self.sample(rows, REST)
        self.assertEqual({r["request_id"] for r in a}, {r["request_id"] for r in b})

    def test_first_matching_stratum_wins(self):
        rows = [{"request_id": f"x{i}", "ts": "2026-03-01T00:00:00Z", "predicted": "legal", "ticket": "my lawyer"}
                for i in range(20)]
        strata = [{"name": "legal_pred", "where": {"predicted": "legal"}, "rate": 1.0},
                  {"name": "legal_words", "regex": {"ticket": "(?i)lawyer"}, "rate": 0.5},
                  {"name": "rest", "rate": 0.1}]
        _, out = self.sample(rows, strata)
        self.assertEqual(len(out), 20)
        self.assertEqual({r["_stratum"] for r in out}, {"legal_pred"})
        self.assertEqual({r["_weight"] for r in out}, {1.0})
        # swap order: now the regex stratum (rate 0.5) claims the same rows
        _, out2 = self.sample(rows, list(reversed(strata[:2])))
        self.assertEqual({r["_stratum"] for r in out2}, {"legal_words"})
        self.assertLess(len(out2), 20)

    def test_row_matching_no_stratum_is_dropped(self):
        rows = [{"request_id": "a", "predicted": "x"}, {"request_id": "b", "predicted": "legal"}]
        _, out = self.sample(rows, [{"name": "l", "where": {"predicted": "legal"}, "rate": 1.0}])
        self.assertEqual([r["request_id"] for r in out], ["b"])

    def test_weight_is_inverse_rate_and_day_present(self):
        rows = make_requests(300)
        strata = [{"name": "legal", "where": {"predicted": "legal"}, "rate": 1.0},
                  {"name": "rest", "rate": 0.25}]
        _, out = self.sample(rows, strata)
        self.assertTrue(out)
        by_id = {r["request_id"]: r for r in rows}
        for r in out:
            self.assertAlmostEqual(r["_weight"], 1.0 if r["_stratum"] == "legal" else 4.0)
            self.assertEqual(r["_day"], by_id[r["request_id"]]["ts"][:10])
        self.assertEqual({r["_stratum"] for r in out}, {"legal", "rest"})

    def test_no_day_when_ts_missing_or_not_iso(self):
        rows = [{"request_id": "a"}, {"request_id": "b", "ts": "yesterday"}]
        _, out = self.sample(rows, [{"name": "all", "rate": 1.0}])
        self.assertEqual(len(out), 2)
        self.assertTrue(all("_day" not in r for r in out))

    def test_custom_ts_and_id_fields(self):
        rows = [{"rid": "a", "when": "2026-01-02 03:04:05"}]
        _, out = self.sample(rows, [{"name": "all", "rate": 1.0}], "--id", "rid", "--ts", "when")
        self.assertEqual(out[0]["_day"], "2026-01-02")

    def test_pii_redacts_email_and_phone(self):
        rows = [{"request_id": "a", "ts": "2026-03-04T12:34:56Z",
                 "ticket": "mail bob.smith+x@example.co.uk or call +1 (415) 555-0134, acct 1234567890"}]
        _, out = self.sample(rows, [{"name": "all", "rate": 1.0}])
        t = out[0]["ticket"]
        self.assertNotIn("bob.smith", t)
        self.assertNotIn("555-0134", t)
        self.assertNotIn("1234567890", t)
        self.assertIn("<EMAIL>", t)
        self.assertIn("<PHONE>", t)

    def test_pii_leaves_iso_timestamps_intact(self):
        stamps = ["2026-03-04T12:34:56Z", "2026-03-04T12:34:56.789+01:00", "2026-03-04 12:34", "2026-03-04"]
        rows = [{"request_id": f"a{i}", "ts": s, "created": s, "ticket": "x"} for i, s in enumerate(stamps)]
        _, out = self.sample(rows, [{"name": "all", "rate": 1.0}])
        for r, s in zip(out, stamps):
            self.assertEqual(r["ts"], s)
            self.assertEqual(r["created"], s)
            self.assertEqual(r["_day"], "2026-03-04")

    def test_pii_does_not_touch_request_id(self):
        rows = [{"request_id": "123456789012", "ticket": "ref 123456789012"}]
        _, out = self.sample(rows, [{"name": "all", "rate": 1.0}])
        self.assertEqual(out[0]["request_id"], "123456789012")
        self.assertNotIn("123456789012", out[0]["ticket"])

    def test_pii_keeps_iso_timestamp_embedded_in_free_text(self):
        # regression: the phone regex used to eat timestamps inside free text
        rows = [{"request_id": "a", "ticket": "failed at 2026-03-04T12:34:56Z, call +1 415 555 0100 to retry"}]
        _, out = self.sample(rows, [{"name": "all", "rate": 1.0}])
        self.assertIn("2026-03-04T12:34:56Z", out[0]["ticket"])
        self.assertIn("<PHONE>", out[0]["ticket"])
        self.assertNotIn("555", out[0]["ticket"])

    def test_missing_strata_arg_errors(self):
        req = write_jsonl(self.tmp / "req.jsonl", [{"request_id": "a"}])
        self.assertNotEqual(run(PATH, "sample", req).returncode, 0)

    def test_help_documents_subcommands(self):
        p = run(PATH, "--help")
        self.assertExit(p, 0)
        for s in ("sample", "calibrate", "report", "first matching stratum wins"):
            self.assertIn(s, p.stdout)


def make_calibration_inputs(tmp, spec):
    """spec: list of (version, n_correct_accepted, n_correct_rejected, n_err_rejected, n_err_accepted).
    Returns paths (labels, judge, requests) and the ids of accepted errors."""
    reqs, labels, judge, accepted = [], [], [], []
    k = 0

    def add(ver, truth, accepts):
        nonlocal k
        rid = f"{ver}-{k:03d}"
        k += 1
        reqs.append({"request_id": rid, "version": ver, "predicted": "A"})
        labels.append({"request_id": rid, "gold": "A" if truth else "B"})
        judge.append({"request_id": rid, "verdict": "correct" if accepts else "incorrect"})
        if not truth and accepts:
            accepted.append(rid)

    for ver, ca, cr, er, ea in spec:
        for _ in range(ca):
            add(ver, True, True)
        for _ in range(cr):
            add(ver, True, False)
        for _ in range(er):
            add(ver, False, False)
        for _ in range(ea):
            add(ver, False, True)
    random.Random(3).shuffle(labels)
    return (write_jsonl(tmp / "labels.jsonl", labels), write_jsonl(tmp / "judge.jsonl", judge),
            write_jsonl(tmp / "req.jsonl", reqs), accepted)


class CalibrateTests(TmpCase):
    def calibrate(self, spec, *extra):
        lab, jud, req, accepted = make_calibration_inputs(self.tmp, spec)
        p = run(PATH, "calibrate", "--labels", lab, "--judge", jud, "--requests", req, *extra)
        self.assertExit(p, 0)
        return p, json.loads(p.stdout), accepted

    def test_se_sp_overall_and_per_segment(self):
        # v1: 36/40 correct accepted (Se .9), 16/20 errors rejected (Sp .8); v2: 10/10 accepted, 5/5 rejected
        p, out, _ = self.calibrate([("v1", 36, 4, 16, 4), ("v2", 10, 0, 5, 0)])
        v1, v2 = out["segments"]["version=v1"], out["segments"]["version=v2"]
        self.assertAlmostEqual(v1["se"], 0.9)
        self.assertAlmostEqual(v1["sp"], 0.8)
        self.assertEqual((v1["n"], v1["errors"]), (60, 20))
        self.assertAlmostEqual(v2["se"], 1.0)
        self.assertAlmostEqual(v2["sp"], 1.0)
        self.assertAlmostEqual(out["overall"]["se"], 46 / 50)
        self.assertAlmostEqual(out["overall"]["sp"], 21 / 25)
        self.assertEqual(out["overall"]["n"], 75)
        lo, hi = v1["se_ci"]
        self.assertTrue(lo < 0.9 < hi)
        self.assertIn("version=v1", p.stderr)
        self.assertIn("Se=  90%", p.stderr)
        self.assertIn("Sp=  80%", p.stderr)

    def test_validated_thresholds_and_unvalidated_flag(self):
        # v1: 30 labels, 10 errors -> validated. v2: 29 labels -> not. v3: 40 labels but 9 errors -> not.
        p, out, _ = self.calibrate([("v1", 20, 0, 10, 0), ("v2", 20, 0, 9, 0), ("v3", 31, 0, 9, 0)])
        s = out["segments"]
        self.assertTrue(s["version=v1"]["validated"])
        self.assertFalse(s["version=v2"]["validated"])
        self.assertFalse(s["version=v3"]["validated"])
        lines = {l.split()[0]: l for l in p.stderr.splitlines() if l.strip().startswith(("version=", "overall"))}
        self.assertNotIn("UNVALIDATED", lines["version=v1"])
        self.assertIn("UNVALIDATED", lines["version=v2"])
        self.assertIn("UNVALIDATED", lines["version=v3"])

    def test_lists_ids_where_judge_accepted_a_human_labeled_error(self):
        p, out, accepted = self.calibrate([("v1", 30, 0, 8, 3)])
        self.assertEqual(len(accepted), 3)
        self.assertEqual(sorted(out["overall"]["accepted_errors"]), sorted(accepted))
        self.assertIn("judge passed wrong outputs:", p.stderr)
        for rid in accepted:
            self.assertIn(rid, p.stderr)

    def test_no_accepted_errors_no_listing(self):
        p, out, _ = self.calibrate([("v1", 30, 0, 12, 0)])
        self.assertEqual(out["overall"]["accepted_errors"], [])
        self.assertNotIn("judge passed wrong outputs", p.stderr)

    def test_no_errors_gives_null_sp(self):
        p, out, _ = self.calibrate([("v1", 35, 0, 0, 0)])
        self.assertIsNone(out["overall"]["sp"])
        self.assertIn("n/a", p.stderr)
        self.assertIn("UNVALIDATED", p.stderr)

    def test_by_custom_field(self):
        lab, jud, req, _ = make_calibration_inputs(self.tmp, [("v1", 5, 0, 5, 0)])
        p = run(PATH, "calibrate", "--labels", lab, "--judge", jud, "--requests", req, "--by", "predicted")
        self.assertExit(p, 0)
        self.assertEqual(list(json.loads(p.stdout)["segments"]), ["predicted=A"])

    def test_missing_judge_verdict_is_skipped_with_note(self):
        lab, jud, req, _ = make_calibration_inputs(self.tmp, [("v1", 5, 0, 5, 0)])
        rows = [json.loads(l) for l in Path(jud).read_text().splitlines()]
        drop = rows.pop(0)["request_id"]
        write_jsonl(self.tmp / "judge.jsonl", rows)
        p = run(PATH, "calibrate", "--labels", lab, "--judge", jud, "--requests", req)
        self.assertExit(p, 0)
        self.assertIn(f"skip {drop}", p.stderr)
        self.assertEqual(json.loads(p.stdout)["overall"]["n"], 9)


def calib(se, sp, validated=True):
    return {"se": se, "sp": sp, "validated": validated}


def graded_rows(n_w1=10, n_w4=10, version="v1", pass_w1=6, pass_w4=6):
    rows = []
    for i in range(n_w1):
        rows.append({"request_id": f"a{i}", "version": version, "_weight": 1.0,
                     "verdict": "correct" if i < pass_w1 else "incorrect"})
    for i in range(n_w4):
        rows.append({"request_id": f"b{i}", "version": version, "_weight": 4.0,
                     "verdict": "correct" if i < pass_w4 else "incorrect"})
    return rows


class ReportTests(TmpCase):
    def report(self, rows, cal, *extra):
        g = write_jsonl(self.tmp / "graded.jsonl", rows)
        c = write_json(self.tmp / "cal.json", cal)
        p = run(PATH, "report", g, "--calibration", c, *extra)
        self.assertExit(p, 0)
        return p

    def line(self, p, prefix):
        return next(l for l in p.stdout.splitlines() if l.startswith(prefix))

    def test_rogan_gladen_corrected_rate_with_ci(self):
        # equal weights, p = 0.6, Se=.9, Sp=.8 -> (0.6+0.8-1)/(0.9+0.8-1) = 0.5714
        rows = graded_rows(10, 10, pass_w1=6, pass_w4=6)
        for r in rows:
            r["_weight"] = 1.0
        cal = {"overall": calib(0.9, 0.8), "segments": {"version=v1": calib(0.9, 0.8)}}
        p = self.report(rows, cal)
        ln = self.line(p, "version=v1")
        self.assertIn("57.1%", ln)
        self.assertRegex(ln, r"57\.1%\s+\[\d+\.\d%, \d+\.\d%\]")
        lo, hi = [float(x.strip("%[]")) for x in ln.split("[")[1].split("]")[0].split(",")]
        self.assertLessEqual(lo, 57.2)
        self.assertGreaterEqual(hi, 57.0)
        self.assertLess(lo, hi)
        self.assertIn("calibration: segment", ln)
        self.assertNotIn("PROVISIONAL", ln)

    def test_corrected_rate_is_weighted(self):
        # w1 rows: 10/10 pass; w4 rows: 0/10 pass -> weighted p = 10/50 = 0.2; Se=1,Sp=.9 -> (0.2-0.1)/0.9 = 11.1%
        rows = graded_rows(10, 10, pass_w1=10, pass_w4=0)
        cal = {"overall": calib(1.0, 0.9), "segments": {"version=v1": calib(1.0, 0.9)}}
        self.assertIn("11.1%", self.line(self.report(rows, cal), "version=v1"))

    def test_kish_effective_n_shown(self):
        # weights: ten 1.0 and ten 4.0 -> (10+40)^2 / (10+160) = 14.7 -> "effective 15"
        cal = {"overall": calib(0.9, 0.8), "segments": {"version=v1": calib(0.9, 0.8)}}
        ln = self.line(self.report(graded_rows(), cal), "version=v1")
        self.assertIn("n=20 (effective 15)", ln)

    def test_equal_weights_effective_n_equals_n(self):
        rows = graded_rows(8, 0)
        cal = {"overall": calib(0.9, 0.8), "segments": {"version=v1": calib(0.9, 0.8)}}
        self.assertIn("n=8 (effective 8)", self.line(self.report(rows, cal), "version=v1"))

    def test_provisional_when_segment_unvalidated(self):
        cal = {"overall": calib(0.9, 0.8), "segments": {"version=v1": calib(0.9, 0.8, validated=False)}}
        ln = self.line(self.report(graded_rows(), cal), "version=v1")
        self.assertIn("PROVISIONAL", ln)
        self.assertIn("judge not validated for this group", ln)

    def test_provisional_when_calibration_missing_for_group(self):
        cal = {"overall": calib(0.9, 0.8), "segments": {"version=v1": calib(0.9, 0.8)}}
        rows = graded_rows(version="v9")
        ln = self.line(self.report(rows, cal), "version=v9")
        self.assertIn("PROVISIONAL", ln)
        self.assertIn("judge never checked on this group", ln)
        self.assertIn("overall (no segment calibration)", ln)

    def test_provisional_when_overall_missing_se_sp(self):
        cal = {"overall": calib(None, None, validated=False), "segments": {}}
        ln = self.line(self.report(graded_rows(), cal), "version=v1")
        self.assertIn("PROVISIONAL", ln)
        # uncorrected: weighted pass rate = (6 + 24) / 50 = 60%
        self.assertIn("60.0%", ln)

    def test_provisional_when_judge_too_weak(self):
        cal = {"overall": calib(0.5, 0.5), "segments": {"version=v1": calib(0.5, 0.5)}}  # Se+Sp-1 = 0
        ln = self.line(self.report(graded_rows(), cal), "version=v1")
        self.assertIn("judge too weak to correct", ln)

    def test_rows_without_valid_verdict_ignored_and_groups_separate(self):
        rows = graded_rows(5, 0, version="v1") + graded_rows(5, 0, version="v2")
        rows.append({"request_id": "z", "version": "v1", "_weight": 1.0, "verdict": "error"})
        cal = {"overall": calib(0.9, 0.8), "segments": {"version=v1": calib(0.9, 0.8), "version=v2": calib(0.9, 0.8)}}
        p = self.report(rows, cal)
        self.assertIn("n=5 ", self.line(p, "version=v1"))
        self.assertIn("n=5 ", self.line(p, "version=v2"))
        self.assertIn("CI covers sampling only", p.stdout)

    def test_report_is_deterministic(self):
        cal = {"overall": calib(0.9, 0.8), "segments": {"version=v1": calib(0.9, 0.8)}}
        a = self.report(graded_rows(), cal).stdout
        b = self.report(graded_rows(), cal).stdout
        self.assertEqual(a, b)


class EndToEndTests(TmpCase):
    def test_sample_to_calibrate_to_report(self):
        rows = make_requests(400, seed=5)
        write_jsonl(self.tmp / "req.jsonl", rows)
        write_json(self.tmp / "st.json", [{"name": "all", "rate": 0.5}])
        s = run(PATH, "sample", self.tmp / "req.jsonl", "--strata", self.tmp / "st.json")
        self.assertExit(s, 0)
        sampled = [json.loads(l) for l in s.stdout.splitlines()]
        rng = random.Random(8)
        graded = [{**r, "verdict": rng.choice(["correct", "incorrect"])} for r in sampled]
        write_jsonl(self.tmp / "graded.jsonl", graded)
        write_json(self.tmp / "cal.json", {"overall": calib(0.9, 0.9), "segments": {}})
        p = run(PATH, "report", self.tmp / "graded.jsonl", "--calibration", self.tmp / "cal.json")
        self.assertExit(p, 0)
        self.assertIn("version=v1", p.stdout)
        self.assertIn("version=v2", p.stdout)
        self.assertIn("PROVISIONAL", p.stdout)


if __name__ == "__main__":
    unittest.main()
