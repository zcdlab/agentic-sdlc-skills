---
name: 2-build-review-tools
description: Use when adding, changing, or reviewing tools for an agent or an MCP server - names, descriptions, parameters, return payloads, errors - or when an agent picks the wrong tool, passes bad arguments, floods its context with tool output, or does something destructive it shouldn't. Not for the agent's system prompt or skill text (use writing-skills).
---

# 2-build-review-tools

## Overview
Wrong tool choices are usually interface bugs. Lint mechanically, review judgment calls by hand, and **prove the change with a tool-choice eval**. A rewritten description is a hypothesis until the eval runs.

## Steps

1. **Lint.**
   ```bash
   python3 <this-skill-dir>/lint_tools.py tools.json   # API or MCP form; exit 1 on HIGH
   ```
   Catches generic/overlapping names, thin descriptions, ambiguous params, JSON-in-strings, unbounded list tools, unguarded destructive tools. Fix every HIGH.
2. **Review what the linter can't judge**, using real sample responses where you have them:
   - **Distinct purpose:** can a newcomer tell from descriptions alone which tool fits "close T-1" versus "delete T-1"? Merge or split until they can.
   - **High-signal returns:** only fields the agent will use, readable ids and names instead of opaque UUIDs and flags. Cap the size and say so (`truncated: true`).
   - **Actionable errors:** turn `Error 500` into a 4xx saying what was wrong and what valid input looks like.
   - **Poka-yoke:** typed params and enums from the real system. Destructive actions need `confirm`, **enforced server-side**, and a softer alternative named in the description.
   - **Mark assumptions.** Any return shape or behavior written into a description but not verified against the server goes in the report as unverified.
3. **Build a tool-choice eval (do it, don't just plan it).** Write 10–20 tasks, each with the expected first tool and key args. Include near-misses ("close" vs "delete", "find a user" vs "find tickets") and should-not cases (no destructive call). Use the `2-build-create-evals` runner with this skill's target:
   ```bash
   TOOLS_FILE=tools.json EVAL_MODEL=<id> python3 <eval-first-dir>/evalrun.py run cases.jsonl --cmd "python3 <this-skill-dir>/target_tool_choice.py" --trials 3 --out old.json
   ```
   Case: `{"id": "close", "input": "Close T-1", "expect": {"tool": "tickets_update", "args": {"ticket_id": "T-1"}}}`. Nested `expect` is a subset match; should-not cases expect `{"tool": "NONE"}`. Tools renamed? Pass `NAME_MAP=old_to_new.json` on the old run so the same cases grade both sets. Use your real system prompt via `EVAL_SYSTEM`. Calls = cases × trials × 2.
4. **Compare old vs new definitions** on the same cases (`3-test-rerun-evals-before-merge`). Report the correct-tool rate and the destructive-on-non-delete rate (trials on non-delete cases whose first call was destructive; target 0). If not run, write "not run".

## Common mistakes

| Mistake | Fix |
|---|---|
| Review by reading only | Lint + tool-choice eval |
| Safety rule in the description only | Enforce confirm/permissions in the server |
| Fixing the prompt for a tool's bad error | Fix the error message |
| Describing return shapes you haven't checked | Verify against the server or mark unverified |
| "Should pick the right tool now" | Old vs new eval numbers |
