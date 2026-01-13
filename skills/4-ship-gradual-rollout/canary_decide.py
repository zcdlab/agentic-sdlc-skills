#!/usr/bin/env python3
"""Decide PROMOTE / HOLD / ROLLBACK for a canary from per-request logs, with confidence intervals.

  canary_decide.py requests.jsonl --policy canary_policy.json --canary 1.4.0 --control 1.3.0

requests.jsonl: one JSON object per request with "version", "ts" (ISO) and the metric fields.
canary_policy.json (decide this BEFORE looking at the data):
  {"min_requests": 400, "min_minutes": 60,
   "metrics": {
     "success":   {"type": "rate",  "field": "success",    "min": 0.90, "max_drop": 0.02},
     "p95_ms":    {"type": "p95",   "field": "latency_ms", "max": 8000, "max_ratio": 1.2},
     "tool_err":  {"type": "ratio", "num": "tool_errors",  "den": "tool_calls", "max": 0.02, "max_ratio": 1.5},
     "cost":      {"type": "mean",  "field": "cost_usd",   "max_ratio": 1.15},
     "escalated": {"type": "rate",  "field": "escalated",  "max_rise": 0.02}}}
min/max = absolute SLO on the canary; max_drop/max_rise = allowed change vs control (rates);
max_ratio = allowed canary/control ratio. Each check uses a 95% CI: PASS if the whole CI is inside
the limit, FAIL if the whole CI is outside, else INCONCLUSIVE.
Verdict: ROLLBACK (exit 1) if any FAIL; HOLD (exit 3) if any INCONCLUSIVE or too little traffic;
PROMOTE-NEXT-STAGE (exit 0) only if every check PASSes. Promotion still needs human approval.
Bad input (missing file, unknown version, metric field absent from the logs) exits 2 - never 1, so a typo
can't trigger an automatic rollback.
"""
import argparse
import json
import math
import random
import sys
from datetime import datetime

Z = 1.96


def wilson(k, n):
    if n == 0:
        return 0.0, 0.0, 1.0
    p = k / n
    d = 1 + Z * Z / n
    c = (p + Z * Z / (2 * n)) / d
    h = Z * math.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n)) / d
    return p, max(0.0, c - h), min(1.0, c + h)


def p95(xs):
    s = sorted(xs)
    return s[min(len(s) - 1, int(0.95 * len(s)))]


def mean(xs):
    return sum(xs) / len(xs)


def boot(xs, stat, rng, n=400):
    return sorted(stat(rng.choices(xs, k=len(xs))) for _ in range(n))


def ci(vals):
    return vals[int(0.025 * len(vals))], vals[int(0.975 * len(vals)) - 1]


def bad_input(msg):
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(2)


def judge(lo, hi, limit, upper):
    """upper=True: value must stay <= limit."""
    if upper:
        return "PASS" if hi <= limit else "FAIL" if lo > limit else "INCONCLUSIVE"
    return "PASS" if lo >= limit else "FAIL" if hi < limit else "INCONCLUSIVE"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("requests")
    ap.add_argument("--policy", required=True)
    ap.add_argument("--canary", required=True)
    ap.add_argument("--control", required=True)
    a = ap.parse_args()
    pol = json.load(open(a.policy))
    arms = {a.canary: [], a.control: []}
    for line in open(a.requests):
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("version") in arms:
            arms[r["version"]].append(r)
    can, ctl = arms[a.canary], arms[a.control]
    rng = random.Random(0)
    rows, verdicts = [], []

    ts = [datetime.fromisoformat(r["ts"].replace("Z", "+00:00")) for r in can if r.get("ts")]
    minutes = (max(ts) - min(ts)).total_seconds() / 60 if ts else 0
    print(f"canary {a.canary}: {len(can)} requests over {minutes:.0f} min; control {a.control}: {len(ctl)} requests")
    if len(can) < pol.get("min_requests", 0) or minutes < pol.get("min_minutes", 0):
        verdicts.append("INCONCLUSIVE")
        print(f"  traffic below policy minimum ({pol.get('min_requests', 0)} requests, {pol.get('min_minutes', 0)} min) -> HOLD")
    if not can or not ctl:
        bad_input("no rows for one of the versions - check --canary/--control")

    for name, m in pol["metrics"].items():
        t = m["type"]
        if t in ("rate", "ratio"):
            def counts(rs):
                if t == "rate":
                    return sum(bool(r.get(m["field"])) for r in rs), len(rs)
                return sum(r.get(m["num"], 0) for r in rs), sum(r.get(m["den"], 0) for r in rs)
            (kc, nc), (kt, nt) = counts(can), counts(ctl)
            pc, lc, hc = wilson(kc, nc)
            pt, lt, ht = wilson(kt, nt)
            val = f"{pc:.2%} [{lc:.2%}, {hc:.2%}] vs control {pt:.2%}"
            d_lo = (pc - pt) - math.sqrt((pc - lc) ** 2 + (ht - pt) ** 2)  # Newcombe difference CI
            d_hi = (pc - pt) + math.sqrt((hc - pc) ** 2 + (pt - lt) ** 2)
            checks = []
            if "min" in m:
                checks.append((f">= {m['min']:.0%}", judge(lc, hc, m["min"], upper=False)))
            if "max" in m:
                checks.append((f"<= {m['max']:.0%}", judge(lc, hc, m["max"], upper=True)))
            if "max_drop" in m:
                checks.append((f"drop <= {m['max_drop']:.0%} (diff [{d_lo:+.2%}, {d_hi:+.2%}])", judge(d_lo, d_hi, -m["max_drop"], upper=False)))
            if "max_rise" in m:
                checks.append((f"rise <= {m['max_rise']:.0%} (diff [{d_lo:+.2%}, {d_hi:+.2%}])", judge(d_lo, d_hi, m["max_rise"], upper=True)))
            if "max_ratio" in m and pt > 0:
                r_lo, r_hi = lc / ht if ht else 0, hc / max(lt, 1e-9)  # conservative ratio bounds
                checks.append((f"ratio <= {m['max_ratio']} ([{r_lo:.2f}, {r_hi:.2f}])", judge(r_lo, r_hi, m["max_ratio"], upper=True)))
        else:
            stat = p95 if t == "p95" else mean
            xc = [r[m["field"]] for r in can if m["field"] in r]
            xt = [r[m["field"]] for r in ctl if m["field"] in r]
            if not xc or not xt:
                bad_input(f"metric {name!r}: field {m['field']!r} missing from every {'canary' if not xc else 'control'} row")
            bc, bt = boot(xc, stat, rng), boot(xt, stat, rng)
            lc, hc = ci(bc)
            ratios = sorted(c / t_ for c, t_ in zip(bc, rng.sample(bt, len(bt))) if t_)
            r_lo, r_hi = ci(ratios)
            val = f"{stat(xc):,.4g} [{lc:,.4g}, {hc:,.4g}] vs control {stat(xt):,.4g}"
            checks = []
            if "max" in m:
                checks.append((f"<= {m['max']:,}", judge(lc, hc, m["max"], upper=True)))
            if "min" in m:
                checks.append((f">= {m['min']:,}", judge(lc, hc, m["min"], upper=False)))
            if "max_ratio" in m:
                checks.append((f"ratio <= {m['max_ratio']} ([{r_lo:.2f}, {r_hi:.2f}])", judge(r_lo, r_hi, m["max_ratio"], upper=True)))
        for desc, v in checks:
            verdicts.append(v)
            rows.append((v, name, val, desc))

    for v, name, val, desc in rows:
        print(f"  {v:<12} {name:<12} {val:<44} {desc}")
    if "FAIL" in verdicts:
        print("\nVERDICT: ROLLBACK - a check failed with its whole 95% CI outside the limit")
        raise SystemExit(1)
    if "INCONCLUSIVE" in verdicts:
        print("\nVERDICT: HOLD - keep the canary share and collect more traffic (or roll back if any FAIL appears)")
        raise SystemExit(3)
    print("\nVERDICT: PROMOTE-NEXT-STAGE - every check passed; needs human approval to widen")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError) as e:  # bad files / policy: exit 2, not the ROLLBACK code
        bad_input(f"{type(e).__name__}: {e}")
