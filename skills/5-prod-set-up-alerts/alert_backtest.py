#!/usr/bin/env python3
"""Replay alert rules over per-request logs: what would have paged, and would it catch regressions it wasn't tuned on.

  alert_backtest.py backtest rules.json requests.jsonl [--incidents incidents.json]
  alert_backtest.py inject   rules.json requests.jsonl --change human_override=rate:+0.15 [--change cost_usd=x3]
                             [--minutes 90] [--trials 30] [--incidents incidents.json] [--seed 0]

rules.json:
  {"step_min": 5, "confirm": 2,            # evaluate every 5 min; page only after 2 consecutive breaching checks
   "rules": [
    {"name": "override", "type": "rate", "field": "human_override", "window_min": 90, "lag_min": 15,
     "min_n": 40, "page_lb_above": 0.12},                     # Wilson 95% lower bound of the rate > limit
    {"name": "cost", "type": "mean", "field": "cost_usd", "window_min": 60, "min_n": 20, "page_above": 0.022},
    {"name": "loops", "type": "count", "where": {"tool_calls": {"ge": 8}}, "window_min": 60, "page_ge": 2},
    {"name": "silence", "type": "silence", "page_gap_min": 90},                 # no requests at all
    {"name": "labels", "type": "freshness", "field": "human_override", "page_gap_min": 120}]}  # feed went dead
  lag_min skips the newest minutes for labels that arrive late. Rules with "severity": "ticket" never page.
incidents.json: [{"start": ISO, "end": ISO, "name": "..."}] - known bad periods.

backtest: page episodes, each incident caught or missed with time-to-page, and false pages (outside incidents) per week.
inject:   copies the log, plants a synthetic regression of the given size in random windows OUTSIDE known incidents
          (rate:+d flips that share of rows to true; xF multiplies the field), and reports how often and how fast
          the rules page. This is the held-out check: thresholds tuned on past incidents prove nothing about new ones.
Exit 1 if any incident is missed or detection < 80% (inject); else 0.
"""
import argparse
import bisect
import json
import math
import random
import sys
from datetime import datetime, timedelta

Z = 1.96
WARNED = set()


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def wilson_lb(k, n):
    if n == 0:
        return 0.0
    p = k / n
    return (p + Z * Z / (2 * n) - Z * math.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n))) / (1 + Z * Z / n)


def matches(r, where):
    ops = {"ge": lambda a, b: a >= b, "gt": lambda a, b: a > b, "le": lambda a, b: a <= b,
           "lt": lambda a, b: a < b, "eq": lambda a, b: a == b}
    return all(r.get(f) is not None and ops[o](r[f], v) for f, c in where.items() for o, v in c.items())


def breach(rule, rows, times, now):
    """Return a message if the rule breaches at time `now`, else None."""
    t = rule["type"]
    if t in ("silence", "freshness"):
        i = bisect.bisect_right(times, now) - 1
        while t == "freshness" and i >= 0 and rows[i].get(rule["field"]) is None:
            i -= 1
        gap = (now - times[i]).total_seconds() / 60 if i >= 0 else float("inf")
        return f"no {'requests' if t == 'silence' else rule['field'] + ' values'} for {gap:.0f} min" \
            if gap > rule["page_gap_min"] else None
    end = now - timedelta(minutes=rule.get("lag_min", 0))
    lo, hi = bisect.bisect_left(times, end - timedelta(minutes=rule["window_min"])), bisect.bisect_right(times, end)
    win = rows[lo:hi]
    if t == "count":
        k = sum(matches(r, rule["where"]) for r in win)
        return f"{k} matching requests in {rule['window_min']}m" if k >= rule["page_ge"] else None
    vals = [r[rule["field"]] for r in win if r.get(rule["field"]) is not None]
    if len(vals) < rule.get("min_n", 1):
        return None
    if t == "rate":
        k, n = sum(bool(v) for v in vals), len(vals)
        lb = wilson_lb(k, n)
        return f"{k}/{n}={k / n:.0%} (lower bound {lb:.0%})" if lb > rule["page_lb_above"] else None
    if t == "mean":
        m = sum(vals) / len(vals)
        return f"mean {m:.4g} over {len(vals)}" if m > rule["page_above"] else None
    raise SystemExit(f"unknown rule type {t}")


def episodes(cfg, rows):
    times = [ts(r["ts"]) for r in rows]
    step, confirm = timedelta(minutes=cfg.get("step_min", 5)), cfg.get("confirm", 2)
    out = []
    for rule in cfg["rules"]:
        fields = [rule["field"]] if "field" in rule else list(rule.get("where", {}))
        for f in fields:
            if f not in WARNED and not any(f in r for r in rows):
                WARNED.add(f)
                print(f"WARN rule {rule['name']}: field '{f}' is in no log row - this rule can never fire", file=sys.stderr)
        now, streak, open_ep = times[0] + step, 0, None
        while now <= times[-1] + step:
            msg = breach(rule, rows, times, now)
            streak = streak + 1 if msg else 0
            if streak >= confirm and not open_ep:
                open_ep = {"rule": rule["name"], "severity": rule.get("severity", "page"), "start": now, "msg": msg}
                out.append(open_ep)
            if not msg and open_ep:
                open_ep["end"], open_ep = now, None
            now += step
    return sorted(out, key=lambda e: e["start"])


def load(path):
    rows = [json.loads(l) for l in open(path) if l.strip()]
    return sorted((r for r in rows if r.get("ts")), key=lambda r: r["ts"])


def overlaps(e, inc, slack):
    end = e.get("end", e["start"])
    return e["start"] <= inc["end"] + slack and end >= inc["start"]


def cmd_backtest(a, cfg, rows):
    incs = [{**i, "start": ts(i["start"]), "end": ts(i["end"])} for i in json.load(open(a.incidents))] if a.incidents else []
    eps = episodes(cfg, rows)
    days = (ts(rows[-1]["ts"]) - ts(rows[0]["ts"])).total_seconds() / 86400
    slack = timedelta(minutes=60)
    pages = [e for e in eps if e["severity"] == "page"]
    for e in eps:
        print(f"  {e['severity'].upper():<6} {e['rule']:<12} {e['start']:%Y-%m-%dT%H:%MZ}  {e['msg']}")
    missed = 0
    for i in incs:
        hit = [e for e in pages if overlaps(e, i, slack)]
        if hit:
            d = (min(e["start"] for e in hit) - i["start"]).total_seconds() / 60
            print(f"CAUGHT {i.get('name', i['start'])}: first page {d:.0f} min after start ({hit[0]['rule']})")
        else:
            missed += 1
            print(f"MISSED {i.get('name', i['start'])}: no page")
    false = [e for e in pages if not any(overlaps(e, i, slack) for i in incs)]
    print(f"{len(pages)} pages over {days:.1f} days; {len(false)} outside known incidents "
          f"= {len(false) / days * 7:.1f} false pages/week" + ("" if incs else " (no --incidents: all pages counted)"))
    return 1 if missed else 0


def cmd_inject(a, cfg, rows):
    rng = random.Random(a.seed)
    incs = [(ts(i["start"]), ts(i["end"])) for i in json.load(open(a.incidents))] if a.incidents else []
    t0, t1 = ts(rows[0]["ts"]), ts(rows[-1]["ts"])
    dur = timedelta(minutes=a.minutes)
    changes = []
    for c in a.change:
        field, how = c.split("=", 1)
        changes.append((field, how))
    delays, missed, tried = [], [], 0
    base_pages = {(e["rule"], e["start"]) for e in episodes(cfg, rows) if e["severity"] == "page"}
    while tried < a.trials:
        s = t0 + timedelta(seconds=rng.uniform(0, (t1 - t0 - dur).total_seconds()))
        if any(s < ie + timedelta(hours=2) and s + dur > is_ - timedelta(hours=2) for is_, ie in incs):
            continue  # keep clear of real incidents so they don't get the credit
        tried += 1
        new = []
        for r in rows:
            r2 = dict(r)
            if s <= ts(r["ts"]) < s + dur:
                for field, how in changes:
                    if how.startswith("rate:+") and not r2.get(field) and rng.random() < float(how[6:]):
                        r2[field] = True
                    elif how.startswith("x") and isinstance(r2.get(field), (int, float)):
                        r2[field] = r2[field] * float(how[1:])
            new.append(r2)
        hit = [e for e in episodes(cfg, new) if e["severity"] == "page" and (e["rule"], e["start"]) not in base_pages
               and s <= e["start"] <= s + dur + timedelta(minutes=60)]
        if hit:
            delays.append((min(e["start"] for e in hit) - s).total_seconds() / 60)
        else:
            missed.append(s)
    rate = len(delays) / tried
    delays.sort()
    print(f"injected {' '.join(a.change)} for {a.minutes} min, {tried} random windows outside known incidents")
    print(f"  detected {len(delays)}/{tried} = {rate:.0%}" + (f"; time to page median {delays[len(delays) // 2]:.0f} min, "
          f"worst {delays[-1]:.0f} min" if delays else ""))
    for s in missed[:5]:
        print(f"  missed window starting {s:%Y-%m-%dT%H:%MZ} (hour {s.hour:02d} UTC)")
    return 1 if rate < 0.8 else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["backtest", "inject"])
    ap.add_argument("rules")
    ap.add_argument("requests")
    ap.add_argument("--incidents")
    ap.add_argument("--change", action="append", default=[], help="FIELD=rate:+0.15 or FIELD=x3")
    ap.add_argument("--minutes", type=int, default=90)
    ap.add_argument("--trials", type=int, default=30)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    cfg, rows = json.load(open(a.rules)), load(a.requests)
    if a.cmd == "inject" and not a.change:
        raise SystemExit("inject needs at least one --change")
    raise SystemExit((cmd_backtest if a.cmd == "backtest" else cmd_inject)(a, cfg, rows))


if __name__ == "__main__":
    main()
