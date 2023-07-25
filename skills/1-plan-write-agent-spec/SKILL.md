---
name: 1-plan-write-agent-spec
description: Use before building or scaffolding any agent, LLM feature, or automation that acts on its own - "build an agent that...", "auto-file / auto-reply / auto-triage", "scaffold the agent loop", "add AI to X" - including when the user asks to start coding right away. Not for general product ideation with no clear task yet (use brainstorming).
---

# 1-plan-write-agent-spec

## Overview
A one-page contract written **before** the scaffold, so "working" has a number and the architecture is the simplest that can meet it.

## Steps

1. **Decide workflow or agent, first and explicitly.** Is the sequence of steps known in advance?
   - Fixed steps (classify → dedupe → file) → a **workflow**: code orchestrates, the LLM is called at fixed points with structured output. Cheaper, testable, can't wander.
   - Steps depend on what's found → an **agent** loop with tools.
   - Write down the reason. Default to the workflow; upgrade only if a fixed path provably can't do the task.
2. **Fill in the contract.** Put a proposed default in every cell, marked `(proposed)`. No blanks, no blocking.

   | Field | Content |
   |---|---|
   | Input | source, volume/day, format |
   | Output | exact schema; enums come from the real system, never invented |
   | Success | metric + threshold on a labeled set (e.g. precision ≥ 0.95, recall ≥ 0.8) |
   | Failure budget | which error is worse (false ticket vs missed incident) and how many per week are OK |
   | Autonomy | dry-run → human-approve → live, with the exit criteria for each stage |
   | Untrusted input | which inputs come from outside (Slack text, emails, web pages); keep them as data, never as instructions |
   | Hard guarantees | what must hold even if the model is wrong (no duplicates, no deletes). Enforce these **in code**, not in the prompt |
   | Budget | cost per item, latency, max turns/tool calls |
   | Eval set | where ~20–50 labeled real cases come from |
3. **Ask only for numbers and facts you can't look up** (thresholds, real enums, label source): ≤3 questions, and **don't wait for the answers**.
4. **Scaffold in the same turn**, against the `(proposed)` values: workflow skeleton or agent loop, dry-run default, hard guarantees as code checks. Save the contract as `CONTRACT.md` beside the code if you can write files. When answers arrive, update contract and code together. Run `2-build-create-evals` before any prompt tuning.

## Common mistakes

| Mistake | Fix |
|---|---|
| Agent loop because the user said "agent" | Decide by whether steps are fixed; say why |
| "Search first, don't duplicate" in the prompt | Dedupe check in code |
| "Working" = it ran and produced output | Metric + threshold on labeled data |
| Code first, contract "later" | Contract first, even if short and mostly `(proposed)` |
| Blocking on unknowns or waiting for answers | Propose a default, mark it, scaffold anyway |
