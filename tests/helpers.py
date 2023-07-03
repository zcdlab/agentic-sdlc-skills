"""Shared helpers for the skill-script tests. Stdlib only; scripts run as subprocesses with -I -B so nothing
is imported from (or compiled into) skills/."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"


def script(skill, name):
    p = SKILLS / skill / name
    assert p.exists(), p
    return str(p)


def run(path, *args, stdin=None, cwd=None, env=None):
    """Run a skill script; returns CompletedProcess with text stdout/stderr."""
    e = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", **(env or {})}
    return subprocess.run([sys.executable, "-I", "-B", path, *map(str, args)], input=stdin, capture_output=True,
                          text=True, cwd=cwd, env=e, timeout=300)


def write_jsonl(path, rows):
    Path(path).write_text("".join(json.dumps(r) + "\n" for r in rows))
    return str(path)


def write_json(path, obj):
    Path(path).write_text(json.dumps(obj))
    return str(path)


class TmpCase(unittest.TestCase):
    """TestCase with a fresh temp dir in self.tmp (Path)."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def assertExit(self, proc, code):
        self.assertEqual(proc.returncode, code, f"exit {proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")
