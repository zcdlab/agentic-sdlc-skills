#!/usr/bin/env python3
"""Find when a metric regressed and which change events line up with it.

  onset.py metrics.jsonl --metric success_rate [--weight requests] [--events deploy.log ...]
      [--deploy-pattern 'deploy (\\S+)'] [--target v1.3.0]

metrics.jsonl: one JSON object per time bucket with "ts" (ISO) and the metric (and optionally a weight field).
events: text logs whose lines start with an ISO timestamp (deploys, config/env changes, manual edits, upstream
service changes). Lines out of time order are flagged - the log itself may be wrong.

Prints the single most likely change point (the split that maximizes the weighted mean shift), the before/after
levels, the events between the last good bucket and the first bad one (the suspects), and the nearest events either
side. With --target, lists every non-deploy event since that version was last deployed: things the old version
never ran against, which a rollback to it must be checked against first.
"""
import argparse
import json
import re
from datetime import datetime

TS = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:?\d{2})?)\s*(.*)$")


def parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def change_point(xs, ws):
    best = None
    for i in range(2, len(xs) - 1):  # at least 2 buckets on each side
        a, b = xs[:i], xs[i:]
        wa, wb = ws[:i], ws[i:]
        ma = sum(x * w for x, w in zip(a, wa)) / sum(wa)
        mb = sum(x * w for x, w in zip(b, wb)) / sum(wb)
        sse = sum(w * (x - ma) ** 2 for x, w in zip(a, wa)) + sum(w * (x - mb) ** 2 for x, w in zip(b, wb))
        if best is None or sse < best[0]:
            best = (sse, i, ma, mb)
    return best


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("metrics")
    ap.add_argument("--metric", required=True)
    ap.add_argument("--weight", help="per-bucket weight field, e.g. requests")
    ap.add_argument("--events", nargs="*", default=[])
    ap.add_argument("--deploy-pattern", default=r"\bdeploy (\S+)", help="regex; group 1 = version")
    ap.add_argument("--target", help="rollback candidate version, as it appears in the deploy lines")
    a = ap.parse_args()

    rows = [json.loads(l) for l in open(a.metrics) if l.strip()]
    rows = sorted((r for r in rows if a.metric in r and "ts" in r), key=lambda r: parse_ts(r["ts"]))
    if len(rows) < 4:
        raise SystemExit("need at least 4 buckets with ts and the metric")
    xs = [float(r[a.metric]) for r in rows]
    ws = [float(r.get(a.weight, 1)) if a.weight else 1.0 for r in rows]
    sse, i, before, after = change_point(xs, ws)
    good, bad = parse_ts(rows[i - 1]["ts"]), parse_ts(rows[i]["ts"])
    noise = (sse / sum(ws)) ** 0.5  # bucket-to-bucket spread around the two levels
    snr = abs(after - before) / noise if noise else float("inf")
    print(f"{a.metric}: {before:.4g} before -> {after:.4g} after; "
          f"last good bucket {rows[i - 1]['ts']}, first bad bucket {rows[i]['ts']} ({i}/{len(rows)} buckets before)")
    print(f"  shift = {snr:.1f}x bucket noise" + ("" if snr >= 3 else
          " -> NO CLEAR STEP: this metric did not regress at one moment; don't hunt for a single cause in it"))
    if any(r.get("version") for r in rows):
        print(f"  versions in metrics: before {sorted({r.get('version') for r in rows[:i]})}, after {sorted({r.get('version') for r in rows[i:]})}")

    events = []
    for path in a.events:
        prev = None
        for n, line in enumerate(open(path), 1):
            m = TS.match(line.strip())
            if not m:
                continue
            t = parse_ts(m.group(1))
            if prev and t < prev:
                print(f"  WARN {path}:{n} is earlier than the line above it - clock skew or a bad log; don't trust its time")
            prev = t
            events.append((t, f"{path}:{n}", m.group(5)))
    events.sort()
    if events:
        sus = [e for e in events if good < e[0] <= bad]
        print("suspects (between last good and first bad bucket):" if sus else
              "suspects: NONE logged in that window - an unlogged change (manual edit, upstream, traffic mix)? run drift checks")
        for t, where, text in sus:
            print(f"  {t:%Y-%m-%dT%H:%M:%SZ}  {text}   [{where}]")
        earlier = [e for e in events if e[0] <= good][-3:]
        later = [e for e in events if e[0] > bad][:3]
        for label, es in (("before the window (metric was still fine after these)", earlier), ("after the window", later)):
            if es:
                print(f"{label}:")
                for t, where, text in es:
                    print(f"  {t:%Y-%m-%dT%H:%M:%SZ}  {text}   [{where}]")

    if a.target:
        dp = re.compile(a.deploy_pattern)
        last = max((e[0] for e in events if (m := dp.search(e[2])) and m.group(1) == a.target), default=None)
        how = "last deployed"
        live = [parse_ts(r["ts"]) for r in rows if r.get("version") in (a.target, a.target.lstrip("v"))]
        if live:  # metrics say when the target was actually serving; the events after that are new to it
            last, how = max(live), "last live in metrics at"
        if not last:
            print(f"target {a.target}: no deploy or metrics of it - can't tell what changed since it ran; check by hand")
        else:
            since = [e for e in events if e[0] > last and not dp.search(e[2])]
            print(f"target {a.target}: {how} {last:%Y-%m-%dT%H:%M:%SZ}; "
                  f"{len(since)} non-deploy change(s) since - it never ran against these:")
            for t, where, text in since:
                print(f"  {t:%Y-%m-%dT%H:%M:%SZ}  {text}   [{where}]")


if __name__ == "__main__":
    main()
