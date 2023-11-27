#!/usr/bin/env python3
"""Lint agent tool definitions for the mechanical problems that make agents pick the wrong tool.

  lint_tools.py tools.json

Accepts a JSON list of tools in Anthropic API form ({name, description, input_schema}) or MCP form
({name, description, inputSchema}), or an object with a "tools" list (e.g. MCP tools/list output).
Judgment calls (is the purpose distinct? are returns high-signal?) are left to the reviewer.
Exit code 1 if any HIGH finding.
"""
import argparse
import json
import re
import sys

GENERIC_NAMES = {"search", "get", "run", "query", "execute", "do", "call", "fetch", "list", "update", "tool"}
AMBIGUOUS_PARAMS = {"id", "user", "q", "data", "input", "value", "obj", "item", "fields", "params", "args"}
DESTRUCTIVE = re.compile(r"(^|_)(delete|remove|drop|destroy|purge|wipe|reset)(_|$)", re.I)
LISTISH = re.compile(r"^(list|get_all|list_all|search|find|query)", re.I)
BOUND_PARAMS = {"limit", "max_results", "page_size", "cursor", "page", "offset", "top_k"}
SAFETY_PARAMS = {"dry_run", "confirm", "confirmation", "preview"}
PAYLOAD_PARAMS = {"fields", "data", "params", "args", "payload", "body", "json", "filter", "options"}


def lint(tools):
    out = []
    add = lambda sev, name, msg: out.append((sev, name, msg))
    names = [t["name"] for t in tools]
    for t in tools:
        n, desc = t["name"], (t.get("description") or "").strip()
        schema = t.get("input_schema") or t.get("inputSchema") or {}
        props = schema.get("properties") or {}
        required = set(schema.get("required") or [])
        if n.lower() in GENERIC_NAMES:
            add("HIGH", n, "generic name - say what and where (e.g. tickets_search)")
        if len(desc.split()) < 8:
            add("HIGH", n, f"description is {len(desc.split())} words - say when to use it, what it returns, and when NOT to use it")
        if DESTRUCTIVE.search(n) and not (SAFETY_PARAMS & set(props)) and "irreversible" not in desc.lower():
            add("HIGH", n, "destructive with no dry_run/confirm param and no 'irreversible' warning in the description")
        if LISTISH.match(n) and not (BOUND_PARAMS & set(props)):
            add("HIGH", n, "list/search with no limit or pagination param - can flood the context")
        for p, spec in props.items():
            if p.lower() in AMBIGUOUS_PARAMS:
                add("MED", n, f"param '{p}' is ambiguous - name the thing (ticket_id, user_email)")
            if not spec.get("description"):
                add("MED", n, f"param '{p}' has no description (format, example)")
            packed = re.search(r"\bjson\b", spec.get("description") or "", re.I) or (
                p.lower() in PAYLOAD_PARAMS and re.search(r"\bjson\b", desc, re.I))
            if spec.get("type") == "string" and packed:
                add("HIGH", n, f"param '{p}' is JSON packed in a string - use a typed object schema")
        for p in props:
            if p not in required and "default" not in props[p] and p not in BOUND_PARAMS:
                add("LOW", n, f"param '{p}' optional with no default stated")
    toks = {n: set(re.split(r"[_\-.]", n.lower())) - {""} for n in names}
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            shared = toks[a] & toks[b]
            if shared and (toks[a] <= toks[b] or toks[b] <= toks[a]):
                add("HIGH", f"{a} / {b}", f"overlapping names (share {sorted(shared)}) - make purposes distinct or merge")
    if len({n.split("_")[0] for n in names}) == len(names) and len(names) > 3:
        add("LOW", "*", "no shared namespace prefix (e.g. tickets_*) across tools")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tools_json")
    data = json.load(open(ap.parse_args().tools_json))
    tools = data["tools"] if isinstance(data, dict) else data
    findings = lint(tools)
    order = {"HIGH": 0, "MED": 1, "LOW": 2}
    for sev, name, msg in sorted(findings, key=lambda f: order[f[0]]):
        print(f"{sev:<4} {name}: {msg}")
    high = sum(f[0] == "HIGH" for f in findings)
    print(f"\n{len(tools)} tools, {len(findings)} findings ({high} HIGH). Mechanical checks only - also review purpose, returns, and errors.")
    sys.exit(1 if high else 0)


if __name__ == "__main__":
    main()
