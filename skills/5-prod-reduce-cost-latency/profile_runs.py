#!/usr/bin/env python3
"""Profile where an agent's cost and time go, and rank the waste, from per-step traces.

  profile_runs.py traces.jsonl --prices prices.json [--compare after.jsonl]

traces.jsonl, one object per step:
  llm:  {"run_id", "step", "type": "llm", "name", "model", "start_s", "latency_s", "input_tokens",
         "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens"}
  tool: {"run_id", "step", "type": "tool", "tool", "start_s", "latency_s", "result_tokens"}
prices.json (USD per million tokens - take these from your provider's price page, don't guess):
  {"<model id>": {"in": 15, "out": 75, "cache_write": 18.75, "cache_read": 1.5}}

Prints per-run mean / p50 / p95 cost and wall time, cost and time by step, then the waste findings with an
estimated saving each: cache never read, large tool results re-sent on every later call, serial tool calls
inside one step, and tiny-output steps on an expensive model. Estimates are not results - re-profile real
traces after the change (--compare) and pass the eval gate before claiming a saving.
"""
import argparse
import json
from collections import defaultdict


def load(path):
    runs = defaultdict(list)
    for l in open(path):
        if l.strip():
            r = json.loads(l)
            runs[r["run_id"]].append(r)
    for rs in runs.values():
        rs.sort(key=lambda r: r.get("start_s", 0))
    return runs


INCLUDES_CACHE = False  # set by --input-includes-cache


def cost(r, prices):
    p = prices.get(r.get("model"))
    if not p:
        raise SystemExit(f"no price for model {r.get('model')!r} in --prices")
    cw, cr = r.get("cache_creation_input_tokens", 0), r.get("cache_read_input_tokens", 0)
    fresh = r["input_tokens"] - cw - cr if INCLUDES_CACHE else r["input_tokens"]
    return (fresh * p["in"] + cw * p.get("cache_write", p["in"]) + cr * p.get("cache_read", p["in"])
            + r["output_tokens"] * p["out"]) / 1e6


def pct(xs, q):
    s = sorted(xs)
    return s[min(len(s) - 1, int(q * len(s)))]


def summary(runs, prices):
    c = [sum(cost(r, prices) for r in rs if r["type"] == "llm") for rs in runs.values()]
    t = [max(r["start_s"] + r["latency_s"] for r in rs) - min(r["start_s"] for r in rs) for rs in runs.values()]
    return c, t


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("traces")
    ap.add_argument("--prices", required=True)
    ap.add_argument("--compare", help="traces after the change")
    ap.add_argument("--big-result", type=int, default=5000, help="tool result tokens considered large")
    ap.add_argument("--input-includes-cache", action="store_true",
                    help="your logger's input_tokens already counts cache write/read tokens")
    a = ap.parse_args()
    global INCLUDES_CACHE
    INCLUDES_CACHE = a.input_includes_cache
    prices, runs = json.load(open(a.prices)), load(a.traces)
    n = len(runs)
    c, t = summary(runs, prices)
    logged = [r["cost_usd"] for rs in runs.values() for r in rs if r["type"] == "llm" and "cost_usd" in r]
    if logged:
        lg = sum(logged) / n
        if abs(lg / (sum(c) / n) - 1) > 0.05:
            print(f"WARN logged cost_usd ${lg:.3f}/run differs from computed ${sum(c) / n:.3f}/run by "
                  f"{lg / (sum(c) / n) - 1:+.0%} - check prices and whether input_tokens includes cached tokens "
                  "(--input-includes-cache); don't report savings until they agree")
    print(f"{n} runs: cost/run mean ${sum(c) / n:.3f} p50 ${pct(c, .5):.3f} p95 ${pct(c, .95):.3f}; "
          f"wall time mean {sum(t) / n:.1f}s p50 {pct(t, .5):.1f}s p95 {pct(t, .95):.1f}s")

    by_step, by_time = defaultdict(float), defaultdict(float)
    for rs in runs.values():
        for r in rs:
            key = r.get("name") or r.get("tool") if r["type"] == "llm" else f"tool:{r['tool']}"
            by_time[key] += r["latency_s"] / n
            if r["type"] == "llm":
                by_step[key] += cost(r, prices) / n
    tot_c, tot_t = sum(by_step.values()), sum(by_time.values())
    print("by step (per run):")
    for k in sorted(by_time, key=lambda k: -by_step.get(k, 0) - by_time[k] / 1e3):
        print(f"  {k:<22} ${by_step.get(k, 0):7.3f} ({by_step.get(k, 0) / tot_c:4.0%})   {by_time[k]:5.1f}s ({by_time[k] / tot_t:4.0%})")

    findings = []
    llm = [r for rs in runs.values() for r in rs if r["type"] == "llm"]
    reads = sum(r.get("cache_read_input_tokens", 0) for r in llm)
    writes = sum(r.get("cache_creation_input_tokens", 0) for r in llm)
    if writes and reads < 0.1 * writes:
        # what the written prefix would have cost as reads on every call after the first in each run
        save = sum((r.get("cache_creation_input_tokens", 0)) * (prices[r["model"]].get("cache_write", prices[r["model"]]["in"])
                   - prices[r["model"]].get("cache_read", prices[r["model"]]["in"])) / 1e6
                   for rs in runs.values() for r in [x for x in rs if x["type"] == "llm"][1:]) / n
        findings.append((save, 0, f"cache written {writes:,} tokens but read only {reads:,}: the cached prefix changes every call "
                         "(timestamps/ids/reordered tools at the top?) - make the prefix byte-identical"))
    for tool in sorted({r["tool"] for rs in runs.values() for r in rs if r["type"] == "tool"}):
        save = 0.0
        hits = 0
        for rs in runs.values():
            for i, r in enumerate(rs):
                if r["type"] == "tool" and r["tool"] == tool and r["result_tokens"] >= a.big_result:
                    hits += 1
                    later = [x for x in rs[i + 1:] if x["type"] == "llm"]
                    save += sum((r["result_tokens"] - a.big_result // 5) * prices[x["model"]]["in"] / 1e6 for x in later)
        if hits:
            findings.append((save / n, 0, f"tool:{tool} returned >= {a.big_result:,} tokens in {hits} calls, re-sent on every later LLM call "
                             f"- return top-k / summaries / ids (saving assumes ~{a.big_result // 5:,} tokens)"))
    serial = 0.0
    for rs in runs.values():
        steps = defaultdict(list)
        for r in rs:
            if r["type"] == "tool":
                steps[r["step"]].append(r)
        for ts in steps.values():
            if len(ts) > 1:
                span = max(x["start_s"] + x["latency_s"] for x in ts) - min(x["start_s"] for x in ts)
                serial += span - max(x["latency_s"] for x in ts)
    if serial / n > 0.5:
        findings.append((0, serial / n, "several tool calls in one step run one after another - run independent ones in parallel"))
    small = defaultdict(list)
    for r in llm:
        if r["output_tokens"] < 50:
            small[(r.get("name"), r["model"])].append(r)
    for (name, model), rs in small.items():
        if len(rs) >= n / 2:
            cheapest = min(prices, key=lambda m: prices[m]["in"])
            if cheapest != model:
                save = sum(cost(r, prices) - cost({**r, "model": cheapest}, prices) for r in rs) / n
                findings.append((save, 0, f"step '{name}' emits <50 tokens on {model}: try {cheapest} - only if the eval gate passes"))

    print("waste, ranked by estimated saving per run (ESTIMATES - verify with --compare and the eval gate):")
    for save, secs, msg in sorted(findings, key=lambda f: -(f[0] / max(tot_c, 1e-9) + f[1] / max(tot_t, 1e-9))):
        est = " ".join(x for x in (f"~${save:.3f}" if save else "", f"~{secs:.1f}s" if secs else "") if x)
        print(f"  {est:<14} {msg}")
    if not findings:
        print("  none of the known patterns found")
    elif len(findings) > 1:
        print("  (each estimate assumes the others weren't done - they overlap, don't add them up; --compare measures the total)")

    if a.compare:
        after = load(a.compare)
        c2, t2 = summary(after, prices)
        m = len(after)
        print(f"after ({m} runs): cost/run mean ${sum(c2) / m:.3f} ({sum(c2) / m / (sum(c) / n) - 1:+.0%}) p95 ${pct(c2, .95):.3f}; "
              f"wall mean {sum(t2) / m:.1f}s ({sum(t2) / m / (sum(t) / n) - 1:+.0%}) p95 {pct(t2, .95):.1f}s"
              + ("  (fewer than 20 runs - noisy)" if m < 20 else ""))


if __name__ == "__main__":
    main()
