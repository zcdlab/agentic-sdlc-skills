---
name: 6-fix-roll-back-agent
description: Use when an agent or LLM feature in production is regressing right now - success rate dropped, customers complaining, bad outputs after a deploy - and someone says "roll it back", "revert to the last version", or "what broke prod?". Not for deciding whether a canary should be promoted (use 4-ship-gradual-rollout) or debugging one bad run (use 6-fix-debug-one-run).
---

# 6-fix-roll-back-agent

## Overview
"Roll back" is right only if the previous version is what changed and still works with today's dependencies. Regressions often come from outside the release (hand-edited prompt, env, model alias, upstream API). Spend ~2 minutes on evidence, restore the smallest thing undoing the cause.

## Steps

1. **When did it break, and what changed then?**
   ```bash
   python3 -I <this-skill-dir>/onset.py metrics.jsonl --metric success_rate --weight requests \
     --events deploy.log <config/env/upstream logs> --target <version the user wants back>
   ```
   "NO CLEAR STEP": the metric didn't break at one moment. **Suspects**: events between the last good and first bad bucket; events *before* it aren't the cause; `--target` lists changes that version never ran against; distrust out-of-order lines.
2. **Check drift** with 4-ship-pin-release's `bundle.py verify <repo>/release/<live>.json --root <deploy dir> --model "$MODEL" --param temperature=0`. Use the main-branch manifest (the deploy dir's `release/` may predate it). Whole bundle, not one file; mismatch means prod runs no release.
3. **Pick the restore by evidence, not the request:**
   - Drift in a live file/model/param: redeploy the **live** version's tag, or revert that input.
   - Onset is a deploy and nothing the target never ran against breaks it: previous version.
   - Target never ran against a change it depends on (tool API, schema, model): don't deploy it; fix forward or roll back that change with its owner.
   - Upstream change only: its owner rolls it back; the agent release stays.
   - No suspect in window: don't roll back blind; check unlogged changes and traffic mix; say so.

   If this differs from the ask, give one-line evidence; proceed only if yours is the *smaller, reversible* restore, else ask. Never deploy their target if it depends on something removed (step 1).
4. **Back up drifted files** *outside* the deploy dir before overwriting; they're evidence.
5. **Restore, then verify.** `bundle.py verify --root` must print OK. Recovery = **at least 3 buckets after the restore at the step 1 before-level**. Until then say "recovery not verified" with re-check time (restore + 3 buckets) and expected level; never "fixed". No recovery: suspect wrong; back to step 1, don't stack changes.
6. **Close the path.** Drift: `verify` at deploy/startup; ops prompt changes go via a release (4-ship-pin-release). Bad release: failing transcripts to 6-fix-debug-one-run, add an eval case, gate the next release (3-test-rerun-evals-before-merge).
7. **Report:** onset and suspects (log lines); `verify` before/after; restore and why (incl. divergence from ask); backup path; recovery status; follow-up owners.

## Common mistakes
"Deployed 08:00, broke today": check onset. Rolling back across an upstream change: `--target`. Diffing one file: `verify --root`. "Rolled back, fixed": verify OK plus >=3 recovered buckets. Overwriting hand-edits: back up.
