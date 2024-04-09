#!/usr/bin/env python3
"""Guard a multi-session feature list: agents may only flip "passes", and only when the feature's test passes.

  features_guard.py init SPEC_ITEMS.txt > features.json     # one feature per non-empty line, all passes=false
  features_guard.py check features.json [--base HEAD] [--staged] [--protect tests/e2e]
      # --staged: check the staged copy (use in the pre-commit hook); --protect: paths no session may edit

features.json: [{"id": "client-add", "description": "...", "test": "python3 -m unittest tests.test_clients",
                 "passes": false}, ...]
check fails (exit 1) if:
  - any feature has an empty id, description, or test, or two features share an id;
  - compared with the file at git --base, a feature was removed, added, renamed, or its description/test
    changed (only "passes" may change);
  - a file under a --protect path (e.g. the user-reviewed e2e tests) changed vs --base;
  - any feature with passes=true fails its test right now.
It prints the next unfinished feature, so a fresh session knows what to pick up.
"""
import argparse
import json
import os
import re
import subprocess
import sys


def git_show(path, ref):
    d, name = os.path.split(os.path.abspath(path))
    p = subprocess.run(["git", "-C", d, "show", f"{ref}:./{name}"], capture_output=True, text=True)
    return json.loads(p.stdout) if p.returncode == 0 else None


def cmd_init(args):
    items = [l.strip() for l in open(args.items) if l.strip()]
    feats = []
    for line in items:
        text = re.sub(r"^\s*(\d+[.)]|[-*])\s*", "", line)
        fid = "-".join(re.findall(r"[a-z0-9]+", text.lower())[:4])
        feats.append({"id": fid, "description": text, "test": "", "passes": False})
    print(json.dumps(feats, indent=1))


def cmd_check(args):
    feats = git_show(args.features, "") if args.staged else json.load(open(args.features))  # ":" = index
    if feats is None:
        feats = json.load(open(args.features))
    problems = []
    ids = [f.get("id") for f in feats]
    problems += [f"duplicate id: {i}" for i in sorted({i for i in ids if ids.count(i) > 1})]
    for f in feats:
        for k in ("id", "description", "test"):
            if not f.get(k):
                problems.append(f"{f.get('id') or '?'}: empty '{k}' (fill every test before the first commit)")
    repo = os.path.dirname(os.path.abspath(args.features))
    for path in args.protect:
        diff = ["git", "-C", repo, "diff", "--name-only"] + (["--cached"] if args.staged else []) + [args.base, "--", path]
        changed = subprocess.run(diff, capture_output=True, text=True).stdout.split()
        problems += [f"protected file changed: {c}" for c in changed]
    base = git_show(args.features, args.base)
    if base is None:
        print(f"CONTRACT: {args.features} is new at {args.base} - nothing to compare; the user must review it before work starts")
    else:
        now = {f["id"]: f for f in feats}
        for b in base:
            f = now.get(b["id"])
            if f is None:
                problems.append(f"removed or renamed: {b['id']}")
                continue
            for k in set(b) | set(f):
                if k != "passes" and b.get(k) != f.get(k):
                    problems.append(f"{b['id']}: '{k}' changed (only 'passes' may change)")
        for f in feats:
            if f["id"] not in {b["id"] for b in base}:
                problems.append(f"added without review: {f['id']}")
    for f in feats:
        if not f.get("passes"):
            continue
        if not f.get("test"):
            problems.append(f"{f['id']}: passes=true but no test command")
            continue
        p = subprocess.run(f["test"], shell=True, capture_output=True, text=True, timeout=args.timeout,
                           cwd=os.path.dirname(os.path.abspath(args.features)))  # tests run from the repo
        if p.returncode != 0:
            tail = (p.stdout + p.stderr).strip().splitlines()[-1:] or [""]
            problems.append(f"{f['id']}: passes=true but test fails (exit {p.returncode}): {tail[0][:120]}")
    done = sum(bool(f.get("passes")) for f in feats)
    nxt = next((f for f in feats if not f.get("passes")), None)
    print(f"{done}/{len(feats)} features pass" + (f"; next: {nxt['id']} - {nxt['description']}" if nxt else "; all done"))
    for p in problems:
        print(f"FAIL {p}")
    sys.exit(1 if problems else 0)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("init")
    i.add_argument("items")
    c = sub.add_parser("check")
    c.add_argument("features")
    c.add_argument("--base", default="HEAD")
    c.add_argument("--timeout", type=int, default=600)
    c.add_argument("--staged", action="store_true")
    c.add_argument("--protect", nargs="*", default=[])
    args = ap.parse_args()
    cmd_init(args) if args.cmd == "init" else cmd_check(args)


if __name__ == "__main__":
    main()
