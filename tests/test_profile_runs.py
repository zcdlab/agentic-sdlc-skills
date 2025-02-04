"""Tests for skills/5-prod-reduce-cost-latency/profile_runs.py. Traces are generated in-test."""
import random
import unittest

from helpers import TmpCase, run, script, write_json, write_jsonl

PATH = script("5-prod-reduce-cost-latency", "profile_runs.py")

PRICES = {"big": {"in": 10, "out": 50, "cache_write": 12.5, "cache_read": 1}}
TWO = {**PRICES, "small": {"in": 1, "out": 5, "cache_write": 1.25, "cache_read": 0.1}}


def llm(run_id, step, start=0.0, lat=1.0, model="big", inp=1000, out=200, cw=None, cr=None, name="plan", **kw):
    r = {"run_id": run_id, "step": step, "type": "llm", "name": name, "model": model, "start_s": start,
         "latency_s": lat, "input_tokens": inp, "output_tokens": out, **kw}
    if cw is not None:
        r["cache_creation_input_tokens"] = cw
    if cr is not None:
        r["cache_read_input_tokens"] = cr
    return r


def tool(run_id, step, start, lat, result=100, name="search"):
    return {"run_id": run_id, "step": step, "type": "tool", "tool": name, "start_s": start, "latency_s": lat,
            "result_tokens": result}


class Base(TmpCase):
    def profile(self, rows, prices=PRICES, *extra, code=0, after=None):
        t = write_jsonl(self.tmp / "t.jsonl", rows)
        pr = write_json(self.tmp / "prices.json", prices)
        args = [t, "--prices", pr, *extra]
        if after is not None:
            args += ["--compare", write_jsonl(self.tmp / "after.jsonl", after)]
        p = run(PATH, *args)
        self.assertExit(p, code)
        return p


class SummaryTests(Base):
    def test_simple_trace_cost_hand_computed(self):
        # 1000 in * $10/M + 200 out * $50/M = 0.01 + 0.01 = $0.020 ; wall = 2.5 s
        p = self.profile([llm("r1", 1, 0, 2.5)])
        self.assertIn("1 runs: cost/run mean $0.020 p50 $0.020 p95 $0.020; wall time mean 2.5s p50 2.5s p95 2.5s", p.stdout)

    def test_cache_tokens_priced_separately(self):
        # 100 fresh*10 + 600 cw*12.5 + 300 cr*1 + 100 out*50 = 1000+7500+300+5000 = 13800 -> $0.0138
        p = self.profile([llm("r1", 1, inp=100, out=100, cw=600, cr=300)], PRICES)
        self.assertIn("mean $0.014", p.stdout)

    def test_mean_p50_p95_across_runs(self):
        # price in=0,out=1000/M: cost = out_tokens/1000 dollars. 10 runs costing $1..$10 and taking 1..10 s.
        prices = {"m": {"in": 0, "out": 1000}}
        rows = [llm(f"r{i}", 1, 0, float(i), model="m", inp=0, out=1000 * i) for i in range(1, 11)]
        p = self.profile(rows, prices)
        self.assertIn("10 runs: cost/run mean $5.500 p50 $6.000 p95 $10.000; wall time mean 5.5s p50 6.0s p95 10.0s", p.stdout)

    def test_wall_time_spans_first_start_to_last_end_and_tools_cost_nothing(self):
        rows = [llm("r1", 1, 0, 1), tool("r1", 2, 1, 4), llm("r1", 3, 5, 2)]
        p = self.profile(rows)
        self.assertIn("wall time mean 7.0s", p.stdout)
        self.assertIn("mean $0.040", p.stdout)  # two llm steps only
        self.assertIn("tool:search", p.stdout)

    def test_runs_sorted_by_start_s_regardless_of_file_order(self):
        rows = [llm("r1", 3, 5, 2), llm("r1", 1, 0, 1)]
        self.assertIn("wall time mean 7.0s", self.profile(rows).stdout)

    def test_by_step_breakdown(self):
        rows = [llm("r1", 1, 0, 1, name="plan"), llm("r1", 2, 1, 1, name="answer", out=0, inp=0)]
        p = self.profile(rows)
        self.assertIn("by step (per run):", p.stdout)
        self.assertRegex(p.stdout, r"plan\s+\$\s*0\.020 \(100%\)")

    def test_missing_model_in_prices_exits_with_clear_message(self):
        p = self.profile([llm("r1", 1, model="mystery")], PRICES, code=1)
        self.assertIn("no price for model 'mystery' in --prices", p.stderr)
        self.assertNotIn("Traceback", p.stderr)

    def test_missing_price_on_later_run_also_clear(self):
        rows = [llm("r1", 1), llm("r2", 1, model="ghost")]
        p = self.profile(rows, PRICES, code=1)
        self.assertIn("'ghost'", p.stderr)

    def test_help_documents_trace_format(self):
        p = run(PATH, "--help")
        self.assertExit(p, 0)
        for s in ("--compare", "--big-result", "--input-includes-cache", "cache_creation_input_tokens"):
            self.assertIn(s, p.stdout)


class FindingTests(Base):
    def clean(self, n=3):
        return [r for i in range(n) for r in (llm(f"r{i}", 1, 0, 1), tool(f"r{i}", 2, 1, 1), llm(f"r{i}", 3, 2, 1))]

    def test_clean_trace_has_no_findings(self):
        p = self.profile(self.clean())
        self.assertIn("none of the known patterns found", p.stdout)
        self.assertNotIn("overlap", p.stdout)

    def test_cache_written_never_read(self):
        rows = [llm(f"r{i}", s, s, 1, cw=500, cr=0) for i in range(3) for s in (1, 2, 3)]
        p = self.profile(rows)
        self.assertIn("cache written 4,500 tokens but read only 0", p.stdout)
        self.assertNotIn("none of the known patterns", p.stdout)

    def test_cache_written_and_read_is_fine(self):
        rows = [llm(f"r{i}", s, s, 1, cw=500 if s == 1 else 0, cr=0 if s == 1 else 500) for i in range(3) for s in (1, 2, 3)]
        self.assertNotIn("cache written", self.profile(rows).stdout)

    def test_cache_read_threshold_is_ten_percent(self):
        # reads exactly 10% of writes -> not < 10% -> no finding
        rows = [llm("r0", 1, cw=1000, cr=100)]
        self.assertNotIn("cache written", self.profile(rows).stdout)
        rows = [llm("r0", 1, cw=1000, cr=99)]
        self.assertIn("cache written", self.profile(rows).stdout)

    def test_big_tool_result_resent(self):
        rows = [llm("r0", 1, 0, 1), tool("r0", 2, 1, 1, result=8000, name="fetch_page"), llm("r0", 3, 2, 1), llm("r0", 4, 3, 1)]
        p = self.profile(rows)
        self.assertIn("tool:fetch_page returned >= 5,000 tokens in 1 calls", p.stdout)
        # saving = (8000 - 1000) * $10/M * 2 later calls = $0.14
        self.assertIn("~$0.140", p.stdout)

    def test_big_result_threshold_flag(self):
        rows = [llm("r0", 1, 0, 1), tool("r0", 2, 1, 1, result=8000), llm("r0", 3, 2, 1)]
        self.assertNotIn("returned >=", self.profile(rows, PRICES, "--big-result", "9000").stdout)
        self.assertIn("returned >= 7,000", self.profile(rows, PRICES, "--big-result", "7000").stdout)

    def test_small_tool_result_not_flagged(self):
        rows = [llm("r0", 1, 0, 1), tool("r0", 2, 1, 1, result=4999), llm("r0", 3, 2, 1)]
        self.assertNotIn("returned >=", self.profile(rows).stdout)

    def test_serial_tool_calls_in_one_step(self):
        rows = [llm("r0", 1, 0, 1), tool("r0", 2, 1, 2, name="a"), tool("r0", 2, 3, 2, name="b"), llm("r0", 3, 5, 1)]
        p = self.profile(rows)
        self.assertIn("several tool calls in one step run one after another", p.stdout)
        self.assertIn("~2.0s", p.stdout)  # span 4 - longest 2

    def test_parallel_tool_calls_not_flagged(self):
        rows = [llm("r0", 1, 0, 1), tool("r0", 2, 1, 2, name="a"), tool("r0", 2, 1, 2, name="b"), llm("r0", 3, 3, 1)]
        self.assertNotIn("one after another", self.profile(rows).stdout)

    def test_tools_in_different_steps_not_flagged_as_serial(self):
        rows = [llm("r0", 1, 0, 1), tool("r0", 2, 1, 2), tool("r0", 3, 3, 2), llm("r0", 4, 5, 1)]
        self.assertNotIn("one after another", self.profile(rows).stdout)

    def test_slight_serialism_below_half_second_not_flagged(self):
        rows = [tool("r0", 1, 0, 1, name="a"), tool("r0", 1, 1.0, 0.4, name="b"), llm("r0", 2, 2, 1)]
        # span 1.4 - max 1 = 0.4 <= 0.5
        self.assertNotIn("one after another", self.profile(rows).stdout)

    def test_tiny_output_on_non_cheapest_model(self):
        rows = [llm(f"r{i}", 1, 0, 1, model="big", out=10, name="route") for i in range(4)]
        p = self.profile(rows, TWO)
        self.assertIn("step 'route' emits <50 tokens on big: try small", p.stdout)

    def test_tiny_output_on_cheapest_model_not_flagged(self):
        rows = [llm(f"r{i}", 1, 0, 1, model="small", out=10, name="route") for i in range(4)]
        self.assertNotIn("emits <50", self.profile(rows, TWO).stdout)

    def test_large_output_on_expensive_model_not_flagged(self):
        rows = [llm(f"r{i}", 1, 0, 1, model="big", out=50, name="route") for i in range(4)]
        self.assertNotIn("emits <50", self.profile(rows, TWO).stdout)

    def test_tiny_output_in_minority_of_runs_not_flagged(self):
        rows = [llm("r0", 1, model="big", out=10, name="route")] + [llm(f"r{i}", 1, model="big", out=300, name="route") for i in range(1, 4)]
        self.assertNotIn("emits <50", self.profile(rows, TWO).stdout)

    def test_overlap_note_only_with_multiple_findings(self):
        one = [llm(f"r{i}", 1, 0, 1, cw=500, cr=0) for i in range(3)]
        p1 = self.profile(one)
        self.assertIn("cache written", p1.stdout)
        self.assertNotIn("overlap", p1.stdout)
        two = [r for i in range(3) for r in (llm(f"r{i}", 1, 0, 1, cw=500, cr=0), tool(f"r{i}", 2, 1, 2, name="a"),
                                              tool(f"r{i}", 2, 3, 2, name="b"), llm(f"r{i}", 3, 5, 1, cw=500, cr=0))]
        p2 = self.profile(two)
        self.assertIn("cache written", p2.stdout)
        self.assertIn("one after another", p2.stdout)
        self.assertIn("they overlap, don't add them up", p2.stdout)

    def test_randomized_clean_traces_never_flag(self):
        rng = random.Random(11)
        rows = []
        for i in range(8):
            t = 0.0
            for s in range(rng.randint(1, 4)):
                rows.append(llm(f"r{i}", 2 * s, t, rng.uniform(0.5, 2), inp=rng.randint(500, 3000), out=rng.randint(100, 900)))
                t += 3
        self.assertIn("none of the known patterns found", self.profile(rows).stdout)


class LoggedCostTests(Base):
    def logged_rows(self, n=4):
        # logger counts cache tokens inside input_tokens; its cost_usd is the true figure
        true = (100 * 10 + 600 * 12.5 + 300 * 1 + 100 * 50) / 1e6
        return [llm(f"r{i}", 1, 0, 1, inp=1000, out=100, cw=600, cr=300, cost_usd=true) for i in range(n)]

    def test_warn_when_logged_cost_differs_more_than_5_percent(self):
        p = self.profile(self.logged_rows())
        self.assertIn("WARN logged cost_usd $0.014/run differs from computed $0.023/run", p.stdout)
        self.assertIn("--input-includes-cache", p.stdout)

    def test_input_includes_cache_removes_warn(self):
        p = self.profile(self.logged_rows(), PRICES, "--input-includes-cache")
        self.assertNotIn("WARN", p.stdout)
        self.assertIn("mean $0.014", p.stdout)

    def test_no_warn_when_logged_within_5_percent(self):
        rows = [llm("r0", 1, cost_usd=0.0204)]  # computed 0.020, +2%
        self.assertNotIn("WARN", self.profile(rows).stdout)

    def test_warn_just_over_5_percent(self):
        rows = [llm("r0", 1, cost_usd=0.0212)]  # +6%
        p = self.profile(rows)
        self.assertIn("WARN", p.stdout)
        self.assertIn("+6%", p.stdout)

    def test_no_warn_without_logged_cost(self):
        self.assertNotIn("WARN", self.profile([llm("r0", 1)]).stdout)


class CompareTests(Base):
    def runs(self, n, out=200):
        return [llm(f"r{i}", 1, 0, 2.0, out=out) for i in range(n)]

    def test_after_line_with_deltas_and_small_sample_note(self):
        p = self.profile(self.runs(5), PRICES, after=self.runs(5, out=100))
        # after cost: 1000*10 + 100*50 = $0.015 vs $0.020 -> -25%
        self.assertIn("after (5 runs): cost/run mean $0.015 (-25%) p95 $0.015; wall mean 2.0s (+0%) p95 2.0s", p.stdout)
        self.assertIn("(fewer than 20 runs - noisy)", p.stdout)

    def test_no_noisy_note_at_20_runs(self):
        p = self.profile(self.runs(20), PRICES, after=self.runs(20, out=100))
        self.assertIn("after (20 runs)", p.stdout)
        self.assertNotIn("fewer than 20", p.stdout)

    def test_no_after_line_without_compare(self):
        self.assertNotIn("after (", self.profile(self.runs(3)).stdout)

    def test_compare_with_unpriced_model_exits_clearly(self):
        p = self.profile(self.runs(2), PRICES, after=[llm("r0", 1, model="newmodel")], code=1)
        self.assertIn("'newmodel'", p.stderr)


if __name__ == "__main__":
    unittest.main()
