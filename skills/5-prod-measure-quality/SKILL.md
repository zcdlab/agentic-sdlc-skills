---
name: 5-prod-measure-quality
description: Use when measuring an agent's or LLM feature's quality on live production traffic - "sample prod and grade it", "daily quality number", "online eval", "LLM-as-judge on real requests", "offline evals say 93% but I don't trust them" - or before trusting or dashboarding a judge's scores. Not for building the offline eval set (use 2-build-create-evals) or alerting (use 5-prod-set-up-alerts).
---

# 5-prod-measure-quality

## Overview
Offline evals miss unforeseen traffic: grade sampled production. The daily number depends on **the sample** (rare high-risk cases weighted back out), **the judge** (checked against humans *per segment*), and **the privacy path** (what leaves your system).

## Steps

1. **Strata before sampling.** Oversample rare, costly cases: high-risk categories, inputs that *look* high-risk but weren't routed so (keyword regex), new versions, each language.
   ```bash
   python3 -I <this-skill-dir>/online_eval.py sample requests.jsonl --strata strata.json > sample.jsonl
   ```
   - **First matching stratum wins**: list the narrowest first; a broad early regex swallows the rest.
   - Rows get `_day` (for `report --by version _day`) and `_weight` = 1/rate, so oversampling doesn't inflate the overall number.
   - Tune rates to the judging budget.
2. **Privacy.** The script redacts only emails, phones, long numbers; names and free text are NOT redacted. Before ticket text goes to a judge model, get the data path signed off by whoever owns privacy and record it. No sign-off: don't send, say "blocked on privacy review".
3. **Calibrate the judge against humans** before any dashboard number.
   ```bash
   python3 -I <this-skill-dir>/online_eval.py calibrate --labels human.jsonl --judge judge.jsonl --requests requests.jsonl --by version lang gold > calibration.json
   ```
   - Read **Se** (accepts correct) and **Sp** (catches wrong) per segment.
   - A segment is UNVALIDATED until it has ≥30 labels **and ≥10 real errors**: a judge never shown errors is untested; "agreement 90%" usually means easy cases.
   - Label more where unvalidated: `sample` at high rates on those strata with a different `--salt` builds the queue; favor high-risk, new-version, and system-*wrong* items (e.g. overrides).
   - If it passes wrong outputs (listed), fix its prompt (e.g. category definitions) and re-calibrate on **fresh** labels, not tuning ones.
4. **Grade and report.** Add `verdict` to rows:
   ```bash
   python3 -I <this-skill-dir>/online_eval.py report graded.jsonl --calibration calibration.json --by version
   ```
   - Gives weighted, bias-corrected rate with CI and effective n. A group is PROVISIONAL when its judge isn't validated; keep that word on the dashboard.
   - Report the high-risk stratum separately (`--by _stratum`).
   - Version-day CIs are wide: pool days before comparing.
5. **Stay honest.** Humans re-label a few judged items weekly. Re-calibrate when judge prompt/model or product version changes. Feed confirmed failures to 2-build-create-evals' offline set.

## Report
Calibration table (Se/Sp per segment, UNVALIDATED flags, accepted-error ids), privacy status, report lines verbatim, what to label before PROVISIONAL can drop.

## Common mistakes
- "Judge ran on 80 labels, good": Se/Sp per segment, ≥10 errors.
- Uniform sampling: strata, rare high-risk rate 1.0.
- Raw judge pass rate on dashboard: corrected, CI, PROVISIONAL.
- PII to judge: redact, sign-off.
- Re-calibrating on tuning labels: fresh ones.
