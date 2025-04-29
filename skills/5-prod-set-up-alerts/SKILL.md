---
name: 5-prod-set-up-alerts
description: Use when setting up or reviewing production monitoring, dashboards, or paging for an agent or LLM feature - "alert us when it goes bad", "we found out from customers", "the pager cries wolf", "what should we monitor?", or after an incident that no alert caught. Not for one-off offline evals (use 2-build-create-evals) or an outage happening now (use 6-fix-roll-back-agent).
---

# 5-prod-set-up-alerts

## Overview
Agents fail quietly (wrong answers at normal latency, tool loops burning money), so monitor **outcome and cost signals**, not just uptime. Thresholds were tuned on past incidents, so a backtest is weak evidence: prove rules catch unseen regressions by **synthetic injection**.

## Steps

1. **One signal per failure mode**, each request logged with `version`:
   - Wrong answers: quality proxy (human override, thumbs-down, reopen, escalation) or 5-prod-measure-quality scores.
   - Loops / runaway: count of requests with tool_calls ≥ N; cost per request.
   - Breakage: error rate, tool-error rate.
   - Dead agent or logging: silence (none for X minutes).
   - Dead **label feed**: freshness (no proxy values for X minutes), else the quality rule goes quiet.
   - Slow: p95 latency (ticket unless user-blocking).
2. **Write `rules.json`** (format: `alert_backtest.py --help`).
   - Rate rules page on the **95% lower bound** with a `min_n` (2-request night buckets are noise).
   - `lag_min` for late labels; `confirm: 2`; `severity: ticket` for anything that shouldn't wake someone.
   - Agree a **false-page budget** up front (e.g. ≤1/week).
3. **Backtest on history** (CAUGHT/MISSED, time to page, false pages/week):
   ```bash
   python3 -I <this-skill-dir>/alert_backtest.py backtest rules.json requests.jsonl --incidents incidents.json
   ```
4. **Held-out check: inject** the smallest regression you need to catch, one per failure mode (e.g. `cost_usd=x3`). Exits 1 below 80% detection.
   ```bash
   python3 -I <this-skill-dir>/alert_backtest.py inject rules.json requests.jsonl --incidents incidents.json --change human_override=rate:+0.15 --minutes 90
   ```
   - Check **which hours** it misses (usually low-traffic). Fix with a longer-window night rule or lower `min_n`, then re-run **both** backtest and inject (fixes cost false pages).
   - Never shrink the injected size until it passes; if the catchable size exceeds the one you care about, say so.
   - Traffic too thin for a rate rule: count individual extreme requests or add a denser quality signal, else accept and state the delay.
   - `--minutes` must exceed `window_min` + `lag_min` + confirm steps, or detection is capped by construction.
5. **Wire it up.** Schedule the same rules file. Page links the runbook (6-fix-roll-back-agent) and example request ids. Re-baseline thresholds and re-run step 4 after any model or prompt release.

## Report
Per rule: backtest result, inject detection % and median delay, false pages per week; **blind spots** (hours or sizes missed); **what isn't wired** (e.g. pager key); and, if so, that **thresholds were tuned on the same incidents they were checked against**.

## Common mistakes
- Only uptime/latency/errors: add quality proxy and cost.
- "Backtest pages on both incidents ✓": tuned on them; inject.
- Raw hourly rates: lower bound, `min_n`, confirm.
- Delayed label: `lag_min` + freshness rule.
- No per-version split: canary regressions get averaged away.
