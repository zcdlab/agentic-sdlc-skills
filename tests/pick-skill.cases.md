# 0-pick-skill — routing test cases

Each case: the user's first message → expected first skill (or NONE).
Pass criteria per case (all must hold):
1. Correct skill (or NONE) chosen.
2. Rewritten prompt reuses the user's words and adds scope, done-criteria, verification.
3. User is NOT asked to re-state the request; at most 1–2 questions, only for facts that can't be inferred.
4. For NONE cases: just answers/does it, no ceremony.

| # | User ask | Expected |
|---|---|---|
| 1 | my tests fail randomly on CI, look at it | systematic-debugging |
| 2 | what's the model id for the latest opus? | claude-api |
| 3 | rename foo to bar in utils.py | NONE |
| 4 | I got review comments on my PR, deal with them | receiving-code-review |
| 5 | I want to build something that summarizes my standups | brainstorming |
| 6 | clean this up (after a diff) | simplify |
| 7 | what does git rebase --onto do | NONE |
| 8 | make a skill for our release checklist | writing-skills |
| 9 | I think my change is done, ship it | verification-before-completion |
| 10 | fix the auth bug, stack trace: KeyError 'token' at auth.py:42 | systematic-debugging |

## Results

### RED (baseline, no skill) — 2026-11-04
- Routing: **10/10 correct** (matches Expected column).
- Gaps observed (verbatim from baseline agent):
  - #1 asked user for CI/run/tests before discovering, while noting "I could skim the repo's CI config first".
  - #5 asked 3 questions up front; #8 blocked on user input.
  - No forged prompt (goal / scope / done / verify) shown for any case.
  - No runner-up skill or rationale surfaced.

### GREEN (with skill v1) — 2026-11-04
- Routing 10/10 kept. Discover-first on #1/#8; forged prompt shown; runner-ups named.
- Issues reported: conflict w/ using-superpowers; "NONE for factual" broke claude-api (#2); duplicate plugin skills; discovery cap too tight; brainstorming hand-off; card too heavy for obvious asks.

### REFACTOR v2 → v3 — 2026-11-04
- v2: added precedence, topic-claiming skill wins, duplicate rule, hand-off rule, one-line card. Retest: #6 regressed (hedged NONE vs simplify); chaining unclear (#9).
- v3: "skill matching the action wins; NONE only if nothing matches" + chain rule. Retest A–D (#6, #9, #3, #7): simplify / verification→finishing (stop before push) / NONE / NONE — all pass; reported "none" remaining conflicts.
- Final: 10/10 routing, 0 blocking questions before work on all 10.

### Headless trigger test (observed, `run_trigger_tests.py`) — 2026-11-04
The runs above were subagent role-plays. This one is real routing: `claude -p` in a fixture repo, first `Skill` call recorded.
- Run 1 (`--max-turns 4`, 2 trials): **5/10 cases** (`pick-skill.results.md`). All 12 NONE trials had read files first (Glob/Read/Grep). The old runner counted "hit the turn limit" as NONE, so the runner now reports `TRUNCATED` separately.
- Re-run of the 5 misses (`--max-turns 10`, 2 trials, `pick-skill.misses-maxturns10.results.md`): #1 flaky CI → systematic-debugging 2/2. #10 "make it better" → 0-pick-skill 2/2. **#7 make a skill, #8 ship it, #9 KeyError fix → NONE 0/2.** These sessions ended normally after 5–6 read calls and never called a skill.
- Reading: 0-pick-skill fires on genuinely vague asks (#4, #5, #10). For concrete asks (#7–#9) the model does the work directly and skips skills such as writing-skills, verification-before-completion, and systematic-debugging.
  - Not fixed by editing 0-pick-skill: those prompts aren't vague, so 0-pick-skill shouldn't own them.
  - The fix would go in those skills' descriptions (Superpowers, not ours), or in a SessionStart rule.
  - Open question for the owner. Not done.
- Caveat (unverified): headless used `--tools "Skill Read Glob Grep"`. With no Edit/Bash available, the agent may answer instead of acting, which could suppress skills. An interactive check would settle it.
