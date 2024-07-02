"""Tests for skills/6-fix-debug-one-run/timeline.py."""
import json
import unittest

from helpers import TmpCase, run, script, write_jsonl

T = script("6-fix-debug-one-run", "timeline.py")


def user(text):
    return {"type": "user", "message": {"content": text}}


def say(text):
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


def call(cid, name, **inp):
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": cid, "name": name, "input": inp}]}}


def result(cid, content, is_error=False):
    b = {"type": "tool_result", "tool_use_id": cid, "content": content}
    if is_error:
        b["is_error"] = True
    return {"type": "user", "message": {"content": [b]}}


class Timeline(TmpCase):
    def go(self, events, *args):
        p = run(T, write_jsonl(self.tmp / "t.jsonl", events), *args)
        self.assertExit(p, 0)
        return p.stdout

    def test_help(self):
        p = run(T, "--help")
        self.assertExit(p, 0)
        self.assertIn("timeline.py transcript.jsonl", p.stdout)

    def test_missing_arg_and_missing_file(self):
        self.assertEqual(run(T).returncode, 2)
        self.assertNotEqual(run(T, str(self.tmp / "nope.jsonl")).returncode, 0)

    def test_clean_run_numbered_with_task_and_results(self):
        out = self.go([user("fix the bug"), say("looking"), call("1", "Read", path="a.py"),
                       result("1", [{"type": "text", "text": "file body"}]), say("hello")])
        self.assertIn("TASK: fix the bug", out)
        self.assertIn('#1   SAY  looking', out)
        self.assertIn('#2   CALL Read {"path": "a.py"}', out)
        self.assertIn("     ->   file body", out)
        self.assertIn("#3   SAY  hello", out)
        self.assertIn("3 steps, 0 flagged", out)
        self.assertNotIn("<--", out)
        self.assertNotIn("CLAIM", out)

    def test_task_not_found(self):
        self.assertIn("TASK: (not found)", self.go([say("hi")]))

    def test_task_from_user_text_block_and_first_wins(self):
        out = self.go([{"type": "user", "message": {"content": [{"type": "text", "text": "block task"}]}},
                       user("second")])
        self.assertIn("TASK: block task", out)

    def test_err_flag(self):
        out = self.go([user("t"), call("1", "Bash", cmd="ls"), result("1", "boom", is_error=True)])
        self.assertIn("<-- ERR", out)
        self.assertIn("1 steps, 1 flagged", out)

    def test_fail_output_flag_on_failure_words(self):
        for txt in ("3 failed, 2 passed", "Traceback (most recent call last)", "foo: No such file", "exit code 2"):
            out = self.go([user("t"), call("1", "Bash", cmd="x"), result("1", txt)])
            self.assertIn("<-- FAIL-OUTPUT", out, txt)

    def test_is_error_takes_precedence_over_fail_output(self):
        out = self.go([user("t"), call("1", "Bash", cmd="x"), result("1", "Traceback", is_error=True)])
        self.assertIn("<-- ERR", out)
        self.assertNotIn("FAIL-OUTPUT", out)

    def test_repeat_flag_references_first_call_step(self):
        out = self.go([user("t"), call("1", "Read", p="a"), result("1", "x"), call("2", "Read", p="b"),
                       result("2", "y"), call("3", "Read", p="a"), result("3", "x")])
        self.assertIn("#3   CALL Read {\"p\": \"a\"}  <-- REPEAT of #1", out)
        self.assertNotIn("#2   CALL Read {\"p\": \"b\"}  <--", out)

    def test_repeat_ignores_key_order(self):
        out = self.go([user("t"), call("1", "T", a=1, b=2), call("2", "T", b=2, a=1)])
        self.assertIn("REPEAT of #1", out)

    def test_same_input_different_tool_not_repeat(self):
        out = self.go([user("t"), call("1", "A", p=1), call("2", "B", p=1)])
        self.assertNotIn("REPEAT", out)

    def test_unseen_id_argument_flagged(self):
        out = self.go([user("close ticket T-1"), call("1", "close", ticket_id="T-999")])
        self.assertIn("<-- UNSEEN ticket_id", out)

    def test_id_seen_in_task_not_flagged(self):
        out = self.go([user("close ticket T-1"), call("1", "close", ticket_id="T-1")])
        self.assertNotIn("UNSEEN", out)

    def test_id_seen_in_earlier_tool_result_not_flagged(self):
        out = self.go([user("find it"), call("1", "search", q="x"), result("1", "found id=ABC123"),
                       call("2", "get", item_id="ABC123")])
        self.assertNotIn("UNSEEN", out)

    def test_id_in_later_result_does_not_count(self):
        out = self.go([user("find it"), call("1", "get", item_id="LATE9"), result("1", "LATE9 data")])
        self.assertIn("UNSEEN item_id", out)

    def test_old_string_unseen_flagged_and_seen_not(self):
        out = self.go([user("edit"), call("1", "Edit", old_string="foo bar")])
        self.assertIn("UNSEEN old_string", out)
        out = self.go([user("edit"), call("1", "Read", f="a"), result("1", "has foo bar inside"),
                       call("2", "Edit", old_string="foo bar")])
        self.assertNotIn("UNSEEN", out)

    def test_non_id_param_not_checked(self):
        self.assertNotIn("UNSEEN", self.go([user("t"), call("1", "x", label="never seen")]))

    def test_big_flag_over_5000(self):
        out = self.go([user("t"), call("1", "Read", p="a"), result("1", "x" * 5001)])
        self.assertIn("<-- BIG 5001 chars", out)
        out = self.go([user("t"), call("1", "Read", p="a"), result("1", "x" * 5000)])
        self.assertNotIn("BIG", out)

    def test_big_alone_does_not_trigger_claim(self):
        out = self.go([user("t"), call("1", "Read", p="a"), result("1", "x" * 6000), say("All done, it works")])
        self.assertNotIn("CLAIM", out)

    def test_claim_after_error(self):
        out = self.go([user("t"), call("1", "Bash", c="x"), result("1", "bad", is_error=True), say("All done, fixed!")])
        self.assertIn("CLAIM: final message claims success", out)

    def test_claim_after_failing_output(self):
        out = self.go([user("t"), call("1", "Bash", c="x"), result("1", "2 failed"), say("Complete.")])
        self.assertIn("CLAIM:", out)

    def test_no_claim_when_final_has_no_success_words(self):
        out = self.go([user("t"), call("1", "Bash", c="x"), result("1", "2 failed"), say("It is still broken.")])
        self.assertNotIn("CLAIM", out)

    def test_no_claim_when_no_failure(self):
        self.assertNotIn("CLAIM", self.go([user("t"), call("1", "Bash", c="x"), result("1", "ok"), say("Done.")]))

    def test_claim_uses_last_say_only(self):
        out = self.go([user("t"), call("1", "Bash", c="x"), result("1", "x", is_error=True),
                       say("done"), call("2", "Bash", c="y"), result("2", "ok"), say("I could not finish")])
        self.assertNotIn("CLAIM", out)

    def test_string_content_tool_result(self):
        out = self.go([user("t"), call("1", "Bash", c="x"), result("1", "plain string out")])
        self.assertIn("->   plain string out", out)

    def test_width_truncation(self):
        out = self.go([user("t"), say("w" * 300)], "--width", "40")
        line = [l for l in out.splitlines() if "SAY" in l][0]
        self.assertTrue(line.endswith("..."))
        self.assertIn("w" * 30, line)
        self.assertNotIn("w" * 40, line)

    def test_default_width_160(self):
        out = self.go([user("t"), say("w" * 300)])
        line = [l for l in out.splitlines() if "SAY" in l][0]
        self.assertEqual(len(line.split("SAY  ", 1)[1]), 160)

    def test_whitespace_collapsed_to_one_line(self):
        out = self.go([user("t"), say("a\n\n  b\tc")])
        self.assertIn("SAY  a b c", out)

    def test_ignores_garbage_lines_and_other_event_types(self):
        path = self.tmp / "t.jsonl"
        path.write_text("not json\n" + json.dumps({"type": "system", "subtype": "init"}) + "\n" +
                        json.dumps(user("task")) + "\n\n" + json.dumps(say("hey")) + "\n")
        p = run(T, path)
        self.assertExit(p, 0)
        self.assertIn("TASK: task", p.stdout)
        self.assertIn("1 steps, 0 flagged", p.stdout)

    def test_empty_text_block_skipped_and_empty_file(self):
        out = self.go([user("t"), say("   ")])
        self.assertIn("0 steps, 0 flagged", out)
        (self.tmp / "e.jsonl").write_text("")
        p = run(T, self.tmp / "e.jsonl")
        self.assertExit(p, 0)
        self.assertIn("TASK: (not found)", p.stdout)
        self.assertIn("0 steps, 0 flagged", p.stdout)

    def test_result_without_matching_call_is_ignored(self):
        out = self.go([user("t"), result("ghost", "Traceback"), say("done")])
        self.assertIn("1 steps, 0 flagged", out)
        self.assertIn("CLAIM", out)  # failure still seen, even with no step to attach it to

    def test_flag_count_counts_steps_not_flags(self):
        out = self.go([user("t"), call("1", "Bash", c="x"), result("1", "x", is_error=True),
                       call("2", "Bash", c="x"), result("2", "x", is_error=True)])
        self.assertIn("2 steps, 2 flagged", out)
        self.assertIn("<-- REPEAT of #1, ERR", out)

    def test_claude_code_style_transcript_with_extra_fields(self):
        ev = {"type": "assistant", "uuid": "u", "sessionId": "s", "message": {"role": "assistant", "model": "m",
              "content": [{"type": "text", "text": "hi"}, {"type": "tool_use", "id": "9", "name": "Glob", "input": {"pattern": "*"}}]}}
        out = self.go([user("go"), ev])
        self.assertIn("2 steps", out)
        self.assertIn('CALL Glob {"pattern": "*"}', out)


if __name__ == "__main__":
    unittest.main()
