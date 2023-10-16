---
name: 2-build-create-evals
description: Use when building or changing any LLM-driven behavior - a prompt, agent, classifier, extractor, tool-using loop, or skill's output quality - before writing or editing the prompt. Also when someone asks "is my prompt good?", "how do I know it works?", or plans to "eyeball the results". Not for deterministic code (use test-driven-development).
---

# 2-build-create-evals

## Overview
TDD for non-deterministic systems: **write the eval set, run it against a naive prompt, watch it fail, then write the prompt.** "Run it on some tickets and eyeball it" is not an eval.

## Steps

1. **Cases before prompt.** 20–50 cases is the target; **"working today" minimum ≈ 15 real + 10 synthetic**, grow later from production failures. Source them in this order: real inputs/failures the user has → existing manual checks/bug reports → only then synthetic, labeled `"synthetic"`. Never invent the label set (categories, rules) — ask for it; that's the one blocking question. **While waiting**, scaffold the structured-output target (start from `target_example.py`) and the case skeleton.
2. **Each case is unambiguous.** Two domain experts would give the same verdict — if two answers are both right, accept either (`{"category": {"any": ["a", "b"]}}`), don't drop the case. Include: typical, edge, adversarial (prompt-injection in the input), and **should-not** cases (e.g. must *not* escalate).
3. **Pick graders, cheapest first.** Code graders (exact field match, contains, regex) wherever the output is structured — design the output to be structured. LLM judge only for free text, calibrated against a handful of human-labeled examples. Grade **outcomes**, not the exact steps.
4. **Weight asymmetric errors.** If one mistake is worse (missed escalation vs extra escalation), make it its own case group and report it separately.
5. **Run the baseline**, then iterate the prompt:
   ```bash
   python3 <this-skill-dir>/evalrun.py run cases.jsonl --cmd "python target.py" --trials 3 --out baseline.json
   ```
   Target reads one case input on stdin, prints output (JSON for field checks). `--help` for case format. Trials run serially: state cases × trials API calls before running.
6. **Report pass^k, not just pass@k** for anything that must be consistent (an agent acting unattended). 75% per trial ≈ 42% over 3 trials (if trials are independent).
7. **A case at 0/k is usually a broken task or grader** — read the transcript before "fixing" the prompt.
8. Hand off to `3-test-rerun-evals-before-merge` for every later change.

## Common mistakes

| Mistake | Fix |
|---|---|
| Prompt and code first, tests "after I see results" | Cases first; naive-prompt baseline must run |
| Inventing categories/rules as placeholders | Ask for the label set; it's the spec |
| "Eyeball the outputs" | Code graders + a number |
| 1 trial per case | ≥3; report pass^k |
| LLM judge for enum/boolean output | Exact match |
| Only happy-path cases | Add edge, adversarial, should-not |

Sources: Anthropic, *Demystifying evals for AI agents*; *Writing effective tools for agents*.
