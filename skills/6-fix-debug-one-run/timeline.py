#!/usr/bin/env python3
"""Render an agent transcript as a numbered step timeline and flag suspicious steps.

  timeline.py transcript.jsonl [--width 160]

Reads Claude Code session transcripts (~/.claude/projects/*/*.jsonl) and `claude -p
--output-format stream-json` output; both carry user/assistant events with message.content blocks.
For a Messages API conversation, write one {"type": role, "message": {"content": [...]}} per line.

Flags: ERR (tool_result is_error), REPEAT (same tool + same input as an earlier call),
UNSEEN (an old_string/*_id argument that appears nowhere in the task or earlier tool results -
likely hallucinated), BIG (tool result > 5000 chars - the needed fact may be buried),
CLAIM (final text claims success after an error or failing output).
"""
import argparse
import json
import re

FAIL_WORDS = re.compile(r"\b(\d+ failed|FAILED|Traceback|Error:|error:|not found|No such file|exit code [1-9])")
CLAIM_WORDS = re.compile(r"\b(done|complete[d]?|all tests pass|fixed|success(fully)?|works)\b", re.I)


def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content if isinstance(b, dict))
    return ""


def one_line(s, width):
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= width else s[: width - 3] + "..."


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("transcript")
    ap.add_argument("--width", type=int, default=160)
    args = ap.parse_args()

    steps, pending, seen_calls = [], {}, {}
    first_user, saw_failure, seen_text = None, False, ""
    with open(args.transcript) as f:
        for line in f:
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("type") not in ("user", "assistant"):
                continue
            content = (ev.get("message") or {}).get("content")
            if ev["type"] == "user" and isinstance(content, str):
                first_user = first_user or content
                seen_text += content
                continue
            for b in content or []:
                kind = b.get("type")
                if kind == "text" and ev["type"] == "user":
                    first_user = first_user or b.get("text")
                    seen_text += b.get("text", "")
                elif kind == "text" and b.get("text", "").strip():
                    steps.append({"kind": "SAY", "text": b["text"], "flags": []})
                elif kind == "tool_use":
                    key = json.dumps([b.get("name"), b.get("input")], sort_keys=True)
                    flags = [f"REPEAT of #{seen_calls[key]}"] if key in seen_calls else []
                    for k, v in (b.get("input") or {}).items():
                        if (k == "old_string" or k.endswith("_id")) and isinstance(v, str) and v and v not in seen_text:
                            flags.append(f"UNSEEN {k}")
                    step = {"kind": "CALL", "text": f"{b.get('name')} {json.dumps(b.get('input'))}", "flags": flags}
                    steps.append(step)
                    seen_calls.setdefault(key, len(steps))
                    pending[b.get("id")] = step
                elif kind == "tool_result":
                    out = text_of(b.get("content"))
                    seen_text += out
                    flags = [f"BIG {len(out)} chars"] if len(out) > 5000 else []
                    if b.get("is_error"):
                        flags.append("ERR")
                    elif FAIL_WORDS.search(out):
                        flags.append("FAIL-OUTPUT")
                    saw_failure = saw_failure or any(not f.startswith("BIG") for f in flags)
                    call = pending.get(b.get("tool_use_id"))
                    if call is not None:
                        call["result"] = out
                        call["flags"] += flags

    print(f"TASK: {one_line(first_user or '(not found)', args.width)}\n")
    for i, s in enumerate(steps, 1):
        flags = f"  <-- {', '.join(s['flags'])}" if s["flags"] else ""
        print(f"#{i:<3} {s['kind']:<4} {one_line(s['text'], args.width)}{flags}")
        if "result" in s:
            print(f"     ->   {one_line(s['result'], args.width)}")
    last_say = next((s for s in reversed(steps) if s["kind"] == "SAY"), None)
    if last_say and saw_failure and CLAIM_WORDS.search(last_say["text"]):
        print("\nCLAIM: final message claims success, but an earlier step errored or showed failing output - check it")
    n_flag = sum(bool(s["flags"]) for s in steps)
    print(f"\n{len(steps)} steps, {n_flag} flagged")


if __name__ == "__main__":
    main()
