#!/usr/bin/env python3
"""Pin everything that drives an agent's behavior into one release manifest, and check prod against it.

  bundle.py build --version 1.4.0 --model claude-sonnet-4-5-20250929 --files prompts/sys.md tools.json skills/ \\
      [--param temperature=0] [--eval-results evals/release.json --eval-model <id>] > release-1.4.0.json
  bundle.py verify release-1.4.0.json --model "$TRIAGE_MODEL" --root /srv/agent [--file prompts/sys.md=/shared/prompts/sys.md] \\
      [--param temperature=$TRIAGE_TEMP]
  bundle.py diff release-1.3.0.json release-1.4.0.json

build fails (exit 1) when the release can't be reproduced: a model alias instead of a pinned id
("latest", or no date/version suffix), a file outside the repo or a symlink pointing outside it, uncommitted
changes to bundled files, or eval results that were measured on a different model than the one pinned
or that predate the last commit touching a bundled file.
verify exits 1 if the live model, params, or file contents differ from the manifest.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys

PINNED = re.compile(r"(\d{8}|-v\d+(:\d+)?|@\d{8}|-\d{3})$")  # dated / versioned ids; aliases don't match


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()[:16]


def git(*a):
    p = subprocess.run(["git", *a], capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 else None


def expand(paths, root, problems):
    out = {}
    for p in paths:
        walk = [os.path.join(d, f) for d, _, fs in os.walk(p, followlinks=True) for f in fs] if os.path.isdir(p) else [p]
        for f in sorted(walk):
            real = os.path.realpath(f)
            if not os.path.exists(real):
                problems.append(f"dangling symlink: {f} -> {real}")
                continue
            if not real.startswith(root + os.sep):
                problems.append(f"outside the repo (won't ship or can change unversioned): {f} -> {real}")
                continue
            out[os.path.relpath(f, root)] = sha(f)
    return out


def params(kvs):
    return dict(kv.split("=", 1) for kv in kvs)


def cmd_build(a):
    problems, root = [], os.path.realpath(git("rev-parse", "--show-toplevel") or os.getcwd())
    if (not PINNED.search(a.model) or "latest" in a.model) and not a.assume_pinned:
        problems.append(f"model '{a.model}' looks like an alias; pin a dated/versioned id "
                        "(or --assume-pinned if your provider documents this id as fixed)")
    files = expand(a.files, root, problems)
    dirty = git("status", "--porcelain", "--", *files) if files else ""
    if dirty:
        problems.append("uncommitted changes in bundled files:\n    " + dirty.replace("\n", "\n    "))
    m = {"version": a.version, "commit": git("rev-parse", "HEAD"), "model": a.model, "params": params(a.param), "files": files}
    if a.assume_pinned:
        m["model_pin_asserted"] = True
    if a.eval_results:
        if a.eval_model != a.model:
            problems.append(f"eval results were measured on '{a.eval_model}', release pins '{a.model}' - re-run evals")
        changed = git("log", "-1", "--format=%ct", "--", *files) if files else None
        evaled = git("log", "-1", "--format=%ct", "--", a.eval_results)
        if git("status", "--porcelain", "--", a.eval_results):
            evaled = str(int(os.path.getmtime(a.eval_results)))  # fresh, uncommitted results
        if changed and evaled and int(evaled) < int(changed):
            problems.append(f"{a.eval_results} predates the last change to bundled files - re-run evals on this bundle")
        s = json.load(open(a.eval_results)).get("summary", {})
        m["eval"] = {"file": a.eval_results, "sha": sha(a.eval_results), "model": a.eval_model, "summary": s}
    else:
        problems.append("no --eval-results: nothing shows this exact bundle was evaluated")
    if a.record_problems:  # backfilling a manifest for an old release: keep the problems instead of failing
        m["problems"] = problems
    print(json.dumps(m, indent=1))
    for p in problems:
        print(f"{'NOTE' if a.record_problems else 'FAIL'} {p}", file=sys.stderr)
    sys.exit(1 if problems and not a.record_problems else 0)


def cmd_verify(a):
    m, bad = json.load(open(a.manifest)), []
    if a.model is not None and a.model != m["model"]:
        bad.append(f"model: live '{a.model}' != release '{m['model']}'")
    for k, v in params(a.param).items():
        if str(m["params"].get(k)) != v:
            bad.append(f"param {k}: live '{v}' != release '{m['params'].get(k)}'")
    specs = dict(s.split("=", 1) if "=" in s else (s, s) for s in a.file)
    if a.root:  # check every bundled file under the live root; --file entries override single paths
        specs = {**{rel: os.path.join(a.root, rel) for rel in m["files"]}, **specs}
    for rel, live in specs.items():
        if rel not in m["files"]:
            bad.append(f"{rel}: not in the release")
        elif not os.path.exists(live):
            bad.append(f"{rel}: live copy {live} missing")
        elif sha(live) != m["files"][rel]:
            bad.append(f"{rel}: live {live} differs from release")
    print(f"{m['version']}: " + ("OK - live config matches the release" if not bad else f"{len(bad)} mismatch(es)"))
    for b in bad:
        print(f"FAIL {b}")
    sys.exit(1 if bad else 0)


def cmd_diff(a):
    x, y = json.load(open(a.old)), json.load(open(a.new))
    print(f"{x['version']} -> {y['version']}")
    for k in ("model", "params"):
        if x.get(k) != y.get(k):
            print(f"  {k}: {x.get(k)} -> {y.get(k)}")
    for f in sorted(set(x["files"]) | set(y["files"])):
        if x["files"].get(f) != y["files"].get(f):
            print(f"  {'added' if f not in x['files'] else 'removed' if f not in y['files'] else 'changed'}: {f}")
    ex, ey = (x.get("eval") or {}).get("summary", {}), (y.get("eval") or {}).get("summary", {})
    if ex or ey:
        print(f"  eval pass^k: {ex.get('pass_hat_k')} -> {ey.get('pass_hat_k')}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--version", required=True)
    b.add_argument("--model", required=True)
    b.add_argument("--files", nargs="+", required=True)
    b.add_argument("--param", action="append", default=[])
    b.add_argument("--eval-results")
    b.add_argument("--eval-model")
    b.add_argument("--assume-pinned", action="store_true")
    b.add_argument("--record-problems", action="store_true", help="backfill an old release: list problems, exit 0")
    v = sub.add_parser("verify")
    v.add_argument("manifest")
    v.add_argument("--model")
    v.add_argument("--root", help="live deployment dir: check every bundled file under it")
    v.add_argument("--file", action="append", default=[], help="REL or REL=LIVE_PATH")
    v.add_argument("--param", action="append", default=[])
    d = sub.add_parser("diff")
    d.add_argument("old")
    d.add_argument("new")
    a = ap.parse_args()
    {"build": cmd_build, "verify": cmd_verify, "diff": cmd_diff}[a.cmd](a)


if __name__ == "__main__":
    main()
