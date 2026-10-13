---
name: 0-pick-skill
description: Use when a request is vague, one line, or underspecified and it is unclear which installed skill (if any) applies - e.g. "look at this", "clean this up", "make it better", "fix it", "ship it", "build something that...". Use before asking the user any clarifying question.
---

# 0-pick-skill

## Overview
Turn the user's words into the best skill (or none) plus a complete prompt, then run it. **The user never retypes their request.**

**Precedence:** user/project instructions (CLAUDE.md etc.) > chosen skill's rules (after hand-off) > this skill. For vague asks this *is* the "check for a skill first" step `using-superpowers` requires; NONE is valid.

## Steps

1. **Discover before asking.** ≈5 cheap reads max (`git status`/branch/diff, CI config, open PR, named files). Ask only the unknowable.
2. **Match** the ask against skill descriptions in your skill list.
   - **A skill whose description claims the topic wins**, even for factual questions (model IDs → `claude-api`).
   - A skill matching the **action** wins ("clean this up" after a diff → `simplify`).
   - **NONE** only when no description matches (single clear edit, general factual question): just do/answer, no card.
   - Two skills in sequence → name the chain (`verification-before-completion → finishing-a-development-branch`), hand off to the first, invoke next when done.
   - Never name an unlisted skill.
   - Overlap → more specific "Use when". Duplicates (`x` vs `plugin:x`) → unprefixed, unless inside that plugin's workflow (`innerloop:*` only within `/innerloop`).
3. **Forge the prompt** from their words: Goal (quote them) · Scope / Out of scope · Done when · Verify by · Guardrails.
4. **Show the card**, then act. Obvious reading → one line: `Using systematic-debugging — Goal: fix KeyError 'token' at auth.py:42; done when a failing test reproduces it and passes after the fix.` Otherwise the full card:

```
Skill: systematic-debugging   (runner-up: test-driven-development — used after root cause)
Prompt:
  Goal: find why tests fail intermittently on CI ("fail randomly on CI")
  Scope: tests + CI config · Out: disabling tests, retry-to-green
  Done when: root cause identified with log evidence; fix proposed
  Verify by: repeated runs of the affected test pass
Found: .github/workflows/ci.yml, branch feat/x
Need: link to one failing run (continuing with local repro meanwhile)
```

5. **Proceed** at once when local and reversible. **Stop before** outward-facing/destructive steps (push, PR, merge, deploy, post, delete): do the local part first (e.g. verification), then ask.
6. **Hand off:** invoke the chosen skill with the forged prompt; its interaction rules then apply (`brainstorming` asks one question at a time: leave `Need:` empty).

## Rules
No hand-off: max **2** questions, in `Need:`; keep working on the unblocked part. Ambiguous reading → state it in Goal, don't ask.

## Common mistakes
- Asking "which CI / file?" before looking: discover first.
- 3+ questions: ≤2 in `Need:`.
- Hiding interpretation: show Goal / Done when / Verify by.
- Full card for obvious ask: one line.
- NONE despite topic-claiming skill: it wins.
- Forcing a skill: NONE is valid.
