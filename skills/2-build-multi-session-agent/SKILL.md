---
name: 2-build-multi-session-agent
description: Use when an agent will work on one project across many sessions or context windows (hours or days) - "build the whole app on its own", "keep going tomorrow", "it keeps hitting context limits", "each new session starts from scratch", or an agent that marks work done that isn't, or redoes finished work. Not for a task that fits in one session.
---

# 2-build-multi-session-agent

## Overview
Agents declare unfinished features done, rewrite plans to fit what they built, leave half-built work, and forget across sessions. Use **state in files they can't quietly corrupt** (JSON feature list + guard script) and **an orientation routine**.

## Set up (once)

1. **Feature list:**
   ```bash
   python3 -I <this-skill-dir>/features_guard.py init spec_items.txt > features.json
   ```
   Fill in every `test`; each must check the feature **end to end** via the real interface (helper unit tests don't count).
   **Write the e2e test files now, in this session** (e.g. `tests/e2e/`); all fail for now. User reviews `features.json` and tests; commit. Feature sessions may not edit this contract; no agent writes the test gating its own work.
2. **Guard in code.** `features_guard.py check features.json` fails on any change besides `passes` or a `passes: true` feature failing its test now (details: `--help`). Copy the script in; add a **repo-local** pre-commit hook:
   ```bash
   mkdir -p tools .githooks && cp <this-skill-dir>/features_guard.py tools/
   printf '#!/bin/sh\npython3 -I tools/features_guard.py check features.json --staged --protect tests/e2e\n' > .githooks/pre-commit
   chmod +x .githooks/pre-commit && git config core.hooksPath .githooks
   ```
3. **`init.sh`**: one command that sets up and proves a green baseline: re-apply `git config core.hooksPath .githooks` (clones lose it), smoke test, guard. Add `.gitignore` for build output before the first commit.
4. **`PROGRESS.md`**: append-only; sessions add done, half-done, next step.
5. **CLAUDE.md session protocol:**
   - Orient: `git log --oneline -20`, read PROGRESS.md's end, run `./init.sh`, then `features_guard.py check`. Red? Fix first.
   - Take the **one** next feature, implement, run its `test`; set `passes: true` only after it passes. Commit (hook re-checks), append to PROGRESS.md.
   - Stop after one feature, or sooner if context is low. If unfinished: commit with `passes: false` and a "half-done:" note in PROGRESS.md. Never stop uncommitted. Never `--no-verify`.
   - Never edit a feature's description or test to make it pass; if wrong, note it in PROGRESS.md for the user.

**Per-session prompt:**
> Continue the project per the CLAUDE.md session protocol: orient first, finish exactly one feature from features.json, verified by its test, commit, update PROGRESS.md.

## Verify before handoff
- **Guard:** flip `passes` on an unbuilt feature and commit; edit a `test` and commit. The hook must refuse both. Restore after.
- **Handoff (optional; costs a model run, ask first):** fresh headless session, the prompt, `--max-turns 15`; stream-json should show orientation before edits.
- Report what you ran and saw; untested handoff: "not run".

## Common mistakes
Markdown checklist: JSON + guard. "Work till context runs out": one feature, clean commit. Unit-test acceptance: e2e `test`. Rules only in CLAUDE.md: hook enforces. Self-written e2e test: `--protect`. Skipped orientation: red baseline first.
