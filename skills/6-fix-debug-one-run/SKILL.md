---
name: 6-fix-debug-one-run
description: Use when one agent run went wrong but the code didn't crash - the agent picked the wrong tool, looped on the same call, hallucinated an argument, stopped early, or claimed "done" when it wasn't. Also when given a transcript and asked "what went wrong?". Not for many failing eval cases (use 6-fix-analyze-eval-failures) or code exceptions (use systematic-debugging).
---

# 6-fix-debug-one-run

## Overview
Find the **first wrong step**, put it in **one layer**, fix it there, and **make the run an eval case**.

## Steps

1. **Render the timeline.**
   ```bash
   python3 <this-skill-dir>/timeline.py transcript.jsonl
   ```
   Reads Claude Code session logs (`~/.claude/projects/*/*.jsonl`) and `claude -p --output-format stream-json` output (other formats: `--help`). Flags: ERR, REPEAT (same call again), UNSEEN (argument never seen - likely hallucinated), BIG (huge result may bury the fact), FAIL-OUTPUT, CLAIM (success claimed after a failure).
2. **Find the first divergence**, not the loudest symptom (the false "done" is usually the last bad step). Work back from the flags to the earliest step where the agent's belief diverged from what it had seen. Cite step numbers.
3. **Assign one layer** to that step:

   | Layer | Signs | Fix lives in |
   |---|---|---|
   | **Context** | right fact present but buried, truncated, or stale | trim/structure what the agent sees, compaction, notes |
   | **Tool** | unhelpful error, confusing params, huge output | tool definition or result (`2-build-review-tools`) |
   | **Prompt** | missing or conflicting instruction | system prompt or skill text |
   | **Harness** | nothing stopped a bad loop or false claim | deterministic check: retry cap, test-before-done gate |
   | **Grader** | run fine but marked bad | the eval |
   | **Model** | all above clear, still failed | model choice, or decompose the task |

   Tie-break: if the needed fact was in context, it's not **Model**. Pick the layer whose fix most cheaply prevents the step; list later bad steps from other layers. Prefer harness/tool fixes over prompt rules (no model compliance needed).
4. **Make it an eval case before fixing** in the 2-build-create-evals case format: `input` = the task, `--cmd` = script running the agent headless in a fresh fixture copy, printing outcome JSON (e.g. `{"version": "2.0.0", "tests_failed": 1, "claimed_success": false}`). Grade the outcome via `expect`, not step order. Add 1–2 variants (different file/wording). No eval set yet? Use `2-build-create-evals`.
5. **Fix, re-run the case ≥3 times, report:** first-divergence step, layer, fix, case id, re-run result ("not re-run" if you didn't).

## Common mistakes

| Mistake | Fix |
|---|---|
| Blaming the last step ("it lied") | Find the first divergence; earlier steps cause later ones |
| "Be careful" in the prompt | Harness gate or tool error first |
| Reading a 10k-line transcript raw | timeline.py; open only flagged steps |
| Fix without a case | Case first; it must fail before the fix |
| One-off checker scripts | Use timeline.py; extend it if a flag is missing |
