#!/usr/bin/env python3
"""Run skill-routing test cases against headless Claude Code and report which skill fires.

Cases file: JSONL, one object per line:
  {"prompt": "clean this up", "expect": "simplify"}
  {"prompt": "rename foo to bar", "expect": "NONE"}
  {"prompt": "...", "expect": ["a", "b"]}        # any of these passes

Each trial runs in a scratch git repo with read-only tools (Skill Read Glob Grep) and no
MCP servers, and stops at the first Skill call. If the session finishes without one => NONE.
If it hits --max-turns first => TRUNCATED (counts as a fail: no evidence either way; raise --max-turns).
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
from collections import defaultdict

TOOLS = "Skill Read Glob Grep"


def make_fixture(root):
    """Small but realistic repo: code, tests, CI config, and an uncommitted diff."""
    files = {
        "README.md": "# demo\nRun tests: `python -m pytest`\n",
        "auth.py": "def get_token(resp):\n    return resp['token']\n",
        "utils.py": "def foo(x):\n    return x + 1\n",
        "tests/test_auth.py": "from auth import get_token\n\ndef test_ok():\n    assert get_token({'token': 't'}) == 't'\n",
        ".github/workflows/ci.yml": "on: [push]\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n      - run: python -m pytest\n",
    }
    for rel, body in files.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(body)
    git = lambda *a: subprocess.run(["git", *a], cwd=root, check=True, capture_output=True)
    git("init", "-q")
    git("add", "-A")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
    with open(os.path.join(root, "utils.py"), "a") as f:  # uncommitted diff
        f.write("\ndef foo2(x):\n    y = x + 1\n    return y\n")


def first_skill(prompt, cwd, max_turns, model, timeout=180):
    """Return (first skill invoked or 'NONE', tools used before it, skills listed at init)."""
    cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose",
           "--max-turns", str(max_turns), "--no-session-persistence",
           "--tools", TOOLS, "--strict-mcp-config"]
    if model:
        cmd += ["--model", model]
    proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    timer = threading.Timer(timeout, proc.kill)
    timer.start()
    tools_seen, loaded = [], []
    try:
        for line in proc.stdout:
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("type") == "system" and ev.get("subtype") == "init":
                loaded = ev.get("skills") or []
            if ev.get("type") == "result":
                return ("NONE" if ev.get("subtype") == "success" else "TRUNCATED"), tools_seen, loaded
            if ev.get("type") != "assistant":
                continue
            for block in ev.get("message", {}).get("content", []) or []:
                if block.get("type") != "tool_use":
                    continue
                tools_seen.append(block.get("name"))
                if block.get("name") == "Skill":
                    return (block.get("input") or {}).get("skill"), tools_seen, loaded
    finally:
        timer.cancel()
        proc.kill()
        proc.wait()
    return "TRUNCATED", tools_seen, loaded  # killed by --timeout


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cases")
    ap.add_argument("--trials", type=int, default=3)
    ap.add_argument("--max-turns", type=int, default=4)
    ap.add_argument("--model", default=None)
    ap.add_argument("--min-pass", type=float, default=1.0, help="fraction of trials a case must pass (e.g. 0.67)")
    ap.add_argument("--timeout", type=int, default=180, help="seconds per trial before it is killed")
    ap.add_argument("--out", default=None, help="write per-trial results as JSONL")
    args = ap.parse_args()

    with open(args.cases) as f:
        cases = [json.loads(l) for l in f if l.strip() and not l.lstrip().startswith("#")]

    rows, records = [], []
    fired = defaultdict(lambda: [0, 0])  # skill (or NONE) -> [correct, wrong]
    checked = False
    with tempfile.TemporaryDirectory() as root:
        make_fixture(root)
        for i, case in enumerate(cases, 1):
            expect = case["expect"] if isinstance(case["expect"], list) else [case["expect"]]
            got = []
            for t in range(args.trials):
                skill, tools, loaded = first_skill(case["prompt"], root, args.max_turns, args.model, args.timeout)
                if not checked and loaded:
                    wanted = {e for c in cases for e in (c["expect"] if isinstance(c["expect"], list) else [c["expect"]])}
                    missing = sorted(wanted - set(loaded) - {"NONE"})
                    if missing:
                        sys.exit(f"ABORT: expected skills not loaded in headless session: {', '.join(missing)}")
                    checked = True
                got.append(skill)
                records.append({"case": i, "trial": t + 1, "prompt": case["prompt"],
                                "expect": expect, "got": skill, "tools_before": tools})
                print(f"  [{i}.{t + 1}] {skill}", file=sys.stderr, flush=True)
            passes = sum(g in expect for g in got)
            for g in got:
                fired[g][0 if g in expect else 1] += 1
            rows.append((i, case["prompt"], "|".join(expect), got, passes))

    if args.out:
        with open(args.out, "w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

    print(f"\n| # | prompt | expect | got | pass |\n|---|---|---|---|---|")
    ok_cases = 0
    for i, prompt, expect, got, passes in rows:
        ok = passes / args.trials >= args.min_pass - 0.005  # so 0.67 means 2 of 3, not 3 of 3
        ok_cases += ok
        print(f"| {i} | {prompt} | {expect} | {', '.join(got)} | {passes}/{args.trials} {'✅' if ok else '❌'} |")
    print(f"\nCases passing: {ok_cases}/{len(rows)}  (min-pass {args.min_pass:.0%} of {args.trials} trials)")
    if fired:
        print("\n| fired | correct | wrong |\n|---|---|---|")
        for s, (hit, false) in sorted(fired.items()):
            print(f"| {s} | {hit} | {false} |")
    truncated = sum(r["got"] == "TRUNCATED" for r in records)
    if truncated:
        print(f"\n{truncated} trial(s) TRUNCATED before deciding - re-run with a higher --max-turns before concluding")
    sys.exit(0 if ok_cases == len(rows) else 1)


if __name__ == "__main__":
    main()
