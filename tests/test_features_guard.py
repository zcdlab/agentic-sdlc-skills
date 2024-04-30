"""Tests for skills/2-build-multi-session-agent/features_guard.py. Uses real temporary git repos."""
import json
import subprocess
import unittest

from helpers import TmpCase, run, script

FG = script("2-build-multi-session-agent", "features_guard.py")


def feat(id_="a", desc="Add a", test="true", passes=False):
    return {"id": id_, "description": desc, "test": test, "passes": passes}


class FeaturesGuardTests(TmpCase):
    def setUp(self):
        super().setUp()
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.git("init", "-q")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "T")
        self.git("config", "commit.gpgsign", "false")
        self.ff = self.repo / "features.json"

    def git(self, *a):
        p = subprocess.run(["git", "-C", str(self.repo), *a], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout

    def put(self, feats):
        self.ff.write_text(json.dumps(feats))

    def commit(self, msg="c"):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", msg)

    def check(self, *extra, cwd=None):
        return run(FG, "check", self.ff, *extra, cwd=cwd or self.repo)

    # ---- init
    def test_init_makes_features_with_empty_tests(self):
        items = self.tmp / "items.txt"
        items.write_text("1. Add a client record\n\n- Delete a client now please\n* Export CSV\n")
        p = run(FG, "init", items)
        self.assertExit(p, 0)
        feats = json.loads(p.stdout)
        self.assertEqual(len(feats), 3)
        self.assertEqual(feats[0]["id"], "add-a-client-record")
        self.assertEqual(feats[0]["description"], "Add a client record")
        self.assertEqual(feats[1]["id"], "delete-a-client-now")  # first 4 words
        for f in feats:
            self.assertEqual(f["test"], "")
            self.assertIs(f["passes"], False)

    def test_init_output_fails_check_until_tests_filled(self):
        items = self.tmp / "items.txt"
        items.write_text("Add a client\n")
        self.ff.write_text(run(FG, "init", items).stdout)
        p = self.check()
        self.assertExit(p, 1)
        self.assertIn("empty 'test'", p.stdout)

    # ---- validation
    def test_valid_new_contract(self):
        self.put([feat("a"), feat("b", "Add b")])
        p = self.check()
        self.assertExit(p, 0)
        self.assertIn("CONTRACT", p.stdout)  # no commit yet => no base version
        self.assertIn("0/2 features pass; next: a - Add a", p.stdout)

    def test_empty_fields_rejected(self):
        for k in ("id", "description", "test"):
            with self.subTest(k):
                f = feat()
                f[k] = ""
                self.put([f])
                p = self.check()
                self.assertExit(p, 1)
                self.assertIn(f"empty '{k}'", p.stdout)

    def test_duplicate_ids_rejected(self):
        self.put([feat("a"), feat("a", "other")])
        p = self.check()
        self.assertExit(p, 1)
        self.assertIn("duplicate id: a", p.stdout)

    # ---- base comparison
    def test_only_passes_may_change(self):
        self.put([feat("a")])
        self.commit()
        self.put([feat("a", passes=True)])
        p = self.check()
        self.assertExit(p, 0)
        self.assertNotIn("CONTRACT", p.stdout)
        self.assertIn("1/1 features pass; all done", p.stdout)

    def test_changed_fields_rejected(self):
        self.put([feat("a")])
        self.commit()
        for k, v in (("description", "changed"), ("test", "false")):
            with self.subTest(k):
                f = feat()
                f[k] = v
                self.put([f])
                p = self.check()
                self.assertExit(p, 1)
                self.assertIn(f"'{k}' changed", p.stdout)

    def test_extra_field_rejected(self):
        self.put([feat("a")])
        self.commit()
        f = feat()
        f["note"] = "x"
        self.put([f])
        p = self.check()
        self.assertExit(p, 1)
        self.assertIn("'note' changed", p.stdout)

    def test_added_feature_rejected(self):
        self.put([feat("a")])
        self.commit()
        self.put([feat("a"), feat("b", "Add b")])
        p = self.check()
        self.assertExit(p, 1)
        self.assertIn("added without review: b", p.stdout)

    def test_removed_feature_rejected(self):
        self.put([feat("a"), feat("b", "Add b")])
        self.commit()
        self.put([feat("a")])
        p = self.check()
        self.assertExit(p, 1)
        self.assertIn("removed or renamed: b", p.stdout)

    def test_custom_base(self):
        self.put([feat("a")])
        self.commit("one")
        self.put([feat("a"), feat("b", "Add b")])
        self.commit("two")
        self.assertExit(self.check(), 0)  # HEAD already has b
        p = self.check("--base", "HEAD~1")
        self.assertExit(p, 1)
        self.assertIn("added without review: b", p.stdout)

    # ---- protect
    def test_protect_rejects_changed_path(self):
        (self.repo / "tests" / "e2e").mkdir(parents=True)
        (self.repo / "tests" / "e2e" / "t.py").write_text("v1\n")
        self.put([feat("a")])
        self.commit()
        self.assertExit(self.check("--protect", "tests/e2e"), 0)
        (self.repo / "tests" / "e2e" / "t.py").write_text("v2\n")
        p = self.check("--protect", "tests/e2e")
        self.assertExit(p, 1)
        self.assertIn("protected file changed: tests/e2e/t.py", p.stdout)

    def test_protect_ignores_unprotected_changes(self):
        (self.repo / "src.py").write_text("v1\n")
        self.put([feat("a")])
        self.commit()
        (self.repo / "src.py").write_text("v2\n")
        self.assertExit(self.check("--protect", "tests/e2e"), 0)

    def test_protect_staged_checks_index(self):
        (self.repo / "prot.py").write_text("v1\n")
        self.put([feat("a")])
        self.commit()
        (self.repo / "prot.py").write_text("v2\n")  # working tree only
        self.assertExit(self.check("--staged", "--protect", "prot.py"), 0)
        self.git("add", "prot.py")
        p = self.check("--staged", "--protect", "prot.py")
        self.assertExit(p, 1)
        self.assertIn("protected file changed: prot.py", p.stdout)

    # ---- staged
    def test_staged_checks_index_not_working_tree(self):
        self.put([feat("a")])
        self.commit()
        # working tree has an illegal edit, index is clean => passes
        self.put([feat("a", desc="sneaky")])
        self.assertExit(self.check("--staged"), 0)
        self.assertExit(self.check(), 1)
        # stage the illegal edit, revert working tree => staged check fails
        self.git("add", "features.json")
        self.put([feat("a")])
        p = self.check("--staged")
        self.assertExit(p, 1)
        self.assertIn("'description' changed", p.stdout)
        self.assertExit(self.check(), 0)

    def test_staged_without_index_entry_falls_back_to_working_tree(self):
        self.put([feat("a")])
        self.assertExit(self.check("--staged"), 0)

    # ---- running tests
    def test_passing_feature_with_failing_test_rejected_with_exit_code(self):
        self.put([feat("a")])
        self.commit()
        # commit the failing test command first, so only "passes" changes vs base
        self.put([feat("a", test="echo boom >&2; exit 7")])
        self.commit("test cmd")
        self.put([feat("a", test="echo boom >&2; exit 7", passes=True)])
        p = self.check()
        self.assertExit(p, 1)
        self.assertIn("a: passes=true but test fails (exit 7): boom", p.stdout)

    def test_passing_feature_with_passing_test_accepted(self):
        self.put([feat("a", test="exit 0")])
        self.commit()
        self.put([feat("a", test="exit 0", passes=True)])
        p = self.check()
        self.assertExit(p, 0)
        self.assertNotIn("FAIL", p.stdout)

    def test_failing_test_not_run_when_passes_false(self):
        self.put([feat("a", test="exit 9")])
        self.assertExit(self.check(), 0)

    def test_test_command_runs_in_repo_even_when_invoked_elsewhere(self):
        (self.repo / "marker.txt").write_text("x")
        self.put([feat("a", test="test -f marker.txt", passes=True)])
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        p = self.check(cwd=elsewhere)
        self.assertExit(p, 0)
        self.assertNotIn("FAIL", p.stdout)

    def test_git_works_when_invoked_from_other_directory(self):
        self.put([feat("a")])
        self.commit()
        self.put([feat("a", desc="x")])
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        p = self.check(cwd=elsewhere)
        self.assertExit(p, 1)
        self.assertIn("'description' changed", p.stdout)

    # ---- summary line
    def test_summary_next_unfinished(self):
        self.put([feat("a", test="true", passes=True), feat("b", "Do b"), feat("c", "Do c")])
        p = self.check()
        self.assertExit(p, 0)
        self.assertIn("1/3 features pass; next: b - Do b", p.stdout)

    def test_passes_true_requires_test(self):
        # empty test is reported as empty field; passes=true no-test path is unreachable otherwise
        self.put([feat("a", test="", passes=True)])
        p = self.check()
        self.assertExit(p, 1)
        self.assertIn("empty 'test'", p.stdout)


if __name__ == "__main__":
    unittest.main()
