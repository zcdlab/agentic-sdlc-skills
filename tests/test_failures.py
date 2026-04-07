"""Tests for skills/6-fix-analyze-eval-failures/failures.py."""
import unittest

from helpers import TmpCase, run, script, write_json, write_jsonl

F = script("6-fix-analyze-eval-failures", "failures.py")


def trial(failed=(), output="", stderr=""):
    return {"output": output, "stderr": stderr, "secs": 0.1, "pass": not failed, "score": 0.0 if failed else 1.0,
            "failed": list(failed)}


def result(cid, *trials):
    return {"id": cid, "passes": sum(t["pass"] for t in trials), "trials": list(trials)}


class Failures(TmpCase):
    def res(self, *results):
        return write_json(self.tmp / "r.json", {"cmd": "x", "summary": {}, "results": list(results)})

    def test_help(self):
        p = run(F, "--help")
        self.assertExit(p, 0)
        self.assertIn("failures.py results.json", p.stdout)

    def test_missing_arg_errors(self):
        self.assertEqual(run(F).returncode, 2)

    def test_missing_file_errors(self):
        self.assertNotEqual(run(F, str(self.tmp / "nope.json")).returncode, 0)

    def test_all_pass(self):
        p = run(F, self.res(result("a", trial(), trial())))
        self.assertExit(p, 0)
        self.assertIn("0/2 trials failed; 0 are INFRA", p.stdout)
        self.assertIn("quality pass rate (INFRA excluded): 100% of 2 trials", p.stdout)
        self.assertNotIn("##", p.stdout)

    def test_check_failures_grouped_by_sorted_signature(self):
        p = run(F, self.res(
            result("a", trial(["zeta==1", "alpha==2"], output="out-a")),
            result("b", trial(["alpha==2", "zeta==1"], output="out-b")),
            result("c", trial(["other"], output="out-c"))))
        self.assertExit(p, 0)
        self.assertIn("3/3 trials failed; 0 are INFRA", p.stdout)
        self.assertIn("## CHECK alpha==2 & zeta==1  (2 trials, 2 cases: a, b)", p.stdout)
        self.assertIn("## CHECK other  (1 trials, 1 cases: c)", p.stdout)
        # bigger group first
        self.assertLess(p.stdout.index("alpha==2 & zeta==1"), p.stdout.index("CHECK other"))

    def test_infra_signature_uses_last_error_class_from_stderr(self):
        p = run(F, self.res(result("a", trial(["exit==0"], stderr="ValueError: x\nthen\nKeyError: y"))))
        self.assertIn("## INFRA KeyError", p.stdout)
        self.assertIn("1/1 trials failed; 1 are INFRA", p.stdout)

    def test_infra_timeout_and_plain_nonzero(self):
        p = run(F, self.res(
            result("a", trial(["exit==0"], stderr="subprocess.TimeoutExpired: timed out")),
            result("b", trial(["exit==0"], stderr="segfault-ish"))))
        self.assertIn("## INFRA subprocess.TimeoutExpired", p.stdout)
        self.assertIn("## INFRA non-zero exit", p.stdout)

    def test_infra_excluded_from_quality_rate(self):
        # 4 trials: 1 pass, 1 check fail, 2 infra -> quality = (4-3)/(4-2) = 50% of 2
        p = run(F, self.res(result("a", trial(), trial(["x"]), trial(["exit==0"]), trial(["exit==0"]))))
        self.assertIn("3/4 trials failed; 2 are INFRA", p.stdout)
        self.assertIn("quality pass rate (INFRA excluded): 50% of 2 trials", p.stdout)

    def test_all_infra_omits_quality_line(self):
        p = run(F, self.res(result("a", trial(["exit==0"]))))
        self.assertExit(p, 0)
        self.assertNotIn("quality pass rate", p.stdout)

    def test_samples_limit_and_one_per_case(self):
        rs = [result(c, trial(["x"], output=f"OUT-{c}-1"), trial(["x"], output=f"OUT-{c}-2")) for c in "abcd"]
        p = run(F, self.res(*rs), "--samples", "2")
        self.assertEqual(p.stdout.count("output:"), 2)
        self.assertIn("OUT-a-1", p.stdout)
        self.assertNotIn("OUT-a-2", p.stdout)  # one sample per case
        self.assertNotIn("OUT-c-1", p.stdout)  # beyond --samples
        p = run(F, self.res(*rs), "--samples", "4")
        self.assertEqual(p.stdout.count("output:"), 4)

    def test_default_samples_is_two(self):
        rs = [result(c, trial(["x"], output=f"OUT-{c}")) for c in "abc"]
        p = run(F, self.res(*rs))
        self.assertEqual(p.stdout.count("output:"), 2)

    def test_sample_falls_back_to_stderr_when_no_output(self):
        p = run(F, self.res(result("a", trial(["exit==0"], output="", stderr="RuntimeError: kaboom"))))
        self.assertIn("[a] output: RuntimeError: kaboom", p.stdout)

    def test_output_truncated_to_300(self):
        p = run(F, self.res(result("a", trial(["x"], output="Z" * 500))))
        line = [l for l in p.stdout.splitlines() if "[a] output:" in l][0]
        self.assertEqual(line.count("Z"), 300)

    def test_cases_file_adds_input_with_ids_comments_defaults(self):
        cases = self.tmp / "cases.jsonl"
        cases.write_text('# comment\n\n{"id": "named", "input": "the named input"}\n{"input": {"k": "v"}}\n')
        p = run(F, self.res(result("named", trial(["x"], output="o1")), result("2", trial(["x"], output="o2"))),
                "--cases", str(cases))
        self.assertExit(p, 0)
        self.assertIn("[named] input:  the named input", p.stdout)
        self.assertIn("[2] input:  {'k': 'v'}", p.stdout)  # un-id'd case gets 1-based index among non-comment lines

    def test_cases_input_truncated_to_200(self):
        cases = write_jsonl(self.tmp / "c.jsonl", [{"id": "a", "input": "Q" * 400}])
        p = run(F, self.res(result("a", trial(["x"], output="o"))), "--cases", cases)
        line = [l for l in p.stdout.splitlines() if "[a] input:" in l][0]
        self.assertEqual(line.count("Q"), 200)

    def test_no_input_line_without_cases(self):
        p = run(F, self.res(result("a", trial(["x"], output="o"))))
        self.assertNotIn("input:", p.stdout)

    def test_case_missing_from_cases_file_prints_no_input(self):
        cases = write_jsonl(self.tmp / "c.jsonl", [{"id": "other", "input": "i"}])
        p = run(F, self.res(result("a", trial(["x"], output="o"))), "--cases", cases)
        self.assertExit(p, 0)
        self.assertNotIn("input:", p.stdout)
        self.assertIn("[a] output: o", p.stdout)

    def test_end_to_end_with_evalrun_output(self):
        import sys
        ev = script("2-build-create-evals", "evalrun.py")
        tgt = self.tmp / "t.py"
        tgt.write_text('import sys\nif "crash" in sys.stdin.read():\n    sys.stderr.write("OSError: bad\\n"); sys.exit(2)\nprint("hi")\n')
        cases = write_jsonl(self.tmp / "c.jsonl", [{"id": "ok", "input": "a", "contains": ["hi"]},
                                                     {"id": "chk", "input": "b", "contains": ["nope"]},
                                                     {"id": "boom", "input": "crash", "contains": ["hi"]}])
        out = self.tmp / "res.json"
        self.assertExit(run(ev, "run", cases, "--cmd", f"{sys.executable} {tgt}", "--trials", "1", "--out", out), 0)
        p = run(F, out, "--cases", cases)
        self.assertExit(p, 0)
        self.assertIn("2/3 trials failed; 1 are INFRA", p.stdout)
        self.assertIn("## INFRA OSError", p.stdout)
        self.assertIn("## CHECK contains 'nope'", p.stdout)


if __name__ == "__main__":
    unittest.main()
