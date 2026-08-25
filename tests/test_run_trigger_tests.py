"""Tests for skills/3-test-skill-triggering/run_trigger_tests.py.

Offline only: a fake `claude` executable is placed first on PATH inside self.tmp, so the real CLI is never
invoked. The fake reads a behaviour script per prompt from fake.json and logs its argv to argv.log.
"""
import json
import os
import sys
import unittest

from helpers import TmpCase, run, script, write_jsonl

R = script("3-test-skill-triggering", "run_trigger_tests.py")

FAKE = r'''#!{py}
import hashlib, json, os, sys, time
d = {tmp!r}
argv = sys.argv[1:]
with open(os.path.join(d, "argv.log"), "a") as f:
    f.write(json.dumps({{"argv": argv, "cwd": os.getcwd()}}) + "\n")
prompt = argv[argv.index("-p") + 1]
cfg = json.load(open(os.path.join(d, "fake.json")))
loaded = cfg.get("loaded", [])
steps = cfg["prompts"][prompt]
cnt = os.path.join(d, "cnt-" + hashlib.md5(prompt.encode()).hexdigest())
n = int(open(cnt).read()) if os.path.exists(cnt) else 0
open(cnt, "w").write(str(n + 1))
step = steps[min(n, len(steps) - 1)]
out = lambda o: print(json.dumps(o), flush=True)
out({{"type": "system", "subtype": "init", "skills": loaded}})
if step == "hang":
    time.sleep(60)
elif step.startswith("skill:"):
    out({{"type": "assistant", "message": {{"content": [
        {{"type": "tool_use", "name": "Read", "input": {{}}}},
        {{"type": "tool_use", "name": "Skill", "input": {{"skill": step[6:]}}}}]}}}})
    time.sleep(0.05)
    out({{"type": "result", "subtype": "success"}})
elif step == "none":
    out({{"type": "assistant", "message": {{"content": [{{"type": "text", "text": "ok"}}]}}}})
    out({{"type": "result", "subtype": "success"}})
elif step == "trunc":
    out({{"type": "result", "subtype": "error_max_turns"}})
elif step == "eof":
    pass
'''


class Trigger(TmpCase):
    def setUp(self):
        super().setUp()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        fake = self.bin / "claude"
        fake.write_text(FAKE.format(py=sys.executable, tmp=str(self.tmp)))
        fake.chmod(0o755)
        self.env = {"PATH": f"{self.bin}:{os.environ['PATH']}"}

    def fake(self, prompts, loaded=None):
        (self.tmp / "fake.json").write_text(json.dumps({"prompts": prompts, "loaded": loaded or []}))

    def go(self, cases, *args, code=None):
        cp = write_jsonl(self.tmp / "cases.jsonl", cases)
        p = run(R, cp, *args, env=self.env)
        if code is not None:
            self.assertExit(p, code)
        return p

    def calls(self):
        p = self.tmp / "argv.log"
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []

    # --- argument parsing / help -------------------------------------------------------------------------
    def test_help(self):
        p = run(R, "--help")
        self.assertExit(p, 0)
        for s in ("--trials", "--max-turns", "--model", "--min-pass", "--timeout", "--out", "TRUNCATED"):
            self.assertIn(s, p.stdout)

    def test_missing_cases_arg(self):
        p = run(R, env=self.env)
        self.assertEqual(p.returncode, 2)
        self.assertIn("cases", p.stderr)

    def test_bad_option_types(self):
        for a in (("--trials", "x"), ("--min-pass", "high"), ("--timeout", "1.5"), ("--max-turns", "z")):
            p = run(R, "c.jsonl", *a, env=self.env)
            self.assertEqual(p.returncode, 2, a)

    def test_missing_cases_file(self):
        p = run(R, str(self.tmp / "nope.jsonl"), env=self.env)
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(self.calls(), [])

    # --- case-file validation ----------------------------------------------------------------------------
    def test_invalid_json_line_fails_before_any_claude_call(self):
        (self.tmp / "cases.jsonl").write_text('{"prompt": "a", "expect": "x"}\nnot json\n')
        p = run(R, self.tmp / "cases.jsonl", env=self.env)
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_case_without_expect_fails(self):
        self.fake({"a": ["none"]})
        p = self.go([{"prompt": "a"}])
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("expect", p.stderr)

    def test_case_without_prompt_fails(self):
        self.fake({})
        p = self.go([{"expect": "x"}])
        self.assertNotEqual(p.returncode, 0)

    def test_comments_and_blank_lines_skipped(self):
        self.fake({"p1": ["skill:a"], "p2": ["skill:b"]}, loaded=["a", "b"])
        (self.tmp / "cases.jsonl").write_text('# c\n\n{"prompt": "p1", "expect": "a"}\n   # x\n{"prompt": "p2", "expect": "b"}\n')
        p = run(R, self.tmp / "cases.jsonl", "--trials", "1", env=self.env)
        self.assertExit(p, 0)
        self.assertIn("Cases passing: 2/2", p.stdout)

    def test_empty_cases_file_reports_zero(self):
        (self.tmp / "cases.jsonl").write_text("")
        p = run(R, self.tmp / "cases.jsonl", env=self.env)
        self.assertExit(p, 0)
        self.assertIn("Cases passing: 0/0", p.stdout)
        self.assertEqual(self.calls(), [])

    # --- scoring and report ------------------------------------------------------------------------------
    def test_all_pass_report(self):
        self.fake({"fix it": ["skill:alpha"]}, loaded=["alpha"])
        p = self.go([{"prompt": "fix it", "expect": "alpha"}], "--trials", "2", code=0)
        self.assertIn("| # | prompt | expect | got | pass |", p.stdout)
        self.assertIn("| 1 | fix it | alpha | alpha, alpha | 2/2 ✅ |", p.stdout)
        self.assertIn("Cases passing: 1/1  (min-pass 100% of 2 trials)", p.stdout)
        self.assertIn("| fired | correct | wrong |", p.stdout)
        self.assertIn("| alpha | 2 | 0 |", p.stdout)
        self.assertNotIn("TRUNCATED", p.stdout)

    def test_wrong_skill_fails_exit_one(self):
        self.fake({"p": ["skill:beta"]}, loaded=["alpha", "beta"])
        p = self.go([{"prompt": "p", "expect": "alpha"}], "--trials", "1", code=1)
        self.assertIn("| 1 | p | alpha | beta | 0/1 ❌ |", p.stdout)
        self.assertIn("| beta | 0 | 1 |", p.stdout)
        self.assertIn("Cases passing: 0/1", p.stdout)

    def test_expect_none_passes_when_no_skill_fires(self):
        self.fake({"rename foo": ["none"]}, loaded=["alpha"])
        p = self.go([{"prompt": "rename foo", "expect": "NONE"}], "--trials", "1", code=0)
        self.assertIn("| 1 | rename foo | NONE | NONE | 1/1 ✅ |", p.stdout)
        self.assertIn("| NONE | 1 | 0 |", p.stdout)

    def test_expect_none_fails_when_skill_fires(self):
        self.fake({"p": ["skill:alpha"]}, loaded=["alpha"])
        self.go([{"prompt": "p", "expect": "NONE"}], "--trials", "1", code=1)

    def test_expect_list_any_passes(self):
        self.fake({"p": ["skill:b"]}, loaded=["a", "b"])
        p = self.go([{"prompt": "p", "expect": ["a", "b"]}], "--trials", "1", code=0)
        self.assertIn("| a|b |", p.stdout)

    def test_min_pass_threshold(self):
        self.fake({"p": ["skill:a", "skill:a", "skill:b"]}, loaded=["a", "b"])
        case = [{"prompt": "p", "expect": "a"}]
        p = self.go(case, "--trials", "3", code=1)  # default min-pass 1.0
        self.assertIn("a, a, b | 2/3 ❌", p.stdout)
        for f in (self.tmp).glob("cnt-*"):
            f.unlink()
        p = self.go(case, "--trials", "3", "--min-pass", "0.6", code=0)
        self.assertIn("2/3 ✅", p.stdout)
        self.assertIn("(min-pass 60% of 3 trials)", p.stdout)

    def test_min_pass_boundary_is_inclusive(self):
        self.fake({"p": ["skill:a", "skill:b"]}, loaded=["a", "b"])
        self.go([{"prompt": "p", "expect": "a"}], "--trials", "2", "--min-pass", "0.5", code=0)

    def test_min_pass_rounded_fraction_means_two_of_three(self):
        # regression: SKILL.md recommends 0.67 for 3 trials, but 2/3 = 0.6667 < 0.67 used to fail
        self.fake({"p": ["skill:a", "skill:a", "skill:b"]}, loaded=["a", "b"])
        p = self.go([{"prompt": "p", "expect": "a"}], "--trials", "3", "--min-pass", "0.67", code=0)
        self.assertIn("2/3 ✅", p.stdout)

    def test_min_pass_tolerance_does_not_round_down_real_misses(self):
        # 0.6 of 5 trials needs 3; 2/5 must still fail
        self.fake({"p": ["skill:a", "skill:a", "skill:b", "skill:b", "skill:b"]}, loaded=["a", "b"])
        self.go([{"prompt": "p", "expect": "a"}], "--trials", "5", "--min-pass", "0.6", code=1)

    def test_cases_passing_counts_mixed(self):
        self.fake({"p1": ["skill:a"], "p2": ["skill:z"]}, loaded=["a", "z"])
        p = self.go([{"prompt": "p1", "expect": "a"}, {"prompt": "p2", "expect": "a"}], "--trials", "1", code=1)
        self.assertIn("Cases passing: 1/2", p.stdout)
        self.assertIn("| a | 1 | 0 |", p.stdout)
        self.assertIn("| z | 0 | 1 |", p.stdout)

    def test_truncated_counts_as_fail_with_hint(self):
        self.fake({"p": ["trunc"]}, loaded=["a"])
        p = self.go([{"prompt": "p", "expect": "a"}], "--trials", "2", code=1)
        self.assertIn("TRUNCATED, TRUNCATED", p.stdout)
        self.assertIn("2 trial(s) TRUNCATED before deciding", p.stdout)

    def test_truncated_not_matching_expect_none(self):
        self.fake({"p": ["trunc"]}, loaded=["a"])
        self.go([{"prompt": "p", "expect": "NONE"}], "--trials", "1", code=1)

    def test_stream_ends_without_result_is_truncated(self):
        self.fake({"p": ["eof"]}, loaded=["a"])
        p = self.go([{"prompt": "p", "expect": "a"}], "--trials", "1", code=1)
        self.assertIn("TRUNCATED", p.stdout)

    def test_timeout_kills_trial_and_marks_truncated(self):
        self.fake({"p": ["hang"]}, loaded=["a"])
        p = self.go([{"prompt": "p", "expect": "a"}], "--trials", "1", "--timeout", "1", code=1)
        self.assertIn("TRUNCATED", p.stdout)

    def test_progress_goes_to_stderr(self):
        self.fake({"p": ["skill:a"]}, loaded=["a"])
        p = self.go([{"prompt": "p", "expect": "a"}], "--trials", "2", code=0)
        self.assertIn("[1.1] a", p.stderr)
        self.assertIn("[1.2] a", p.stderr)

    def test_out_jsonl_records(self):
        self.fake({"p": ["skill:a"]}, loaded=["a", "b"])
        out = self.tmp / "out.jsonl"
        self.go([{"prompt": "p", "expect": ["a", "b"]}], "--trials", "2", "--out", str(out), code=0)
        recs = [json.loads(l) for l in out.read_text().splitlines()]
        self.assertEqual([(r["case"], r["trial"]) for r in recs], [(1, 1), (1, 2)])
        self.assertEqual(recs[0]["got"], "a")
        self.assertEqual(recs[0]["expect"], ["a", "b"])
        self.assertEqual(recs[0]["prompt"], "p")
        self.assertEqual(recs[0]["tools_before"], ["Read", "Skill"])

    def test_no_out_file_by_default(self):
        self.fake({"p": ["skill:a"]}, loaded=["a"])
        self.go([{"prompt": "p", "expect": "a"}], "--trials", "1", code=0)
        self.assertEqual(sorted(p.name for p in self.tmp.glob("*.jsonl")), ["cases.jsonl"])

    # --- abort when expected skills are not loaded -------------------------------------------------------
    def test_abort_when_expected_skill_not_loaded(self):
        self.fake({"p": ["skill:a"]}, loaded=["a"])
        p = self.go([{"prompt": "p", "expect": ["a", "ghost"]}], "--trials", "1", code=1)
        self.assertIn("ABORT: expected skills not loaded in headless session: ghost", p.stderr)
        self.assertNotIn("Cases passing", p.stdout)

    def test_none_never_counts_as_missing_skill(self):
        self.fake({"p": ["none"]}, loaded=["a"])
        self.go([{"prompt": "p", "expect": "NONE"}], "--trials", "1", code=0)

    def test_no_abort_check_when_init_lists_no_skills(self):
        self.fake({"p": ["skill:a"]}, loaded=[])
        self.go([{"prompt": "p", "expect": "a"}], "--trials", "1", code=0)

    def test_abort_considers_expectations_of_all_cases(self):
        self.fake({"p1": ["skill:a"], "p2": ["skill:a"]}, loaded=["a"])
        p = self.go([{"prompt": "p1", "expect": "a"}, {"prompt": "p2", "expect": "other"}], "--trials", "1", code=1)
        self.assertIn("not loaded in headless session: other", p.stderr)

    # --- how claude is invoked ---------------------------------------------------------------------------
    def test_claude_invocation_flags(self):
        self.fake({"p": ["skill:a"]}, loaded=["a"])
        self.go([{"prompt": "p", "expect": "a"}], "--trials", "1", "--max-turns", "7", "--model", "m-1", code=0)
        c = self.calls()[0]["argv"]
        self.assertEqual(c[:2], ["-p", "p"])
        for flag, val in (("--output-format", "stream-json"), ("--max-turns", "7"), ("--tools", "Skill Read Glob Grep"),
                          ("--model", "m-1")):
            self.assertEqual(c[c.index(flag) + 1], val, flag)
        for flag in ("--verbose", "--no-session-persistence", "--strict-mcp-config"):
            self.assertIn(flag, c)

    def test_default_flags_no_model_and_max_turns_4(self):
        self.fake({"p": ["skill:a"]}, loaded=["a"])
        self.go([{"prompt": "p", "expect": "a"}], "--trials", "1", code=0)
        c = self.calls()[0]["argv"]
        self.assertNotIn("--model", c)
        self.assertEqual(c[c.index("--max-turns") + 1], "4")

    def test_trials_default_three(self):
        self.fake({"p": ["skill:a"]}, loaded=["a"])
        self.go([{"prompt": "p", "expect": "a"}], code=0)
        self.assertEqual(len(self.calls()), 3)

    def test_runs_in_scratch_git_fixture_with_uncommitted_diff(self):
        self.fake({"p": ["skill:a"]}, loaded=["a"])
        self.go([{"prompt": "p", "expect": "a"}], "--trials", "1", code=0)
        cwd = self.calls()[0]["cwd"]
        self.assertNotEqual(os.path.realpath(cwd), os.path.realpath(self.tmp))
        self.assertFalse(os.path.exists(cwd), "scratch dir should be cleaned up")

    def test_fixture_contents(self):
        # Make the fake snapshot its cwd, to inspect the fixture the script built.
        snap = self.tmp / "snap.txt"
        fake = self.bin / "claude"
        fake.write_text(fake.read_text().replace(
            "out = lambda", f"import subprocess\nopen({str(snap)!r}, 'w').write(subprocess.run(['git','status','--porcelain'],capture_output=True,text=True).stdout + ' '.join(sorted(os.listdir('.'))))\nout = lambda"))
        self.fake({"p": ["skill:a"]}, loaded=["a"])
        self.go([{"prompt": "p", "expect": "a"}], "--trials", "1", code=0)
        text = snap.read_text()
        self.assertIn(" M utils.py", text)  # uncommitted diff
        for f in ("README.md", "auth.py", "tests", ".git"):
            self.assertIn(f, text)

    def test_every_claude_call_used_the_fake(self):
        # sanity guard: the fake logs every invocation; trial count must equal log length
        self.fake({"p1": ["skill:a"], "p2": ["none"]}, loaded=["a"])
        self.go([{"prompt": "p1", "expect": "a"}, {"prompt": "p2", "expect": "NONE"}], "--trials", "2", code=0)
        self.assertEqual(len(self.calls()), 4)


if __name__ == "__main__":
    unittest.main()
