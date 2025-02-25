---
name: 5-prod-reduce-cost-latency
description: Use when an agent or LLM feature is too expensive or too slow - "cut the cost per run", "finance says the bill doubled", "why does each request take 45 seconds?", "make it faster", "should we use a smaller model?" - or before choosing a model for a step. Not for context filling up within one long session (use 6-fix-context-bloat) or alerting on cost spikes (use 5-prod-set-up-alerts).
---

# 5-prod-reduce-cost-latency

## Overview
Cost is dominated by **tokens re-sent on every call** (growing history, big tool results, uncached prefix); latency by serial steps. Measure per step from real traces, fix the largest waste first, claim a saving only when after-traces show it **and** quality held.

## Steps

1. **Measure; don't trust the quoted number.**
   ```bash
   python3 -I <this-skill-dir>/profile_runs.py traces.jsonl --prices prices.json
   ```
   - Prices come from the provider's price page, dated; never from memory. Offline: repo-documented prices, labeled "unverified, from <file>".
   - Trace format and outputs: `--help`. No per-step traces: add logging first.
   - If someone's number ("$0.90/run") differs from traces, say so and work from the traces. It may come from another window, traffic mix or version: ask where; don't average the two.
   - `WARN logged cost_usd differs` means prices or token accounting are wrong. Re-run with `--input-includes-cache`; if the warning clears, use that run, else check prices. Quote nothing until computed and logged cost agree.
2. **Read the ranked waste** (each has an estimated saving):

   - Cache written, never read: byte-identical prefix (move timestamps/ids out of the system prompt, stable tool order). Risk none.
   - Large tool result re-sent: tool returns top-k, summaries or ids; drop stale results. **Risk: answer may be in what you cut.**
   - Serial tool calls in one step: parallelize; independent only if no call uses another's result and none writes. Risk none if truly independent.
   - Tiny-output step on big model: smaller model for that step. **Risk.**
   - Long outputs: `max_tokens`, terser format. Some risk.

3. **Change one lever at a time**, no-risk first. Risky changes: fix at the source (top-k inside the tool, not blind truncation) and put behind a flag.
4. **Gate risky changes on quality** with 3-test-rerun-evals-before-merge, on the pinned model. **Can't run evals?** They don't ship: say "not eval-verified" and hand over the exact command. "Ship it today" doesn't change this. No-risk fixes (caching, parallelism) take normal review.
5. **Prove the saving.** Canary (4-ship-gradual-rollout), collect traces, run `profile_runs.py before.jsonl --prices ... --compare after.jsonl`. Report the *measured* mean and p95 change; until then write "estimated".

## Report
Measured baseline (mean, p95, cost, time, mismatch with quoted number); ranked findings; changes with quality risk; eval status per risky change; estimated vs measured saving, labeled.

## Common mistakes
Optimizing the quoted number: re-measure. Prices from memory: dated price page. Smaller model everywhere: per step, where evals hold. Truncating tool output: top-k at source plus eval gate. "Cut cost 76%" from a model: `--compare` real traces. Mean only: p95 too; users feel the tail.
