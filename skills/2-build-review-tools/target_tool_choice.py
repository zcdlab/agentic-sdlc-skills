#!/usr/bin/env python3
"""evalrun target for tool-choice evals: task on stdin -> {"tool": <first tool called or "NONE">, "args": {...}}.

  TOOLS_FILE=tools.json EVAL_MODEL=<model id> [NAME_MAP=map.json] python3 target_tool_choice.py < task.txt

tool_choice is "auto", so "should not call a tool" cases can expect {"tool": "NONE"}.
NAME_MAP ({"old_name": "new_name"}) lets the same cases grade old and new tool sets.
Tools are only offered, never executed: this measures the first choice, not the outcome.
"""
import json
import os
import sys

import anthropic

tools = json.load(open(os.environ["TOOLS_FILE"]))
tools = tools["tools"] if isinstance(tools, dict) else tools
tools = [{"name": t["name"], "description": t.get("description", ""),
          "input_schema": t.get("input_schema") or t.get("inputSchema") or {"type": "object"}} for t in tools]
name_map = json.load(open(os.environ["NAME_MAP"])) if os.environ.get("NAME_MAP") else {}
system = open(os.environ["EVAL_SYSTEM"]).read() if os.environ.get("EVAL_SYSTEM") else "You are a helpful assistant with tools."

resp = anthropic.Anthropic().messages.create(
    model=os.environ["EVAL_MODEL"], max_tokens=1024, system=system, tools=tools,
    messages=[{"role": "user", "content": sys.stdin.read()}])
call = next((b for b in resp.content if b.type == "tool_use"), None)
print(json.dumps({"tool": name_map.get(call.name, call.name) if call else "NONE",
                  "args": call.input if call else {}}))
