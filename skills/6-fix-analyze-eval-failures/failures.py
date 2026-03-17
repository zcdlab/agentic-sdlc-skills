#!/usr/bin/env python3
"""Group failed eval trials by signature so you read one transcript per cause, not every transcript.

  failures.py results.json [--cases cases.jsonl] [--samples 2]

results.json is evalrun.py output. Signature = the error class for crashed trials (non-zero exit:
INFRA, not a prompt problem), else the sorted set of failed checks. For each group: count, cases,
and sample outputs (with the case input if --cases is given).
"""
import argparse
import json
import re
from collections import defaultdict


def signature(trial):
    if trial["failed"] == ["exit==0"]:
        m = re.findall(r"([A-Za-z_][\w.]*(?:Error|Exception|Timeout)\w*)", trial.get("stderr") or "")
        return "INFRA " + (m[-1] if m else "non-zero exit")
    return "CHECK " + " & ".join(sorted(trial["failed"]))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("results")
    ap.add_argument("--cases")
    ap.add_argument("--samples", type=int, default=2)
    args = ap.parse_args()

    res = json.load(open(args.results))["results"]
    inputs = {}
    if args.cases:
        with open(args.cases) as f:
            for i, l in enumerate(x for x in f if x.strip() and not x.lstrip().startswith("#")):
                c = json.loads(l)
                inputs[c.get("id", str(i + 1))] = c["input"]

    groups = defaultdict(list)
    total = failed = 0
    for r in res:
        for t in r["trials"]:
            total += 1
            if not t["pass"]:
                failed += 1
                groups[signature(t)].append((r["id"], t))

    infra = sum(len(v) for k, v in groups.items() if k.startswith("INFRA"))
    print(f"{failed}/{total} trials failed; {infra} are INFRA (crash/timeout - fix the harness, not the prompt)")
    if total > infra:
        print(f"quality pass rate (INFRA excluded): {(total - failed) / (total - infra):.0%} of {total - infra} trials\n")
    for sig, rows in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        ids = sorted({cid for cid, _ in rows})
        print(f"## {sig}  ({len(rows)} trials, {len(ids)} cases: {', '.join(ids)})")
        seen = set()
        for cid, t in rows:
            if cid in seen or len(seen) >= args.samples:
                continue
            seen.add(cid)
            if cid in inputs:
                print(f"  [{cid}] input:  {str(inputs[cid])[:200]}")
            print(f"  [{cid}] output: {(t['output'] or t.get('stderr') or '')[:300]}")
        print()


if __name__ == "__main__":
    main()
