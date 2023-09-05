#!/usr/bin/env python3
"""Minimal eval runner for LLM-driven behavior: run cases N times, grade with code, report pass@k / pass^k.

  evalrun.py run cases.jsonl --cmd "python triage.py" [--trials 3] [--out results.json]
  evalrun.py compare baseline.json candidate.json [--tolerance 0]

Target contract: --cmd is run once per trial with the case "input" on stdin (strings as-is,
other values as JSON). Its stdout is the output; if it parses as JSON, field checks apply.

Case (JSONL, one per line; '#' lines ignored):
  {"id": "refund-angry", "input": "...", "expect": {"category": "billing", "escalate": true}}
  {"id": "greeting", "input": "hi", "contains": ["hello"], "not_contains": ["refund"], "regex": "^[A-Z]"}
  {"id": "ambiguous", "input": "...", "expect": {"category": {"any": ["billing", "account_access"]}}}
  {"id": "close", "input": "close T-1", "expect": {"tool": "tickets_update", "args": {"ticket_id": "T-1"}}}  # nested = subset
A trial passes only if every check passes; the score is the fraction of checks passed (partial credit).
"""
import argparse
import json
import re
import subprocess
import sys
import time


def load_cases(path):
    with open(path) as f:
        cases = [json.loads(l) for l in f if l.strip() and not l.lstrip().startswith("#")]
    for i, c in enumerate(cases):
        c.setdefault("id", str(i + 1))
    return cases


def grade(case, out):
    checks = []
    try:
        parsed = json.loads(out)
    except (json.JSONDecodeError, TypeError):
        parsed = None
    def match(want, got):
        if isinstance(want, dict) and set(want) == {"any"}:
            return got in want["any"]
        if isinstance(want, dict):  # nested dict = subset match: listed keys must match, extra keys allowed
            return isinstance(got, dict) and all(match(v, got.get(k)) for k, v in want.items())
        return got == want

    for key, want in (case.get("expect") or {}).items():
        got = parsed.get(key) if isinstance(parsed, dict) else None
        checks.append((f"{key}=={want!r}", match(want, got)))
    for s in case.get("contains", []):
        checks.append((f"contains {s!r}", s.lower() in out.lower()))
    for s in case.get("not_contains", []):
        checks.append((f"not_contains {s!r}", s.lower() not in out.lower()))
    if "regex" in case:
        checks.append((f"regex {case['regex']!r}", re.search(case["regex"], out) is not None))
    if not checks:
        raise SystemExit(f"case {case['id']}: no checks (add expect/contains/not_contains/regex)")
    return checks


def run(args):
    cases = load_cases(args.cases)
    results = []
    for c in cases:
        stdin = c["input"] if isinstance(c["input"], str) else json.dumps(c["input"])
        trials = []
        for _ in range(args.trials):
            t0 = time.time()
            try:
                p = subprocess.run(args.cmd, shell=True, input=stdin, capture_output=True, text=True, timeout=args.timeout)
            except subprocess.TimeoutExpired:  # a failed (INFRA) trial, not a crash of the whole run
                p = subprocess.CompletedProcess(args.cmd, None, "", f"TimeoutExpired after {args.timeout}s")
            out = p.stdout.strip()
            checks = grade(c, out) if p.returncode == 0 else [("exit==0", False)]
            trials.append({"output": out[:2000], "stderr": p.stderr[-500:], "secs": round(time.time() - t0, 2),
                           "pass": all(ok for _, ok in checks),
                           "score": sum(ok for _, ok in checks) / len(checks),
                           "failed": [name for name, ok in checks if not ok]})
        n_pass = sum(t["pass"] for t in trials)
        results.append({"id": c["id"], "pass_at_k": n_pass > 0, "pass_hat_k": n_pass == len(trials),
                        "passes": n_pass, "trials": trials})
        mark = "✅" if n_pass == len(trials) else ("⚠️" if n_pass else "❌")
        fails = sorted({f for t in trials for f in t["failed"]})
        print(f"{mark} {c['id']}: {n_pass}/{len(trials)}" + (f"  failed: {', '.join(fails)}" if fails else ""))

    n = len(results)
    summary = {"cases": n, "trials": args.trials,
               "pass_at_k": sum(r["pass_at_k"] for r in results) / n,
               "pass_hat_k": sum(r["pass_hat_k"] for r in results) / n,
               "mean_score": sum(t["score"] for r in results for t in r["trials"]) / (n * args.trials),
               "mean_secs": sum(t["secs"] for r in results for t in r["trials"]) / (n * args.trials)}
    print(f"\npass@{args.trials}: {summary['pass_at_k']:.0%}   pass^{args.trials}: {summary['pass_hat_k']:.0%}   "
          f"mean score: {summary['mean_score']:.0%}   mean latency: {summary['mean_secs']:.2f}s")
    zero = [r["id"] for r in results if r["passes"] == 0]
    if zero:
        print(f"0/{args.trials} cases (read transcripts — task or grader may be broken): {', '.join(zero)}")
    if args.out:
        with open(args.out, "w") as f:
            json.dump({"cmd": args.cmd, "summary": summary, "results": results}, f, indent=1)


def compare(args):
    base, cand = (json.load(open(p)) for p in (args.baseline, args.candidate))
    b = {r["id"]: r["passes"] / len(r["trials"]) for r in base["results"]}
    c = {r["id"]: r["passes"] / len(r["trials"]) for r in cand["results"]}
    regressions = [(k, b[k], c[k]) for k in b if k in c and c[k] < b[k] - args.tolerance]
    improvements = [(k, b[k], c[k]) for k in b if k in c and c[k] > b[k]]
    missing = sorted(set(b) ^ set(c))
    for label, rows in (("REGRESSED", regressions), ("IMPROVED", improvements)):
        for k, x, y in rows:
            print(f"{label} {k}: {x:.0%} -> {y:.0%}")
    if missing:
        print(f"case sets differ (not compared): {', '.join(missing)}")
    bs, cs = base["summary"], cand["summary"]
    print(f"\npass^k {bs['pass_hat_k']:.0%} -> {cs['pass_hat_k']:.0%}   "
          f"mean latency {bs['mean_secs']:.2f}s -> {cs['mean_secs']:.2f}s")
    print("GATE: FAIL" if regressions else "GATE: PASS")
    sys.exit(1 if regressions else 0)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="mode", required=True)
    r = sub.add_parser("run")
    r.add_argument("cases")
    r.add_argument("--cmd", required=True)
    r.add_argument("--trials", type=int, default=3)
    r.add_argument("--timeout", type=int, default=120)
    r.add_argument("--out")
    c = sub.add_parser("compare")
    c.add_argument("baseline")
    c.add_argument("candidate")
    c.add_argument("--tolerance", type=float, default=0.0, help="allowed per-case pass-rate drop")
    args = ap.parse_args()
    run(args) if args.mode == "run" else compare(args)


if __name__ == "__main__":
    main()
