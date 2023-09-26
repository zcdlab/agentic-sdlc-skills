"""Tests for skills/2-build-create-evals/evalrun.py (ports every case of evalrun_selftest.sh, plus more)."""
import json
import sys
import unittest

from helpers import TmpCase, run, script, write_json, write_jsonl

EVAL = script("2-build-create-evals", "evalrun.py")

GOOD = '''
import sys, json
t = sys.stdin.read()
if "nested" in t: print(json.dumps({"category": "billing", "args": {"id": "T-1", "extra": 1}}))
elif "refund" in t: print(json.dumps({"category": "billing"}))
elif "sue" in t: print(json.dumps({"category": "legal", "escalate": True}))
else: print("hello there")
'''

CASES = [
    {"id": "billing", "input": "refund please", "expect": {"category": "billing"}},
    {"id": "escalate", "input": "I will sue", "expect": {"category": "legal", "escalate": True}},
    {"id": "text", "input": "hi", "contains": ["hello"], "not_contains": ["refund"]},
    {"id": "anyof", "input": "refund or login?", "expect": {"category": {"any": ["billing", "account_access"]}}},
    {"id": "nested", "input": "nested refund", "expect": {"args": {"id": "T-1"}}},
]


class Base(TmpCase):
    def target(self, name, body):
        p = self.tmp / name
        p.write_text(body)
        return f"{sys.executable} {p}"

    def counter_target(self, name, expr, payload_ok, payload_bad):
        """Target whose answer depends on a call counter stored in a file; expr is a python bool of n."""
        return self.target(name, f'''
import sys, os
p = {str(self.tmp / (name + ".n"))!r}
n = int(open(p).read()) if os.path.exists(p) else 0
open(p, "w").write(str(n + 1))
sys.stdin.read()
print({payload_ok!r} if ({expr}) else {payload_bad!r})
''')

    def cases(self, rows, name="cases.jsonl"):
        return write_jsonl(self.tmp / name, rows)

    def do_run(self, cases, cmd, *extra):
        return run(EVAL, "run", cases, "--cmd", cmd, *extra)


class SelftestPort(Base):
    """One test per assertion of evalrun_selftest.sh."""

    def setUp(self):
        super().setUp()
        self.cases_path = self.cases(CASES)
        self.good = self.target("good.py", GOOD)
        self.bad = self.target("bad.py", GOOD.replace('"escalate": True', '"escalate": False'))

    def test_good_target_pass_hat_100(self):
        p = self.do_run(self.cases_path, self.good, "--trials", "2")
        self.assertExit(p, 0)
        self.assertIn("pass^2: 100%", p.stdout)

    def test_anyof_accepts_billing(self):
        p = self.do_run(self.cases_path, self.good, "--trials", "2")
        self.assertIn("✅ anyof: 2/2", p.stdout)

    def test_nested_subset_ignores_extra_keys(self):
        p = self.do_run(self.cases_path, self.good, "--trials", "2")
        self.assertIn("✅ nested: 2/2", p.stdout)

    def test_bad_target_fails_escalate(self):
        p = self.do_run(self.cases_path, self.bad, "--trials", "2")
        self.assertExit(p, 0)  # run mode never gates
        self.assertIn("❌ escalate: 0/2  failed: escalate==True", p.stdout)

    def test_compare_good_to_bad_regresses(self):
        g, b = str(self.tmp / "good.json"), str(self.tmp / "bad.json")
        self.do_run(self.cases_path, self.good, "--trials", "2", "--out", g)
        self.do_run(self.cases_path, self.bad, "--trials", "2", "--out", b)
        p = run(EVAL, "compare", g, b)
        self.assertExit(p, 1)
        self.assertIn("REGRESSED escalate: 100% -> 0%", p.stdout)
        self.assertIn("GATE: FAIL", p.stdout)

    def test_compare_bad_to_good_improves(self):
        g, b = str(self.tmp / "good.json"), str(self.tmp / "bad.json")
        self.do_run(self.cases_path, self.good, "--trials", "2", "--out", g)
        self.do_run(self.cases_path, self.bad, "--trials", "2", "--out", b)
        p = run(EVAL, "compare", b, g)
        self.assertExit(p, 0)
        self.assertIn("IMPROVED escalate", p.stdout)
        self.assertIn("GATE: PASS", p.stdout)

    def test_flaky_pass_at_4_100_pass_hat_4_0(self):
        flaky = self.counter_target("flaky.py", "n % 2 == 0", '{"category": "billing"}', '{"category": "bug"}')
        one = self.cases(CASES[:1], "one.jsonl")
        p = self.do_run(one, flaky, "--trials", "4")
        self.assertExit(p, 0)
        self.assertIn("pass@4: 100%   pass^4: 0%", p.stdout)


class Grading(Base):
    def one(self, case, output_code, trials="1"):
        cmd = self.target("t.py", output_code)
        p = self.do_run(self.cases([case]), cmd, "--trials", trials, "--out", str(self.tmp / "r.json"))
        self.assertExit(p, 0)
        return p, json.loads((self.tmp / "r.json").read_text())

    def test_any_rejects_value_outside_list(self):
        p, r = self.one({"id": "a", "input": "x", "expect": {"c": {"any": ["x", "y"]}}},
                        'print(\'{"c": "z"}\')')
        self.assertIn("❌ a: 0/1", p.stdout)
        self.assertEqual(r["results"][0]["trials"][0]["failed"], ["c=={'any': ['x', 'y']}"])

    def test_any_accepts_second_member(self):
        p, _ = self.one({"id": "a", "input": "x", "expect": {"c": {"any": ["x", "y"]}}}, 'print(\'{"c": "y"}\')')
        self.assertIn("✅ a: 1/1", p.stdout)

    def test_any_nested_inside_subset(self):
        case = {"id": "n", "input": "x", "expect": {"args": {"kind": {"any": ["a", "b"]}}}}
        p, _ = self.one(case, 'print(\'{"args": {"kind": "b", "z": 1}}\')')
        self.assertIn("✅ n: 1/1", p.stdout)

    def test_nested_subset_mismatch_value(self):
        case = {"id": "n", "input": "x", "expect": {"args": {"id": "T-1"}}}
        p, _ = self.one(case, 'print(\'{"args": {"id": "T-2"}}\')')
        self.assertIn("❌ n: 0/1", p.stdout)

    def test_nested_subset_missing_key_and_non_dict(self):
        case = {"id": "n", "input": "x", "expect": {"args": {"id": "T-1"}}}
        p, _ = self.one(case, 'print(\'{"args": {"other": 1}}\')')
        self.assertIn("❌ n", p.stdout)
        p, _ = self.one(case, 'print(\'{"args": "T-1"}\')')
        self.assertIn("❌ n", p.stdout)

    def test_deeply_nested_subset(self):
        case = {"id": "d", "input": "x", "expect": {"a": {"b": {"c": 1}}}}
        p, _ = self.one(case, 'print(\'{"a": {"b": {"c": 1, "d": 2}, "e": 3}}\')')
        self.assertIn("✅ d: 1/1", p.stdout)

    def test_expect_on_non_json_output_fails(self):
        p, _ = self.one({"id": "j", "input": "x", "expect": {"k": 1}}, 'print("not json")')
        self.assertIn("❌ j: 0/1", p.stdout)

    def test_contains_is_case_insensitive_and_not_contains(self):
        case = {"id": "c", "input": "x", "contains": ["HELLO"], "not_contains": ["BYE"]}
        p, _ = self.one(case, 'print("Hello world")')
        self.assertIn("✅ c: 1/1", p.stdout)
        p, r = self.one(case, 'print("hello bye")')
        self.assertIn("not_contains 'BYE'", p.stdout)
        self.assertEqual(r["results"][0]["trials"][0]["failed"], ["not_contains 'BYE'"])

    def test_regex(self):
        case = {"id": "r", "input": "x", "regex": "^[A-Z]"}
        p, _ = self.one(case, 'print("Yes")')
        self.assertIn("✅ r: 1/1", p.stdout)
        p, _ = self.one(case, 'print("no")')
        self.assertIn("❌ r: 0/1", p.stdout)
        self.assertIn("regex '^[A-Z]'", p.stdout)

    def test_partial_credit_score(self):
        case = {"id": "p", "input": "x", "contains": ["a"], "not_contains": ["zzz"], "expect": {}}
        case["contains"] = ["a", "missing"]
        _, r = self.one(case, 'print("a")')
        t = r["results"][0]["trials"][0]
        self.assertFalse(t["pass"])
        self.assertAlmostEqual(t["score"], 2 / 3)  # a ok, not_contains ok, missing fails
        self.assertAlmostEqual(r["summary"]["mean_score"], 2 / 3)

    def test_case_without_checks_aborts(self):
        cmd = self.target("t.py", 'print("x")')
        p = self.do_run(self.cases([{"id": "empty", "input": "x"}]), cmd, "--trials", "1")
        self.assertExit(p, 1)
        self.assertIn("case empty: no checks", p.stderr)

    def test_comments_blank_lines_and_default_ids(self):
        path = self.tmp / "c.jsonl"
        path.write_text('# comment\n\n{"input": "a", "contains": ["a"]}\n  # indented comment\n'
                        '{"input": "b", "contains": ["b"]}\n')
        cmd = self.target("echo.py", 'import sys; print(sys.stdin.read())')
        p = self.do_run(str(path), cmd, "--trials", "1")
        self.assertExit(p, 0)
        self.assertIn("✅ 1: 1/1", p.stdout)
        self.assertIn("✅ 2: 1/1", p.stdout)

    def test_non_string_input_sent_as_json(self):
        cmd = self.target("echo.py", 'import sys; print(sys.stdin.read())')
        case = {"id": "o", "input": {"a": [1, 2]}, "contains": ['{"a": [1, 2]}']}
        p = self.do_run(self.cases([case]), cmd, "--trials", "1")
        self.assertIn("✅ o: 1/1", p.stdout)

    def test_string_input_sent_as_is(self):
        cmd = self.target("echo.py", 'import sys; print(sys.stdin.read())')
        case = {"id": "s", "input": "plain text", "regex": "^plain text$"}
        p = self.do_run(self.cases([case]), cmd, "--trials", "1")
        self.assertIn("✅ s: 1/1", p.stdout)

    def test_out_file_structure(self):
        cmd = self.target("t.py", 'print("ok")')
        out = self.tmp / "o.json"
        self.do_run(self.cases([{"id": "k", "input": "x", "contains": ["ok"]}]), cmd, "--trials", "2", "--out", str(out))
        d = json.loads(out.read_text())
        self.assertEqual(d["cmd"], cmd)
        self.assertEqual(d["summary"]["cases"], 1)
        self.assertEqual(d["summary"]["trials"], 2)
        self.assertEqual(d["summary"]["pass_hat_k"], 1.0)
        r = d["results"][0]
        self.assertEqual((r["id"], r["passes"], r["pass_at_k"], r["pass_hat_k"]), ("k", 2, True, True))
        self.assertEqual(len(r["trials"]), 2)

    def test_zero_pass_case_called_out(self):
        cmd = self.target("t.py", 'print("ok")')
        p = self.do_run(self.cases([{"id": "z", "input": "x", "contains": ["nope"]}]), cmd, "--trials", "2")
        self.assertIn("0/2 cases (read transcripts", p.stdout)
        self.assertIn("z", p.stdout.splitlines()[-1])


class PassAtK(Base):
    def test_pass_at_k_vs_pass_hat_k_k3(self):
        # first call fails, next two succeed: 2/3 -> pass@3 yes, pass^3 no
        cmd = self.counter_target("f.py", "n >= 1", "ok", "bad")
        p = self.do_run(self.cases([{"id": "f", "input": "x", "contains": ["ok"]}]), cmd, "--trials", "3")
        self.assertIn("⚠️ f: 2/3", p.stdout)
        self.assertIn("pass@3: 100%   pass^3: 0%", p.stdout)

    def test_mixed_cases_fractions(self):
        # case a (always ok) and case b (never ok) -> pass@2 50%, pass^2 50%
        cmd = self.target("t.py", 'import sys; print("ok" if sys.stdin.read() == "a" else "bad")')
        cases = [{"id": "a", "input": "a", "contains": ["ok"]}, {"id": "b", "input": "b", "contains": ["ok"]}]
        p = self.do_run(self.cases(cases), cmd, "--trials", "2")
        self.assertIn("pass@2: 50%   pass^2: 50%", p.stdout)

    def test_all_trials_fail_pass_at_k_zero(self):
        cmd = self.target("t.py", 'print("bad")')
        p = self.do_run(self.cases([{"id": "a", "input": "a", "contains": ["ok"]}]), cmd, "--trials", "3")
        self.assertIn("pass@3: 0%   pass^3: 0%", p.stdout)

    def test_counter_runs_once_per_trial_per_case(self):
        cmd = self.counter_target("c.py", "True", "ok", "ok")
        self.do_run(self.cases([{"id": "a", "input": "a", "contains": ["ok"]},
                                {"id": "b", "input": "b", "contains": ["ok"]}]), cmd, "--trials", "3")
        self.assertEqual((self.tmp / "c.py.n").read_text(), "6")


class Compare(Base):
    def result(self, name, passes_by_id, k=4):
        results = [{"id": i, "passes": p, "pass_at_k": p > 0, "pass_hat_k": p == k, "trials": [{}] * k}
                   for i, p in passes_by_id.items()]
        return write_json(self.tmp / name, {"cmd": "x", "results": results,
                                            "summary": {"pass_hat_k": 0.5, "mean_secs": 0.25}})

    def test_tolerance_allows_small_drop(self):
        b, c = self.result("b.json", {"a": 4}), self.result("c.json", {"a": 3})
        self.assertExit(run(EVAL, "compare", b, c), 1)
        p = run(EVAL, "compare", b, c, "--tolerance", "0.25")
        self.assertExit(p, 0)
        self.assertNotIn("REGRESSED", p.stdout)

    def test_tolerance_still_fails_bigger_drop(self):
        b, c = self.result("b.json", {"a": 4}), self.result("c.json", {"a": 1})
        self.assertExit(run(EVAL, "compare", b, c, "--tolerance", "0.25"), 1)

    def test_identical_passes_no_labels(self):
        b = self.result("b.json", {"a": 2, "b": 4})
        p = run(EVAL, "compare", b, b)
        self.assertExit(p, 0)
        self.assertNotIn("REGRESSED", p.stdout)
        self.assertNotIn("IMPROVED", p.stdout)
        self.assertIn("pass^k 50% -> 50%", p.stdout)
        self.assertIn("mean latency 0.25s -> 0.25s", p.stdout)

    def test_case_set_mismatch_reported_not_compared(self):
        b, c = self.result("b.json", {"a": 4, "gone": 4}), self.result("c.json", {"a": 4, "new": 0})
        p = run(EVAL, "compare", b, c)
        self.assertExit(p, 0)
        self.assertIn("case sets differ (not compared): gone, new", p.stdout)

    def test_partial_rates_reported_as_percent(self):
        b, c = self.result("b.json", {"a": 2}), self.result("c.json", {"a": 1})
        p = run(EVAL, "compare", b, c)
        self.assertExit(p, 1)
        self.assertIn("REGRESSED a: 50% -> 25%", p.stdout)

    def test_missing_file_errors(self):
        p = run(EVAL, "compare", str(self.tmp / "nope.json"), str(self.tmp / "nope2.json"))
        self.assertNotEqual(p.returncode, 0)


class Crashes(Base):
    def test_crashing_target_is_failed_trial_with_stderr(self):
        cmd = self.target("crash.py", 'import sys; sys.stderr.write("ValueError: boom\\n"); sys.exit(3)')
        out = self.tmp / "r.json"
        p = self.do_run(self.cases([{"id": "c", "input": "x", "contains": ["anything"]}]), cmd,
                        "--trials", "2", "--out", str(out))
        self.assertExit(p, 0)
        self.assertIn("❌ c: 0/2  failed: exit==0", p.stdout)
        t = json.loads(out.read_text())["results"][0]["trials"][0]
        self.assertFalse(t["pass"])
        self.assertEqual(t["score"], 0.0)
        self.assertIn("ValueError: boom", t["stderr"])

    def test_crash_with_matching_stdout_still_fails(self):
        cmd = self.target("crash.py", 'print("ok"); raise SystemExit(1)')
        p = self.do_run(self.cases([{"id": "c", "input": "x", "contains": ["ok"]}]), cmd, "--trials", "1")
        self.assertIn("❌ c: 0/1  failed: exit==0", p.stdout)

    def test_nonexistent_command_is_failed_trial(self):
        p = self.do_run(self.cases([{"id": "c", "input": "x", "contains": ["ok"]}]),
                        str(self.tmp / "does-not-exist"), "--trials", "1")
        self.assertExit(p, 0)
        self.assertIn("failed: exit==0", p.stdout)

    def test_timeout_is_recorded_as_failed_trial(self):
        # regression: subprocess.run(timeout=) raises TimeoutExpired, which evalrun.py does not catch, so one slow trial
        # aborts the whole run with a traceback and no --out file. failures.py expects timeouts as INFRA trials.
        cmd = self.target("slow.py", 'import time; time.sleep(4)')
        out = self.tmp / "r.json"
        p = self.do_run(self.cases([{"id": "s", "input": "x", "contains": ["ok"]}]), cmd,
                        "--trials", "1", "--timeout", "1", "--out", str(out))
        self.assertExit(p, 0)
        self.assertIn("failed: exit==0", p.stdout)
        self.assertTrue(out.exists())


class Misc(Base):
    def test_no_held_out_split_support(self):
        # evalrun.py has no held-out/split option: documents that the flag is rejected.
        p = run(EVAL, "run", "x.jsonl", "--cmd", "true", "--split", "held-out")
        self.assertEqual(p.returncode, 2)
        self.assertIn("unrecognized arguments", p.stderr)

    def test_help_exits_zero(self):
        for args in (("--help",), ("run", "--help"), ("compare", "--help")):
            self.assertExit(run(EVAL, *args), 0)

    def test_mode_required(self):
        self.assertEqual(run(EVAL).returncode, 2)

    def test_run_requires_cmd(self):
        p = run(EVAL, "run", "cases.jsonl")
        self.assertEqual(p.returncode, 2)
        self.assertIn("--cmd", p.stderr)


if __name__ == "__main__":
    unittest.main()
