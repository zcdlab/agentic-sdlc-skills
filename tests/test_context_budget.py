"""Tests for skills/6-fix-context-bloat/context_budget.py. Transcripts (Claude Code session JSONL) are generated in-test."""
import json
import random
import unittest

from helpers import TmpCase, run, script, write_jsonl

PATH = script("6-fix-context-bloat", "context_budget.py")


def asst(mid, ctx, out=100, cache_read=0, blocks=None, cache_create=0):
    """Assistant event whose measured context is ctx (input + cache_creation + cache_read)."""
    return {"type": "assistant", "uuid": "u-" + mid,
            "message": {"id": mid, "content": blocks or [{"type": "text", "text": "ok"}],
                        "usage": {"input_tokens": ctx - cache_read - cache_create, "cache_read_input_tokens": cache_read,
                                  "cache_creation_input_tokens": cache_create, "output_tokens": out}}}


def tool_use(tid, name="Read", **inp):
    return {"type": "tool_use", "id": tid, "name": name, "input": inp or {"file_path": "/a/b.py"}}


def tool_result(tid, text):
    return {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "content": text}]}}


def compact(trigger="auto", pre=3000, post=800):
    return {"type": "system", "subtype": "compact_boundary", "compactMetadata": {"trigger": trigger, "preTokens": pre, "postTokens": post}}


class Base(TmpCase):
    def audit(self, events, *extra, code=0):
        p = run(PATH, write_jsonl(self.tmp / "s.jsonl", events), *extra)
        self.assertExit(p, code)
        return p

    @staticmethod
    def section(out, start, end=None):
        i = out.index(start)
        j = out.index(end, i) if end else len(out)
        return out[i:j]


class BasicTests(Base):
    def test_help_documents_input_format(self):
        p = run(PATH, "--help")
        self.assertExit(p, 0)
        self.assertIn("Claude Code session logs", p.stdout)
        self.assertIn("stream-json", p.stdout)
        self.assertIn("--files", p.stdout)

    def test_basic_measurements(self):
        ev = [asst("m1", 10000, cache_read=0), asst("m2", 12000, cache_read=10000), asst("m3", 15000, cache_read=12000)]
        p = self.audit(ev).stdout
        self.assertIn("model calls: 3", p)
        self.assertIn("fixed overhead (1st call context): 10,000 tokens  [measured]", p)
        self.assertIn("context at end: 15,000   peak: 15,000", p)
        self.assertIn("segment 1 (calls #1-#3): 10,000 -> 15,000 (+5,000 in 3 calls)", p)
        # cache read share = (0+10000+12000) / (10000+12000+15000)
        self.assertIn("cache-read share of all input: 59%", p)

    def test_context_sums_input_cache_creation_and_cache_read(self):
        ev = [{"type": "assistant", "message": {"id": "a", "usage": {"input_tokens": 5, "cache_creation_input_tokens": 700,
                                                                    "cache_read_input_tokens": 9000, "output_tokens": 1}}}]
        self.assertIn("fixed overhead (1st call context): 9,705", self.audit(ev).stdout)

    def test_peak_differs_from_end(self):
        ev = [asst("a", 1000), asst("b", 9000), asst("c", 4000)]
        p = self.audit(ev).stdout
        self.assertIn("context at end: 4,000   peak: 9,000", p)

    def test_malformed_lines_and_blank_lines_skipped(self):
        path = self.tmp / "s.jsonl"
        path.write_text("not json\n\n" + json.dumps(asst("a", 500)) + "\n{broken\n")
        p = run(PATH, path)
        self.assertExit(p, 0)
        self.assertIn("model calls: 1", p.stdout)

    def test_fixed_overhead_breakdown_from_pre_call_events(self):
        ev = [{"type": "user", "message": {"content": "x" * 4000}}, asst("a", 20000)]
        p = self.audit(ev).stdout
        self.assertIn("of which logged before call #1 [estimate]: user/injected text ~1,000", p)
        self.assertIn("the other ~19,000 is system prompt", p)


class NoUsageTests(Base):
    def test_no_assistant_usage_exits_with_error(self):
        ev = [{"type": "user", "message": {"content": "hi"}},
              {"type": "assistant", "message": {"id": "a", "content": [{"type": "text", "text": "x"}]}}]
        p = self.audit(ev, code=1)
        self.assertIn("no assistant usage found", p.stderr)
        self.assertNotIn("Traceback", p.stderr)

    def test_empty_file_exits_with_error(self):
        path = self.tmp / "e.jsonl"
        path.write_text("")
        p = run(PATH, path)
        self.assertExit(p, 1)
        self.assertIn("no assistant usage found", p.stderr)

    def test_empty_usage_dict_counts_as_no_usage(self):
        ev = [{"type": "assistant", "message": {"id": "a", "usage": {}}}]
        self.audit(ev, code=1)

    def test_missing_file_fails(self):
        self.assertNotEqual(run(PATH, self.tmp / "nope.jsonl").returncode, 0)

    def test_all_zero_usage_does_not_crash(self):
        # regression: all-zero input usage used to raise ZeroDivisionError in the cache-read share line
        ev = [{"type": "assistant", "message": {"id": "a", "usage": {"input_tokens": 0, "output_tokens": 5}}}]
        p = self.audit(ev)
        self.assertNotIn("Traceback", p.stderr)
        self.assertExit(p, 0)


class CompactionTests(Base):
    def events(self):
        return [asst("a", 1000), asst("b", 2000), asst("c", 3000), compact("auto", 3000, 800),
                asst("d", 1500), asst("e", 1800)]

    def test_segments_split_across_compaction(self):
        p = self.audit(self.events()).stdout
        self.assertIn("segment 1 (calls #1-#3): 1,000 -> 3,000 (+2,000 in 3 calls)", p)
        self.assertIn("segment 2 (calls #4-#5): 1,500 -> 1,800 (+300 in 2 calls)", p)
        self.assertIn("compaction after call #3 (auto): 3,000 -> 800 tokens", p)
        self.assertNotIn("segment 3", p)

    def test_two_compactions_make_three_segments(self):
        ev = self.events() + [compact("manual", 1800, 500), asst("f", 700)]
        p = self.audit(ev).stdout
        self.assertIn("segment 3 (calls #6-#6): 700 -> 700 (+0 in 1 calls)", p)
        self.assertIn("compaction after call #5 (manual)", p)

    def test_compaction_without_metadata(self):
        ev = [asst("a", 1000), {"type": "system", "subtype": "compact_boundary"}, asst("b", 400)]
        p = self.audit(ev).stdout
        self.assertIn("compaction after call #1 (?)", p)
        self.assertIn("segment 2", p)

    def test_unlogged_drop_reported_only_without_compaction_events(self):
        ev = [asst("a", 10000), asst("b", 11000), asst("c", 3000)]
        self.assertIn("context drops (likely compaction/clear): #3 11,000->3,000", self.audit(ev).stdout)
        self.assertNotIn("context drops", self.audit(self.events()).stdout)

    def test_drop_threshold_is_30_percent(self):
        ev = [asst("a", 10000), asst("b", 7000)]  # exactly 0.7x -> not below
        self.assertNotIn("context drops", self.audit(ev).stdout)


class DoubleCountTests(Base):
    def test_same_message_id_counted_once(self):
        # Claude Code logs one event per content block, repeating message.id and usage
        a1 = asst("m1", 5000, blocks=[{"type": "text", "text": "thinking"}])
        a2 = asst("m1", 5000, blocks=[tool_use("t1")])
        ev = [a1, a2, tool_result("t1", "y" * 400), asst("m2", 5600)]
        p = self.audit(ev).stdout
        self.assertIn("model calls: 2", p)

    def test_same_uuid_used_when_message_id_missing(self):
        e1 = {"type": "assistant", "uuid": "same", "message": {"usage": {"input_tokens": 100, "output_tokens": 1}}}
        self.assertIn("model calls: 1", self.audit([e1, dict(e1)]).stdout)

    def test_own_tool_call_not_counted_as_entering_context_again(self):
        # the model's own tool_use input is already inside the previous call's output_tokens
        big_input = {"file_path": "/x", "content": "z" * 8000}
        ev = [asst("m1", 5000, out=3000, blocks=[tool_use("t1", "Write", **big_input)]),
              tool_result("t1", "ok"),
              asst("m2", 8100)]
        p = self.audit(ev).stdout
        entered = self.section(p, "what entered the context after call #1", "biggest context jumps")
        self.assertNotIn("own call", entered)
        self.assertIn("own output of previous call (output_tokens)", entered)
        self.assertIn("~   3,000  own output of previous call", entered)

    def test_dedup_does_not_double_count_own_output(self):
        ev = [asst("m1", 5000, out=700, blocks=[tool_use("t1")]), asst("m1", 5000, out=700), tool_result("t1", "r"), asst("m2", 5800)]
        p = self.audit(ev).stdout
        entered = self.section(p, "what entered the context after call #1", "biggest context jumps")
        self.assertIn("~     700  own output", entered)


class AttachmentAndInjectionTests(Base):
    def test_attachments_are_counted(self):
        att = {"type": "file", "filename": "big.md", "content": "q" * 4000}
        size = len(json.dumps(att)) // 4
        ev = [asst("a", 5000), {"type": "attachment", "attachment": att}, asst("b", 5000 + size + 100)]
        p = self.audit(ev).stdout
        entered = self.section(p, "what entered the context after call #1", "biggest context jumps")
        self.assertIn(f"~{size:>8,}  attachment: file", entered)
        jumps = self.section(p, "biggest context jumps", "largest tool results")
        self.assertIn(f"attachment: file (~{size:,})", jumps)

    def test_attachment_before_first_call_is_in_fixed_overhead_breakdown(self):
        ev = [{"type": "attachment", "attachment": {"type": "memory", "content": "m" * 2000}}, asst("a", 9000)]
        self.assertIn("attachment: memory ~", self.audit(ev).stdout)

    def test_attachment_without_type(self):
        ev = [asst("a", 100), {"type": "attachment"}, asst("b", 200)]
        self.assertIn("attachment: ?", self.audit(ev).stdout)

    def test_user_text_injection_counted(self):
        ev = [asst("a", 1000), {"type": "user", "message": {"content": "s" * 2000}},
              {"type": "user", "message": {"content": [{"type": "text", "text": "k" * 800}]}}, asst("b", 2000)]
        entered = self.section(self.audit(ev).stdout, "what entered the context after call #1", "biggest context jumps")
        self.assertIn("~     500  user/injected text", entered)
        self.assertIn("~     200  user/injected text (e.g. skill body)", entered)


class ToolResultTests(Base):
    def events(self):
        return [asst("a", 5000, blocks=[tool_use("t1", "Read", file_path="/a.py"), tool_use("t2", "Bash", command="ls")]),
                tool_result("t1", "x" * 40000), tool_result("t2", [{"type": "text", "text": "y" * 400}]),
                asst("b", 5000 + 10500)]

    def test_tool_results_sized_and_named(self):
        p = self.audit(self.events()).stdout
        self.assertIn("tool results: 2, ~10,100 tokens total, ~65% of final context", p)
        largest = self.section(p, "largest tool results")
        self.assertRegex(largest, r"~\s+10,000  Read \{")
        self.assertRegex(largest, r"~\s+100  Bash")
        self.assertLess(largest.index("Read"), largest.index("Bash"))

    def test_big_result_is_attributed_to_biggest_jump(self):
        jumps = self.section(self.audit(self.events()).stdout, "biggest context jumps", "largest tool results")
        self.assertRegex(jumps, r"#2\s+\+10,500\s+Read .* \(~10,000\)")

    def test_tool_result_without_matching_call_is_unknown(self):
        ev = [asst("a", 100), tool_result("ghost", "x" * 400), asst("b", 300)]
        self.assertRegex(self.audit(ev).stdout, r"~\s+100  \?")

    def test_top_limits_rows(self):
        ev = [asst("a", 100, blocks=[tool_use(f"t{i}", "Read", file_path=f"/f{i}") for i in range(5)])]
        ev += [tool_result(f"t{i}", "x" * (400 * (i + 1))) for i in range(5)] + [asst("b", 5000)]
        largest = self.section(self.audit(ev, "--top", "2").stdout, "largest tool results")
        self.assertEqual(sum(1 for l in largest.splitlines() if l.startswith("  ~")), 2)

    def test_no_jumps_when_context_never_grows(self):
        ev = [asst("a", 1000), asst("b", 1000)]
        jumps = self.section(self.audit(ev).stdout, "biggest context jumps", "largest tool results")
        self.assertEqual(len([l for l in jumps.splitlines() if l.startswith("  #")]), 0)


class FilesTests(Base):
    def test_files_sized_and_missing_reported(self):
        f = self.tmp / "CLAUDE.md"
        f.write_text("c" * 4000)
        p = self.audit([asst("a", 100)], "--files", str(f), str(self.tmp / "nope.md")).stdout
        self.assertIn("static files [estimate]:", p)
        self.assertRegex(p, r"~\s+1,000  .*CLAUDE\.md")
        self.assertRegex(p, r"missing  .*nope\.md")

    def test_no_files_section_by_default(self):
        self.assertNotIn("static files", self.audit([asst("a", 100)]).stdout)


class RandomizedTests(Base):
    def test_random_session_is_consistent(self):
        rng = random.Random(42)
        ev, ctx, expect_calls = [], 8000, 0
        for i in range(40):
            tid = f"t{i}"
            ev.append(asst(f"m{i}", ctx, out=rng.randint(10, 500), cache_read=max(ctx - 500, 0),
                           blocks=[tool_use(tid, "Grep", pattern=f"p{i}")]))
            expect_calls += 1
            n = rng.randint(100, 4000)
            ev.append(tool_result(tid, "r" * n))
            ctx += n // 4 + rng.randint(0, 50)
        p = self.audit(ev).stdout
        self.assertIn(f"model calls: {expect_calls}", p)
        self.assertIn("fixed overhead (1st call context): 8,000", p)
        self.assertIn(f"tool results: {expect_calls}", p)
        self.assertIn("cache-read share of all input:", p)


if __name__ == "__main__":
    unittest.main()
