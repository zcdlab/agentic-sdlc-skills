#!/usr/bin/env python3
"""Example evalrun target: one input on stdin -> one JSON object on stdout.

Forced tool use + an enum schema makes the output structured, so evalrun's exact-match
graders work and there is no free-text parsing. Adapt LABELS, SCHEMA and the prompt file.

  EVAL_MODEL=<model id> python3 target_example.py < ticket.txt
Look up the current model id (e.g. via the claude-api skill); don't hard-code from memory.
"""
import json
import os
import sys

import anthropic

LABELS = ["<fill in from the user's spec>"]  # never invent these
SCHEMA = {
    "name": "record_result",
    "description": "Record the triage decision for this input.",
    "input_schema": {
        "type": "object",
        "required": ["category", "escalate", "reason"],
        "properties": {
            "category": {"type": "string", "enum": LABELS},
            "escalate": {"type": "boolean"},
            "reason": {"type": "string"},
        },
    },
}


def main():
    prompt_path = os.environ.get("EVAL_PROMPT", "prompt.md")
    system = open(prompt_path).read() if os.path.exists(prompt_path) else "Classify the input."  # naive baseline
    resp = anthropic.Anthropic().messages.create(
        model=os.environ["EVAL_MODEL"],
        max_tokens=512,
        system=system,
        tools=[SCHEMA],
        tool_choice={"type": "tool", "name": SCHEMA["name"]},
        # Input is untrusted data: wrap it, never concatenate it into instructions.
        messages=[{"role": "user", "content": f"<input>\n{sys.stdin.read()}\n</input>"}],
    )
    print(json.dumps(next(b.input for b in resp.content if b.type == "tool_use")))


if __name__ == "__main__":
    main()
