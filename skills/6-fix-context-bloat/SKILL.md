---
name: 6-fix-context-bloat
description: Use when an agent or Claude Code session gets slow, expensive, or starts forgetting earlier instructions as it runs long, when context fills up or auto-compacts, when someone asks "where is my context going?" or "why is this session so costly?", or before adding more tools, skills, MCP servers, or memory files to an agent.
---

# 6-fix-context-bloat

## Overview
Context is a budget: every turn re-reads all of it; compaction discards detail when full. **Measure where it goes from real token usage before recommending anything.** Tie each fix to a number; re-measure.

## Steps

1. **Measure.**
   ```bash
   python3 -I <this-skill-dir>/context_budget.py transcript.jsonl --files CLAUDE.md <other static files>
   ```
   Input: Claude Code logs (`~/.claude/projects/*/*.jsonl`) or `claude -p --output-format stream-json`; for your own API agent, log each response's `usage` in that shape.
   - **[measured]** is API `usage` (deduplicated by message id); **[estimate]** is chars/4 for what entered the context.
   - Own output is measured `output_tokens`, including thinking that logs store empty.
   - `attachment: queued_command` is a message queued while the agent was busy (usually a subagent report or task notification).
2. **Split the total into three buckets**, each with a number:
   - **Fixed overhead** (system prompt, tool schemas, skill listing, memory, MCP servers; paid every call; script splits out what was logged before call #1): disable unused MCP servers, plugins, skills (check which were called first); trim memory files; defer rarely used tools.
   - **Accumulation** (own output, tool results, subagent reports, fetched pages): terse intermediate turns; cap tool output (`head`, `rg -m`, pagination); subagents return ≤300 words or a file path; fetch only needed sections.
   - **Compaction** (each `compaction after call #N`): compact earlier with a focus prompt, or fresh session per task; durable state in files.
3. **"It forgets instructions."**
   - **Confirm the behavior first.** Count relevant calls before/after the compaction line (`rg -n compact_boundary`), e.g. Bash `grep` versus `rg`. If zero, say so; ask which session or step showed it.
   - Locate the instruction: `rg -n '<phrase>' transcript.jsonl`.
   - **Given in chat before a compaction:** it lives only in a summary. Put it in a file re-read every turn (CLAUDE.md or notes); don't repeat it in chat.
   - **From a file re-injected after compaction**, or **no compaction:** loss isn't the cause. Check if it was buried under large results near the failing step (`6-fix-debug-one-run`).
4. **Rank fixes by tokens saved**, e.g. "skill listing ~9K on each of 113 calls". Say who controls each lever: user config, user habits, or harness. Drop levers under ~2% of peak.
5. **Verify.** Re-run on a comparable session (same task, similar length). Report fixed overhead, segment growth, compaction count before and after; if not re-measured, write "not re-measured".

## Common mistakes
- Blaming tool output by default: own output and subagent reports often dominate.
- Summing raw log sizes: use `usage`; logs hold copies the model never saw.
- "Compact more" without numbers: cite peak and compaction call.
- Repeating lost rules in chat: use a file re-read every turn.
