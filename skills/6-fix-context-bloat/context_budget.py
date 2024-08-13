#!/usr/bin/env python3
"""Measure where an agent's context goes, from a transcript's real token usage.

  context_budget.py transcript.jsonl [--top 8] [--files CLAUDE.md prompt.md ...]

Reads Claude Code session logs / `claude -p --output-format stream-json` output (assistant events
with message.usage). Context per model call = input + cache_creation + cache_read tokens (measured).
Reports: fixed overhead (first call's context: system prompt + tools + skills + memory), growth
over the run, compactions, the biggest single jumps and what entered the context just before them
(tool results, own output, injected text, attachments), tool-result share, and cache-read ratio.
Per-item sizes for tool results, attachments and --files are ESTIMATES (chars / 4).
"""
import argparse
import json
import os


def est(chars):
    return chars // 4


def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content if isinstance(b, dict))
    return ""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("transcript")
    ap.add_argument("--top", type=int, default=8)
    ap.add_argument("--files", nargs="*", default=[], help="static context files to size (estimate)")
    args = ap.parse_args()

    calls, seen_ids, names, results, compactions = [], set(), {}, [], []
    since_last, pending_own = [], []  # what entered the context since the previous model call
    with open(args.transcript) as f:
        for line in f:
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = ev.get("message") or {}
            if ev.get("type") == "system" and ev.get("subtype") == "compact_boundary":
                m = ev.get("compactMetadata") or {}
                compactions.append((len(calls), m.get("trigger", "?"), m.get("preTokens"), m.get("postTokens")))
            elif ev.get("type") == "attachment":
                a = ev.get("attachment") or {}
                since_last.append({"tool": f"attachment: {a.get('type', '?')}", "est": est(len(json.dumps(a)))})
            elif ev.get("type") == "assistant":
                for b in msg.get("content") or []:
                    if b.get("type") == "tool_use":
                        names[b.get("id")] = f"{b.get('name')} {json.dumps(b.get('input'))[:80]}"
                        pending_own.append({"tool": f"own call: {names[b.get('id')]}", "est": est(len(json.dumps(b.get("input"))))})
                u = msg.get("usage")
                mid = msg.get("id") or ev.get("uuid")
                if u and mid not in seen_ids:
                    seen_ids.add(mid)
                    ctx = u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                    if calls:  # previous call's whole output (incl. thinking, which logs store empty) is now context
                        since_last.append({"tool": "own output of previous call (output_tokens)", "est": calls[-1]["out"]})
                    calls.append({"ctx": ctx, "cache_read": u.get("cache_read_input_tokens", 0),
                                  "out": u.get("output_tokens", 0), "caused_by": since_last})
                    since_last, pending_own = pending_own, []  # own output enters the next call's context
            elif ev.get("type") == "user" and isinstance(msg.get("content"), str):
                since_last.append({"tool": "user/injected text", "est": est(len(msg["content"]))})
            elif ev.get("type") == "user" and isinstance(msg.get("content"), list):
                for b in msg["content"]:
                    if b.get("type") == "text":
                        since_last.append({"tool": "user/injected text (e.g. skill body)", "est": est(len(b.get("text", "")))})
                    if b.get("type") == "tool_result":
                        r = {"tool": names.get(b.get("tool_use_id"), "?"), "est": est(len(text_of(b.get("content"))))}
                        results.append(r)
                        since_last.append(r)

    if not calls:
        raise SystemExit("no assistant usage found - is this a Claude Code / stream-json transcript?")
    first, last = calls[0]["ctx"], calls[-1]["ctx"]
    peak = max(c["ctx"] for c in calls)
    total_in = sum(c["ctx"] for c in calls)
    print(f"model calls: {len(calls)}")
    print(f"fixed overhead (1st call context): {first:,} tokens  [measured]")
    pre1 = [r for r in calls[0]["caused_by"] if r["est"] > 0]
    if pre1:
        known = sum(r["est"] for r in pre1)
        print("  of which logged before call #1 [estimate]: " + ", ".join(f"{r['tool']} ~{r['est']:,}" for r in sorted(pre1, key=lambda r: -r["est"])[:5])
              + f"; the other ~{max(first - known, 0):,} is system prompt + tool schemas + anything not in the log")
    print(f"context at end: {last:,}   peak: {peak:,}  [measured]")
    bounds = [0] + [n for n, *_ in compactions] + [len(calls)]
    for k in range(len(bounds) - 1):
        seg = calls[bounds[k]:bounds[k + 1]]
        if seg:
            print(f"  segment {k + 1} (calls #{bounds[k] + 1}-#{bounds[k + 1]}): {seg[0]['ctx']:,} -> {seg[-1]['ctx']:,} (+{seg[-1]['ctx'] - seg[0]['ctx']:,} in {len(seg)} calls)  [measured]")
    for n, trig, pre, post in compactions:
        print(f"compaction after call #{n} ({trig}): {pre:,} -> {post:,} tokens  [measured; earlier turns now only a summary]"
              if pre and post else f"compaction after call #{n} ({trig})")
    drops = [(i + 1, calls[i - 1]["ctx"], calls[i]["ctx"]) for i in range(1, len(calls)) if calls[i]["ctx"] < 0.7 * calls[i - 1]["ctx"]]
    if drops and not compactions:
        print("context drops (likely compaction/clear): " + ", ".join(f"#{i} {a:,}->{b:,}" for i, a, b in drops))
    print(f"cache-read share of all input: {sum(c['cache_read'] for c in calls) / max(total_in, 1):.0%}  [measured]")
    tr = sum(r["est"] for r in results)
    print(f"tool results: {len(results)}, ~{tr:,} tokens total, ~{tr / max(last, 1):.0%} of final context  [estimate]")

    totals = {}
    for c in calls[1:]:
        for r in c["caused_by"]:
            if r["tool"].startswith("own call"):
                continue  # already inside the previous call's output_tokens
            kind = r["tool"] if r["tool"].startswith(("attachment", "own output", "user/")) else "tool results"
            totals[kind] = totals.get(kind, 0) + r["est"]
    print("\nwhat entered the context after call #1, by kind [estimate; includes content later compacted away;"
          "\n  an attachment type that never lines up with a jump may be log-only]:")
    for kind, n in sorted(totals.items(), key=lambda kv: -kv[1])[: args.top]:
        print(f"  ~{n:>8,}  {kind}")

    jumps = sorted(((calls[i]["ctx"] - calls[i - 1]["ctx"], i, calls[i]["caused_by"]) for i in range(1, len(calls))), reverse=True)
    print(f"\nbiggest context jumps (call #, +tokens measured, largest things that entered context just before it [estimate]):")
    for d, i, caused in jumps[: args.top]:
        if d <= 0:
            break
        why = "; ".join(f"{r['tool']} (~{r['est']:,})" for r in sorted(caused, key=lambda r: -r["est"])[:2]) or "(unattributed)"
        print(f"  #{i + 1:<4} +{d:,}  {why}")

    print(f"\nlargest tool results [estimate]:")
    for r in sorted(results, key=lambda r: -r["est"])[: args.top]:
        print(f"  ~{r['est']:>7,}  {r['tool']}")

    if args.files:
        print("\nstatic files [estimate]:")
        for p in args.files:
            print(f"  ~{est(os.path.getsize(p)):>7,}  {p}" if os.path.exists(p) else f"  missing  {p}")


if __name__ == "__main__":
    main()
