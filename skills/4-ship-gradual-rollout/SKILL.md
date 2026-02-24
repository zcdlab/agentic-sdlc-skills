---
name: 4-ship-gradual-rollout
description: Use when shipping a new agent or LLM-feature version to production traffic - setting up a canary, shadow, or staged rollout, or deciding "promote or roll back?" from live metrics, e.g. "it's been on 5% for two hours, can we go to 100%?". Not for offline pre-merge checks (use 3-test-rerun-evals-before-merge) or an outage happening now (use 6-fix-roll-back-agent).
---

# 4-ship-gradual-rollout

## Overview
A canary catches what offline evals can't. Write the decision rule **before** seeing data, use confidence intervals (not point estimates), roll back automatically, widen only with human approval.

## Steps

1. **Pre-register** `canary_policy.json` before the canary starts (format: `--help`):
   - **Absolute SLOs:** success ≥, p95 ≤, tool-error ≤.
   - **Max regression vs control:** `max_drop`, `max_rise`, `max_ratio` (cost, latency).
   - **Minimum traffic:** `min_requests` and `min_minutes`, large enough for the CI to resolve the SLO margin (p≈0.9, ±2% needs ~900 requests).
   - **Stages**, e.g. shadow → 5% → 25% → 100%.

   No minimum in the rollout doc? Propose one, labeled proposed. Canary already running without a policy? Write it now from only the rollout doc and `--help` defaults, *not* values tuned to data seen; label it "written after start".
2. **Same bundle, same logging.** Canary and control each run a verified release (`4-ship-pin-release verify`), logging one row per request: `version`, `ts`, metric fields. Route randomly, not by tier/region (different mix); if possible log a request-type field.
3. **Shadow first** for agents that write/send: run the new version on live-request copies, tools stubbed or dry-run; compare offline, then go live.
4. **Decide with the script each check interval:**
   ```bash
   python3 -I <this-skill-dir>/canary_decide.py requests.jsonl --policy canary_policy.json --canary 1.4.0 --control 1.3.0
   ```
   Checks are PASS (whole 95% CI inside limit), FAIL (whole CI outside), or INCONCLUSIVE. Verdict:
   - **ROLLBACK** (exit 1) if anything fails
   - **HOLD** (exit 3) if anything is inconclusive or traffic is too thin
   - **PROMOTE-NEXT-STAGE** (exit 0) only if everything passes
   - Exit 2 is bad input (wrong version, field not in logs): fix, re-run; not a verdict.

   Wire exit 1 to **automatic** rollback (`6-fix-roll-back-agent`). On exit 0 ask a human to widen; never auto-widen.
5. **Don't move the goalposts**, even if the user or a deadline offers: no changing thresholds, dropping a failing metric, or excluding a "warm-up hour" post-data. If the policy was wrong, write a new one for the *next* canary, and say so.
6. **Report:** verdict line and per-check table verbatim; traffic, duration; caveats (e.g. mix unchecked, tool-error CI assumes independent calls); next action. On ROLLBACK, run `6-fix-debug-one-run` on failing canary transcripts; each new failure mode becomes an eval case.

## Common mistakes

| Mistake | Fix |
|---|---|
| Point estimate vs SLO | CI vs SLO; inconclusive = HOLD |
| Only absolute SLOs | Also compare to control (cost, escalation, latency) |
| "Looks fine" at 50 requests | `min_requests` in the policy |
| Auto-promote | Auto-rollback; promotion needs a human |
| Tweaking thresholds to pass | Pre-registered policy; changes go to the next canary |
