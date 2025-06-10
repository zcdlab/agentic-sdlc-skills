"""Tests for skills/4-ship-pin-release/bundle.py. Uses temporary git repos."""
import json
import os
import shutil
import subprocess
import unittest

from helpers import TmpCase, run, script

BUNDLE = script("4-ship-pin-release", "bundle.py")
MODEL = "claude-sonnet-4-5-20250929"


class BundleCase(TmpCase):
    def setUp(self):
        super().setUp()
        (self.tmp / "repo").mkdir()
        self.repo = (self.tmp / "repo").resolve()
        self.git("init", "-q")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "T")
        self.git("config", "commit.gpgsign", "false")
        (self.repo / "prompts").mkdir()
        (self.repo / "prompts" / "sys.md").write_text("system prompt v1\n")
        (self.repo / "tools.json").write_text('{"tools": []}\n')

    def git(self, *a, date=None):
        env = {**os.environ}
        if date:
            env["GIT_COMMITTER_DATE"] = env["GIT_AUTHOR_DATE"] = date
        p = subprocess.run(["git", "-C", str(self.repo), *a], capture_output=True, text=True, env=env)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout

    def commit(self, msg="c", date=None):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", msg, date=date)

    def write_eval(self, passk=0.9):
        (self.repo / "evals").mkdir(exist_ok=True)
        (self.repo / "evals" / "release.json").write_text(json.dumps({"summary": {"pass_hat_k": passk}}))

    def build(self, *extra, files=("prompts/sys.md", "tools.json"), model=MODEL, version="1.0.0", cwd=None):
        return run(BUNDLE, "build", "--version", version, "--model", model, "--files", *files, *extra,
                   cwd=cwd or self.repo)

    def good_args(self):
        return ["--eval-results", "evals/release.json", "--eval-model", MODEL]

    def ready(self):
        self.write_eval()
        self.commit()

    def manifest(self, name="m.json", **kw):
        self.ready()
        p = self.build(*self.good_args(), **kw)
        self.assertExit(p, 0)
        path = self.tmp / name
        path.write_text(p.stdout)
        return path, json.loads(p.stdout)


class BuildTests(BundleCase):
    def test_clean_build(self):
        self.ready()
        p = self.build(*self.good_args(), "--param", "temperature=0")
        self.assertExit(p, 0)
        m = json.loads(p.stdout)
        self.assertEqual(m["version"], "1.0.0")
        self.assertEqual(m["model"], MODEL)
        self.assertEqual(m["params"], {"temperature": "0"})
        self.assertEqual(set(m["files"]), {"prompts/sys.md", "tools.json"})
        self.assertEqual(m["commit"], self.git("rev-parse", "HEAD").strip())
        self.assertEqual(m["eval"]["summary"]["pass_hat_k"], 0.9)
        self.assertNotIn("model_pin_asserted", m)

    def test_directory_expansion(self):
        self.ready()
        p = self.build(*self.good_args(), files=("prompts",))
        self.assertExit(p, 0)
        self.assertEqual(list(json.loads(p.stdout)["files"]), ["prompts/sys.md"])

    def test_alias_models_rejected(self):
        self.ready()
        for alias in ("claude-sonnet-latest", "claude-sonnet-4-5", "gpt-latest-20250101"):
            with self.subTest(alias):
                p = self.build("--eval-results", "evals/release.json", "--eval-model", alias, model=alias)
                self.assertExit(p, 1)
                self.assertIn("looks like an alias", p.stderr)

    def test_dated_and_versioned_models_accepted(self):
        self.ready()
        for mid in (MODEL, "claude-3-opus@20240229", "anthropic.claude-v2:1-v1:0"):
            with self.subTest(mid):
                p = self.build("--eval-results", "evals/release.json", "--eval-model", mid, model=mid)
                self.assertExit(p, 0)

    def test_assume_pinned_overrides_and_records(self):
        self.ready()
        alias = "claude-sonnet-latest"
        p = self.build("--eval-results", "evals/release.json", "--eval-model", alias, "--assume-pinned", model=alias)
        self.assertExit(p, 0)
        self.assertIs(json.loads(p.stdout)["model_pin_asserted"], True)

    def test_dangling_symlink_is_problem_not_crash(self):
        self.ready()
        os.symlink(self.repo / "nowhere.md", self.repo / "prompts" / "dangling.md")
        self.commit("link")
        p = self.build(*self.good_args(), files=("prompts",))
        self.assertExit(p, 1)
        self.assertIn("dangling symlink", p.stderr)
        self.assertNotIn("Traceback", p.stderr)

    def test_dangling_symlink_as_direct_file_arg(self):
        self.ready()
        os.symlink(self.repo / "nowhere.md", self.repo / "dangling.md")
        p = self.build(*self.good_args(), files=("dangling.md",))
        self.assertExit(p, 1)
        self.assertIn("dangling symlink", p.stderr)
        self.assertNotIn("Traceback", p.stderr)

    def test_symlink_outside_repo_reported(self):
        outside = self.tmp / "outside.md"
        outside.write_text("external\n")
        os.symlink(outside, self.repo / "prompts" / "ext.md")
        self.ready()
        p = self.build(*self.good_args(), files=("prompts",))
        self.assertExit(p, 1)
        self.assertIn("outside the repo", p.stderr)
        self.assertIn("ext.md", p.stderr)

    def test_file_outside_repo_reported(self):
        outside = self.tmp / "outside.md"
        outside.write_text("external\n")
        self.ready()
        p = self.build(*self.good_args(), files=("prompts/sys.md", str(outside)))
        self.assertExit(p, 1)
        self.assertIn("outside the repo", p.stderr)

    def test_uncommitted_changes_reported(self):
        self.ready()
        (self.repo / "prompts" / "sys.md").write_text("edited, uncommitted\n")
        p = self.build(*self.good_args())
        self.assertExit(p, 1)
        self.assertIn("uncommitted changes in bundled files", p.stderr)
        self.assertIn("prompts/sys.md", p.stderr)

    def test_untracked_bundled_file_reported(self):
        self.ready()
        (self.repo / "new.md").write_text("new\n")
        p = self.build(*self.good_args(), files=("prompts/sys.md", "new.md"))
        self.assertExit(p, 1)
        self.assertIn("uncommitted changes", p.stderr)

    def test_eval_model_mismatch_reported(self):
        self.ready()
        p = self.build("--eval-results", "evals/release.json", "--eval-model", "claude-haiku-4-5-20251001")
        self.assertExit(p, 1)
        self.assertIn("eval results were measured on 'claude-haiku-4-5-20251001'", p.stderr)

    def test_eval_model_omitted_is_mismatch(self):
        self.ready()
        p = self.build("--eval-results", "evals/release.json")
        self.assertExit(p, 1)
        self.assertIn("re-run evals", p.stderr)

    def test_stale_eval_results_reported(self):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "base", date="2020-01-01T00:00:00")
        self.write_eval()
        self.commit("evals", date="2021-01-01T00:00:00")
        (self.repo / "prompts" / "sys.md").write_text("system prompt v2\n")
        self.commit("prompt change", date="2024-01-01T00:00:00")
        p = self.build(*self.good_args())
        self.assertExit(p, 1)
        self.assertIn("predates the last change to bundled files", p.stderr)

    def test_fresh_eval_results_not_stale(self):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "base", date="2020-01-01T00:00:00")
        self.write_eval()
        self.commit("evals", date="2024-01-01T00:00:00")
        p = self.build(*self.good_args())
        self.assertExit(p, 0)
        self.assertNotIn("predates", p.stderr)

    def test_missing_eval_results_reported(self):
        self.ready()
        p = self.build()
        self.assertExit(p, 1)
        self.assertIn("no --eval-results", p.stderr)

    def test_record_problems_exits_0_and_writes_problems(self):
        self.ready()
        p = self.build("--record-problems", model="claude-sonnet-latest")
        self.assertExit(p, 0)
        m = json.loads(p.stdout)
        self.assertTrue(any("alias" in x for x in m["problems"]))
        self.assertTrue(any("no --eval-results" in x for x in m["problems"]))
        self.assertIn("NOTE", p.stderr)
        self.assertNotIn("FAIL", p.stderr)

    def test_record_problems_clean_has_empty_list(self):
        self.ready()
        p = self.build(*self.good_args(), "--record-problems")
        self.assertExit(p, 0)
        self.assertEqual(json.loads(p.stdout)["problems"], [])


class VerifyTests(BundleCase):
    def live_copy(self):
        live = self.tmp / "live"
        shutil.copytree(self.repo, live, ignore=shutil.ignore_patterns(".git"))
        return live

    def test_exact_match_exits_0(self):
        path, _ = self.manifest()
        live = self.live_copy()
        p = run(BUNDLE, "verify", path, "--model", MODEL, "--root", live)
        self.assertExit(p, 0)
        self.assertIn("OK - live config matches the release", p.stdout)

    def test_modified_file_exits_1(self):
        path, _ = self.manifest()
        live = self.live_copy()
        (live / "prompts" / "sys.md").write_text("tampered\n")
        p = run(BUNDLE, "verify", path, "--root", live)
        self.assertExit(p, 1)
        self.assertIn("prompts/sys.md", p.stdout)
        self.assertIn("differs from release", p.stdout)
        self.assertIn("1 mismatch", p.stdout)

    def test_missing_live_file_exits_1(self):
        path, _ = self.manifest()
        live = self.live_copy()
        (live / "tools.json").unlink()
        p = run(BUNDLE, "verify", path, "--root", live)
        self.assertExit(p, 1)
        self.assertIn("tools.json: live copy", p.stdout)
        self.assertIn("missing", p.stdout)

    def test_model_mismatch_exits_1(self):
        path, _ = self.manifest()
        live = self.live_copy()
        p = run(BUNDLE, "verify", path, "--model", "claude-sonnet-4-5-20250101", "--root", live)
        self.assertExit(p, 1)
        self.assertIn("model: live", p.stdout)

    def test_model_not_given_is_not_checked(self):
        path, _ = self.manifest()
        self.assertExit(run(BUNDLE, "verify", path, "--root", self.live_copy()), 0)

    def test_param_match_and_mismatch(self):
        self.ready()
        b = self.build(*self.good_args(), "--param", "temperature=0")
        self.assertExit(b, 0)
        path = self.tmp / "m.json"
        path.write_text(b.stdout)
        live = self.live_copy()
        self.assertExit(run(BUNDLE, "verify", path, "--param", "temperature=0", "--root", live), 0)
        p = run(BUNDLE, "verify", path, "--param", "temperature=1", "--root", live)
        self.assertExit(p, 1)
        self.assertIn("param temperature: live '1' != release '0'", p.stdout)

    def test_unknown_param_mismatch(self):
        path, _ = self.manifest()
        p = run(BUNDLE, "verify", path, "--param", "top_p=0.5", "--root", self.live_copy())
        self.assertExit(p, 1)
        self.assertIn("param top_p", p.stdout)

    def test_root_checks_every_bundled_file(self):
        path, _ = self.manifest()
        live = self.live_copy()
        (live / "prompts" / "sys.md").write_text("x\n")
        (live / "tools.json").write_text("y\n")
        p = run(BUNDLE, "verify", path, "--root", live)
        self.assertExit(p, 1)
        self.assertIn("2 mismatch", p.stdout)

    def test_file_only_checks_named_file(self):
        path, _ = self.manifest()
        live = self.live_copy()
        (live / "tools.json").write_text("changed\n")
        p = run(BUNDLE, "verify", path, "--file", f"prompts/sys.md={live / 'prompts' / 'sys.md'}")
        self.assertExit(p, 0)

    def test_file_override_path(self):
        path, _ = self.manifest()
        live = self.live_copy()
        shared = self.tmp / "shared_sys.md"
        shutil.copy(live / "prompts" / "sys.md", shared)
        (live / "prompts" / "sys.md").write_text("stale local copy\n")
        p = run(BUNDLE, "verify", path, "--root", live, "--file", f"prompts/sys.md={shared}")
        self.assertExit(p, 0)
        shared.write_text("drifted\n")
        p = run(BUNDLE, "verify", path, "--root", live, "--file", f"prompts/sys.md={shared}")
        self.assertExit(p, 1)
        self.assertIn(str(shared), p.stdout)

    def test_file_not_in_release(self):
        path, _ = self.manifest()
        extra = self.tmp / "extra.md"
        extra.write_text("x")
        p = run(BUNDLE, "verify", path, "--file", f"extra.md={extra}")
        self.assertExit(p, 1)
        self.assertIn("extra.md: not in the release", p.stdout)

    def test_file_without_equals_uses_relative_path(self):
        path, _ = self.manifest()
        live = self.live_copy()
        p = run(BUNDLE, "verify", path, "--file", "tools.json", cwd=live)
        self.assertExit(p, 0)


class DiffTests(BundleCase):
    def write(self, name, **over):
        m = {"version": "1", "model": MODEL, "params": {}, "files": {"a": "1", "b": "2", "c": "3"},
             "eval": {"summary": {"pass_hat_k": 0.8}}}
        m.update(over)
        path = self.tmp / name
        path.write_text(json.dumps(m))
        return path

    def test_reports_all_changes(self):
        old = self.write("old.json", version="1.3.0", params={"temperature": "0"})
        new = self.write("new.json", version="1.4.0", model="claude-sonnet-4-6-20260101",
                         params={"temperature": "1"}, files={"a": "1", "b": "X", "d": "4"},
                         eval={"summary": {"pass_hat_k": 0.95}})
        p = run(BUNDLE, "diff", old, new)
        self.assertExit(p, 0)
        out = p.stdout
        self.assertIn("1.3.0 -> 1.4.0", out)
        self.assertIn(f"model: {MODEL} -> claude-sonnet-4-6-20260101", out)
        self.assertIn("params:", out)
        self.assertIn("changed: b", out)
        self.assertIn("removed: c", out)
        self.assertIn("added: d", out)
        self.assertNotIn(": a", out)
        self.assertIn("eval pass^k: 0.8 -> 0.95", out)

    def test_identical_manifests_report_only_header_and_eval(self):
        m = self.write("m.json")
        p = run(BUNDLE, "diff", m, m)
        self.assertExit(p, 0)
        self.assertNotIn("model:", p.stdout)
        self.assertNotIn("changed:", p.stdout)
        self.assertIn("eval pass^k: 0.8 -> 0.8", p.stdout)

    def test_no_eval_line_when_neither_has_eval(self):
        a = self.write("a.json", eval=None)
        b = self.write("b.json", eval=None)
        p = run(BUNDLE, "diff", a, b)
        self.assertExit(p, 0)
        self.assertNotIn("pass^k", p.stdout)


if __name__ == "__main__":
    unittest.main()
