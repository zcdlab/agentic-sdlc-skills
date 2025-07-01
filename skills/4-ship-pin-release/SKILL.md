---
name: 4-ship-pin-release
description: Use when cutting, tagging, or preparing a release of an agent or LLM feature, bumping its model, or when nobody can say exactly which prompt, model, or tool version is running in prod - "cut v1.4", "get the release ready", "what changed since last week?", "prod behaves differently from staging". Not for ordinary code-only releases with no prompt, tool, or model involved.
---

# 4-ship-pin-release

## Overview
A git tag pins only code; behavior comes from **model + params + prompts + tool schemas + skills + config**, and anything read from env, a shared path, or `latest` changes it without a release. Bundle it all in one manifest, evaluate exactly that bundle, check prod runs it.

## Steps

1. **Inventory the behavior inputs.** Find load sites: `rg -n 'environ|getenv|open\(|model|temperature|max_tokens'`. Table each input: runtime source, in repo or not. Anything read from **outside the repo, or env with a default**, is a leak (ops-edited `/shared/prompts`, `TRIAGE_MODEL` defaulting to `...-latest`, a symlinked skill).
2. **Close the leaks** (proposed diff): dated or versioned model id; fail fast on unset env vars (no alias fallback); copy shared prompts and symlinked skills into the repo; set params explicitly. If ops must keep editing a prod file, bundle it anyway; `verify` catches drift. **Can't read the prod copy** of a prompt or env file? The release is blocked on it until someone diffs it against the repo; don't bundle a copy that "may be stale". Never pick a model id yourself; ask which prod pins.
3. **Evaluate this exact bundle** against the previous release (`3-test-rerun-evals-before-merge`, pinned model). Commit results beside the code.
4. **Build the manifest:**
   ```bash
   python3 -I <this-skill-dir>/bundle.py build --version 1.4.0 --model <pinned-id> \
     --files agent.py prompts/ tools.json skills/ --param temperature=0 \
     --eval-results evals/release-1.4.0.json --eval-model <pinned-id> > release/1.4.0.json
   ```
   It fails on aliases, outside-repo files/symlinks, uncommitted bundled files, and other-model or stale eval results (`--help`). Bundle the code file holding defaults and params (`agent.py`) too. Never bypass a failure by narrowing `--files`; fix the cause or report it. `--assume-pinned` only if the provider documents the id as fixed; say so.
5. **Commit the manifest, then tag.** `bundle.py diff release/1.3.0.json release/1.4.0.json` gives the changelog. No manifest for the previous release? Backfill: check out its tag in a worktree, run `build ... --record-problems` (lists leaks instead of failing).
6. **Ship the tag, not the working tree.** At deploy or startup run `bundle.py verify release/1.4.0.json --model "$MODEL" --param ... --root <deploy dir>`; `--root` checks every bundled file (`--file`: see `--help`). Mismatch exits 1; deploy fails closed.
7. **Report:** inventory table; leaks fixed vs open; `diff` output; eval delta; whether `verify` ran against real prod config (else "prod not verified").

## Common mistakes
Tag = release: manifest pins model, params, hashes. Env model defaulting to `latest`: pin it, fail if unset. Old baseline "still good": results must postdate bundle, same model. Ops-edited prod prompt: bundle it; `verify` flags drift. Dropping a failing file from `--files`: fix or report open.
