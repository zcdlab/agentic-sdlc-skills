---
name: 3-test-skill-triggering
description: Use when adding, renaming, or editing a skill's description, when two skills might overlap, or when the wrong skill (or no skill) fires for a request - e.g. "check the right skill fires", "does my skill trigger", "these skills collide".
---

# 3-test-skill-triggering

## Overview
A routing eval: real prompts → headless Claude → **observed** first `Skill` call. Reasoning about descriptions is a prediction, not a test.

## Steps

1. **Write expectations from intent, not from descriptions.** Decide which skill *should* own each prompt before re-reading descriptions; otherwise the test just restates the bug.
2. **Build the case set** (JSONL, ~12–20 cases) with three kinds, about a third each:
   - **Should fire** — plain phrasings of the skill's job.
   - **Near-miss** — phrasings that belong to a *sibling* skill (the overlap you're worried about).
   - **Should not fire** — trivial edits / general questions → `"NONE"`.
   An `"expect"` list accepts any listed skill — use it for routers (`["0-pick-skill", "simplify"]`) and plugin duplicates (`["requesting-code-review", "innerloop:requesting-code-review"]`). Use names exactly as the Skill list shows them.
3. **Run** (≥3 trials; routing is probabilistic):
   ```bash
   python3 <this-skill-dir>/run_trigger_tests.py cases.jsonl --trials 3 --out results.jsonl
   ```
   Runs in a scratch git repo with read-only tools and no MCP servers; stops at the first Skill call (no call within `--max-turns`, default 4 → `NONE`). **Aborts if any expected skill isn't loaded** in the headless session. A 3-turn probe cost $0.11 (observed 2026-11-04); runs that hit Skill early cost less — state cases × trials × ~$0.1 before running. Exit code 1 if any case is below `--min-pass` (default 1.0; ≥0.67 is reasonable for a probabilistic router — say which you used). `--help` for all flags.
4. **Report** the printed tables verbatim: per-case pass k/N, and per-skill correct vs false fires. Label anything not run as *inferred*.
5. **Fix descriptions, not tests.** Misroute → add a `Not for … (use X)` clause or sharpen the trigger in the losing/winning description. Re-run the failing rows **and** the full set (fixes cause regressions elsewhere).

## Common mistakes

| Mistake | Fix |
|---|---|
| Predicting results from descriptions and calling it verified | Run the script; only observed Skill calls count |
| Expected column copied from the descriptions | Write expectations from intent first |
| Only one negative control | ~⅓ of cases should expect NONE or a sibling |
| 1 trial | ≥3 trials; report k/N |
| Editing a case's `expect` to make it pass | Change the description, or record a decided intent change explicitly |
| Empty working dir | Script builds a fixture repo (code, tests, CI, uncommitted diff) |
