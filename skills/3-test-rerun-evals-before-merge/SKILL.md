---
name: 3-test-rerun-evals-before-merge
description: Use when a change touches LLM-driven behavior that already has an eval set - prompt edit, model bump, tool/schema change, skill edit, temperature or retrieval change - and someone wants to merge, approve, or says "tried it on a few examples, looks good". Also when asked whether to update an eval baseline. Not for creating the first eval set (use 2-build-create-evals).
---

# 3-test-rerun-evals-before-merge

## Overview
A spot check shows the change isn't broken; the gate shows **nothing that used to pass now fails**.

## Steps

1. **Find the real eval command.** Read README, Makefile, or CI before guessing; else use `evalrun.py` from the 2-build-create-evals skill dir. Note case count and whether `cases.jsonl` changed since the baseline (`git log -- <evals dir>`).
2. **Re-baseline on the old code, this session.** Last week's `baseline.json` may differ in endpoint, grader, or case set. Same command, same `--trials`, on `main`:
   ```bash
   python3 <eval-first-dir>/evalrun.py run cases.jsonl --cmd "..." --trials 3 --out /tmp/base.json   # on main
   python3 <eval-first-dir>/evalrun.py run cases.jsonl --cmd "..." --trials 3 --out /tmp/cand.json   # on branch
   python3 <eval-first-dir>/evalrun.py compare /tmp/base.json /tmp/cand.json
   ```
   Before running, state call count: cases × trials × runs (2, or 3 with step 3). `compare` exits 1 on any per-case drop. Keep `--tolerance` at default 0 unless the owner agreed a number beforehand. `case sets differ` means those cases weren't compared: treat as a case-set change.
3. **Prompt and model both changed:** add a third run changing only one (e.g. new model + old prompt), compared to base, to attribute regressions.
4. **Read the gate per case, not the headline.** `GATE: FAIL` = a case's pass rate dropped. For each `REGRESSED` line, read its transcripts in `cand.json` and classify:
   - **Real loss:** block, or fix prompt and re-run.
   - **Grader or case bug:** fix in a separate commit, re-run both sides.
   - **Accepted tradeoff:** non-critical cases only, listed by id in the PR with the owner's sign-off.
   - Critical cases (safety, escalation, money, anything asymmetric) are never accepted tradeoffs. If unmarked in the case file, say which you treated as critical.
5. **Distrust small moves.** With 3 trials, a one-trial shift (2/3 → 3/3) is noise. Re-run only IMPROVED cases once; they count only if they hold. Improvements never offset a regression.
6. **Report:** verbatim `compare` output, per-case classification, latency delta, cost delta (provider usage, or "not measured"), verdict: approve / approve-with-listed-regressions / block.
7. **Update the baseline** only in the same PR, after review accepts, regenerated with the step-2 command (evalrun stores it as `cmd` in the JSON). Never hand-edit it or update it to turn FAIL into PASS.

## Common mistakes
- "Looks great on 3 examples": full set, both sides, same trials.
- Stale baseline: re-run main now.
- Score rose, merge: classify every REGRESSED line.
- Prompt + model changed together: single-variable run.
- Cases and prompt edited together: separate commit, re-baselined.
- Refreshing baseline.json to go green: only after accepted review.
- Guessing `make eval`: read the repo.
