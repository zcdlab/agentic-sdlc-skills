#!/usr/bin/env python3
"""Online eval for an LLM feature: sample prod traffic, check the judge against humans, report a corrected daily score.

  online_eval.py sample    requests.jsonl --strata strata.json [--salt S] > sample.jsonl
  online_eval.py calibrate --labels human.jsonl --judge judge.jsonl --requests requests.jsonl \\
                           [--pred predicted] [--by version lang gold] > calibration.json
  online_eval.py report    graded.jsonl --calibration calibration.json [--by version]

sample: deterministic (sha256 of id+salt), stratified. strata.json is a list; the first matching stratum wins:
  [{"name": "legal_pred", "where": {"predicted": "legal"}, "rate": 1.0},
   {"name": "legal_words", "regex": {"ticket": "(?i)lawyer|sue|gdpr|legal"}, "rate": 1.0},
   {"name": "rest", "rate": 0.02}]
  Each sampled row gets _stratum and _weight (=1/rate), so oversampled strata don't skew the overall number.
  Emails, phone numbers and long digit runs are redacted from string fields - names and free-text PII are NOT.
calibrate: human labels {"request_id","gold"} + judge verdicts {"request_id","verdict": correct|incorrect}.
  Truth = predicted == gold. Reports how often the judge accepts correct outputs (Se) and rejects wrong ones (Sp),
  overall and per segment, and flags segments the judge was never checked on (few labels or few real errors).
report: graded rows (sample rows + "verdict"). Weighted judge pass rate per group, corrected for judge bias
  ((p + Sp - 1) / (Se + Sp - 1)), with a bootstrap 95% CI. PROVISIONAL when the judge isn't validated for that group.
"""
import argparse
import hashlib
import json
import random
import re
import sys

Z = 1.96
PII = [(re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "<EMAIL>"), (re.compile(r"\+?\d[\d\s().-]{7,}\d"), "<PHONE>"),
       (re.compile(r"\b\d{6,}\b"), "<NUM>")]
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?)?$")
ISO_IN = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?")
MIN_LABELS, MIN_ERRORS = 30, 10


def redact(v):
    """Redact PII but keep timestamps, including ones inside free text (the phone regex would eat them)."""
    kept = []
    v = ISO_IN.sub(lambda m: kept.append(m.group()) or f"\x00{len(kept) - 1}\x00", v)
    for pat, rep in PII:
        v = pat.sub(rep, v)
    return re.sub(r"\x00(\d+)\x00", lambda m: kept[int(m.group(1))], v)


def jl(path):
    return [json.loads(l) for l in open(path) if l.strip()]


def wilson(k, n):
    if not n:
        return (0.0, 1.0)
    p, d = k / n, 1 + Z * Z / n
    h = Z * (p * (1 - p) / n + Z * Z / (4 * n * n)) ** 0.5
    return (max(0.0, (p + Z * Z / (2 * n) - h) / d), min(1.0, (p + Z * Z / (2 * n) + h) / d))


def stratum(r, strata):
    for s in strata:
        if all(r.get(k) == v for k, v in s.get("where", {}).items()) and \
           all(re.search(p, str(r.get(k, ""))) for k, p in s.get("regex", {}).items()):
            return s
    return None


def cmd_sample(a):
    strata, counts = json.load(open(a.strata)), {}
    for r in jl(a.requests):
        s = stratum(r, strata)
        if not s:
            continue
        u = int(hashlib.sha256(f"{a.salt}:{r[a.id]}".encode()).hexdigest()[:12], 16) / 16 ** 12
        if u >= s["rate"]:
            continue
        out = {k: v for k, v in r.items()}
        for k, v in out.items():
            if isinstance(v, str) and k != a.id:
                out[k] = redact(v)
        out["_stratum"], out["_weight"] = s["name"], 1 / s["rate"]
        if isinstance(r.get(a.ts), str) and ISO.match(r[a.ts]):
            out["_day"] = r[a.ts][:10]  # for report --by version _day
        counts[s["name"]] = counts.get(s["name"], 0) + 1
        print(json.dumps(out))
    print("sampled per stratum: " + ", ".join(f"{k}={v}" for k, v in counts.items()) +
          " (regex redaction only: names/addresses/account ids in free text are NOT removed)", file=sys.stderr)


def agreement(rows):
    good = [r for r in rows if r["truth"]]
    bad = [r for r in rows if not r["truth"]]
    se_k, sp_k = sum(r["judge"] for r in good), sum(not r["judge"] for r in bad)
    return {"n": len(rows), "errors": len(bad), "se": se_k / len(good) if good else None, "se_ci": wilson(se_k, len(good)),
            "sp": sp_k / len(bad) if bad else None, "sp_ci": wilson(sp_k, len(bad)),
            "accepted_errors": [r["id"] for r in bad if r["judge"]],
            "validated": len(rows) >= MIN_LABELS and len(bad) >= MIN_ERRORS}


def cmd_calibrate(a):
    req = {r[a.id]: r for r in jl(a.requests)}
    judge = {r[a.id]: r["verdict"] == "correct" for r in jl(a.judge)}
    rows = []
    for l in jl(a.labels):
        r = req.get(l[a.id])
        if r is None or l[a.id] not in judge:
            print(f"skip {l[a.id]}: missing request or judge verdict", file=sys.stderr)
            continue
        rows.append({**r, "gold": l["gold"], "id": l[a.id], "truth": r[a.pred] == l["gold"], "judge": judge[l[a.id]]})
    out = {"overall": agreement(rows), "segments": {}}
    o = out["overall"]
    human = sum(r["truth"] for r in rows)
    print(f"overall: {len(rows)} labeled, {o['errors']} real errors; human-scored accuracy {human / len(rows):.1%}, "
          f"judge-scored {sum(r['judge'] for r in rows) / len(rows):.1%}", file=sys.stderr)
    for field in a.by:
        for val in sorted({str(r.get(field)) for r in rows}):
            seg = agreement([r for r in rows if str(r.get(field)) == val])
            out["segments"][f"{field}={val}"] = seg
    for name, s in [("overall", o)] + list(out["segments"].items()):
        fmt = lambda x: "  n/a" if x is None else f"{x:5.0%}"
        flag = "" if s["validated"] else f"  UNVALIDATED (need >={MIN_LABELS} labels and >={MIN_ERRORS} real errors)"
        acc = f"  judge passed wrong outputs: {', '.join(s['accepted_errors'])}" if s["accepted_errors"] else ""
        print(f"  {name:<22} n={s['n']:<4} errors={s['errors']:<3} Se={fmt(s['se'])} Sp={fmt(s['sp'])}{flag}{acc}", file=sys.stderr)
    print(json.dumps(out, indent=1))


def corrected(rows, se, sp):
    w = sum(r["_weight"] for r in rows)
    p = sum(r["_weight"] for r in rows if r["verdict"] == "correct") / w
    if se is None or sp is None or se + sp - 1 <= 0.05:
        return p, False
    return min(1.0, max(0.0, (p + sp - 1) / (se + sp - 1))), True


def cmd_report(a):
    cal = json.load(open(a.calibration))
    rows = [r for r in jl(a.graded) if r.get("verdict") in ("correct", "incorrect")]
    rng = random.Random(0)
    groups = {}
    for r in rows:
        groups.setdefault(tuple(str(r.get(f)) for f in a.by), []).append(r)
    for key, rs in sorted(groups.items()):
        name = ",".join(f"{f}={v}" for f, v in zip(a.by, key))
        seg = next((cal["segments"][f"{f}={v}"] for f, v in zip(a.by, key) if f"{f}={v}" in cal["segments"]), cal["overall"])
        source = "segment" if seg is not cal["overall"] else "overall (no segment calibration)"
        p, ok = corrected(rs, seg["se"], seg["sp"])
        boots = sorted(corrected(rng.choices(rs, k=len(rs)), seg["se"], seg["sp"])[0] for _ in range(400))
        n_eff = sum(r["_weight"] for r in rs) ** 2 / sum(r["_weight"] ** 2 for r in rs)
        prov = [] if seg["validated"] and ok else ["judge not validated for this group" if not seg["validated"] else
                                                   "judge too weak to correct"]
        if seg is cal["overall"]:
            prov.append("judge never checked on this group")
        print(f"{name:<24} {p:6.1%} [{boots[10]:.1%}, {boots[389]:.1%}]  n={len(rs)} (effective {n_eff:.0f}), "
              f"calibration: {source}" + (f"  PROVISIONAL: {'; '.join(prov)}" if prov else ""))
    print("(corrected = judge pass rate adjusted by its measured Se/Sp; CI covers sampling only, not calibration error)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample")
    s.add_argument("requests")
    s.add_argument("--strata", required=True)
    s.add_argument("--salt", default="online-eval")
    s.add_argument("--ts", default="ts", help="timestamp field, used to add _day")
    c = sub.add_parser("calibrate")
    c.add_argument("--labels", required=True)
    c.add_argument("--judge", required=True)
    c.add_argument("--requests", required=True)
    c.add_argument("--pred", default="predicted")
    c.add_argument("--by", nargs="*", default=["version"])
    r = sub.add_parser("report")
    r.add_argument("graded")
    r.add_argument("--calibration", required=True)
    r.add_argument("--by", nargs="*", default=["version"])
    for p in (s, c):
        p.add_argument("--id", default="request_id")
    a = ap.parse_args()
    {"sample": cmd_sample, "calibrate": cmd_calibrate, "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    main()
