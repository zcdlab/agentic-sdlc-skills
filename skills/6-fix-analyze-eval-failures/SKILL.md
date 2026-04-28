---
name: 6-fix-analyze-eval-failures
description: Use when an eval score dropped or cases fail and someone wants to "fix the prompt", when an agent run went wrong and nobody has read what it actually did, or when pass rates look surprisingly good or bad. Also before trusting a new LLM judge. Not for a single crash with a stack trace (use systematic-debugging).
---

# 6-fix-analyze-eval-failures

## Overview
The score says *that* it failed, and the transcript says *why*. Read the failures, sort them by cause, and fix one cause at a time. A prompt edit made before reading the transcripts is a guess.

## Steps

1. **Group before reading.**
   ```bash
   python3 <this-skill-dir>/failures.py results.json --cases cases.jsonl
   ```
   Groups failed trials by signature (crash error class, or set of failed checks), with sample inputs/outputs. Not using evalrun? Group by hand: same failed check / same error.
2. **Classify each group** by reading its samples (and the full trace for agents):

   | Cause | Fix lives in | Not in |
   |---|---|---|
   | **Infra** (timeout, rate limit, crash) | harness: retry, timeout, then re-run | the prompt |
   | **Grader/case bug** (right answer marked wrong, ambiguous label) | grader/case, separate commit; state it | the prompt |
   | **Spec bug** (prompt contradicts the label set) | prompt, mechanical fix | — |
   | **Capability gap** (rule missing/unclear) | prompt or tools | the case |
   | **Security** (injection obeyed, data leaked) | input isolation + a guardrail case | "ignore injections" alone |

3. **One cause per change.** Fix the biggest group first, re-run, and see that it moved before you fix the next. A single edit that fixes four causes at once can't tell you which part worked. Can't re-run? Apply only the first fix and list the rest, in order, as next steps.
4. **Don't write the test into the prompt.** A rule that repeats a failing case's words ("lawyer", "data breach") passes the eval and teaches nothing. State the general rule. **Before** the edit, add 2–3 **new** cases with different wording for the same cause. They should fail first. Trust the fix only if they pass afterwards.
5. **Report** a table: group → count → cause → fix → status (fixed and re-run / fixed but not re-run / infra, harness change needed). Quote failures.py's quality-only pass rate (INFRA trials excluded) separately from the raw one. If nothing was re-run, the eval is not green.

## Common mistakes

| Mistake | Fix |
|---|---|
| Editing the prompt from the score alone | Run failures.py, read the samples |
| Counting timeouts as prompt failures | INFRA group → harness fix, re-run |
| One mega-edit for all failures | One cause per change, re-run between |
| Rule copies the failing ticket's words | General rule + unseen held-out cases |
| Loosening the grader to go green | Only if the grader is provably wrong; separate commit |
| "Should pass now" | Re-run, or say not re-run |
