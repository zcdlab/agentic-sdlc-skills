"""Tests for skills/2-build-review-tools/lint_tools.py (not target_tool_choice.py, which needs the anthropic SDK)."""
import json
import unittest

from helpers import TmpCase, run, script, write_json

L = script("2-build-review-tools", "lint_tools.py")

DESC = "Use this to do the thing for a customer; returns a short summary, not for bulk operations."


def prop(desc="A well described parameter, e.g. T-123.", typ="string", **kw):
    return {"type": typ, "description": desc, **kw}


def tool(name, desc=DESC, props=None, required=None, key="input_schema"):
    props = {"ticket_id": prop()} if props is None else props
    req = list(props) if required is None else required
    return {"name": name, "description": desc, key: {"type": "object", "properties": props, "required": req}}


def clean_set():
    return [
        tool("tickets_get", props={"ticket_id": prop()}),
        tool("tickets_search", props={"query": prop("Free text to search ticket titles."),
                                      "limit": prop("Max rows to return, default 20.", "integer")},
             required=["query"]),
        tool("tickets_close", props={"ticket_id": prop(), "reason": prop("Why it is closed, one sentence.")}),
        tool("tickets_assign", props={"ticket_id": prop(), "assignee_email": prop("Email of the new owner.")}),
    ]


class Lint(TmpCase):
    def lint(self, tools, code=None):
        p = run(L, write_json(self.tmp / "tools.json", tools))
        if code is not None:
            self.assertExit(p, code)
        return p

    def finding(self, p, sev, name, needle):
        lines = [l for l in p.stdout.splitlines() if l.startswith(sev) and f" {name}:" in l and needle in l]
        self.assertTrue(lines, f"no {sev} finding on {name!r} containing {needle!r}:\n{p.stdout}")

    def test_help(self):
        p = run(L, "--help")
        self.assertExit(p, 0)
        self.assertIn("lint_tools.py tools.json", p.stdout)
        self.assertIn("Exit code 1 if any HIGH", p.stdout)

    def test_no_args_errors(self):
        self.assertEqual(run(L).returncode, 2)

    def test_clean_set_exits_zero(self):
        p = self.lint(clean_set(), 0)
        self.assertIn("4 tools, 0 findings (0 HIGH)", p.stdout)
        self.assertNotIn("HIGH ", p.stdout.split("\n\n")[0])

    def test_high_finding_exits_one_and_summary(self):
        p = self.lint(clean_set() + [tool("run")], 1)
        self.assertRegex(p.stdout, r"5 tools, \d+ findings \([1-9]\d* HIGH\)")

    def test_med_and_low_only_exit_zero(self):
        t = tool("tickets_get", props={"id": prop()})  # MED ambiguous only
        p = self.lint([t], 0)
        self.assertIn("MED", p.stdout)
        self.assertIn("0 HIGH", p.stdout)

    def test_generic_name(self):
        p = self.lint([tool("search", props={"limit": prop(typ="integer")})], 1)
        self.finding(p, "HIGH", "search", "generic name")

    def test_generic_name_case_insensitive(self):
        p = self.lint([tool("Fetch")], 1)
        self.finding(p, "HIGH", "Fetch", "generic name")

    def test_specific_name_not_generic(self):
        p = self.lint([tool("tickets_fetch")], 0)
        self.assertNotIn("generic name", p.stdout)

    def test_short_description(self):
        p = self.lint([tool("tickets_get", desc="Gets a ticket.")], 1)
        self.finding(p, "HIGH", "tickets_get", "description is 3 words")

    def test_missing_description(self):
        t = tool("tickets_get")
        del t["description"]
        p = self.lint([t], 1)
        self.finding(p, "HIGH", "tickets_get", "description is 0 words")

    def test_eight_word_description_ok(self):
        p = self.lint([tool("tickets_get", desc="one two three four five six seven eight")], 0)
        self.assertNotIn("description is", p.stdout)

    def test_destructive_without_safety(self):
        p = self.lint([tool("delete_ticket")], 1)
        self.finding(p, "HIGH", "delete_ticket", "destructive with no dry_run/confirm")

    def test_destructive_namespaced_name_detected(self):
        # regression: DESTRUCTIVE.match() is anchored at the start of the name, so the recommended namespaced form
        # (tickets_delete, db_drop_table) is never seen as destructive; only names that begin with the verb (delete_x) match.
        p = self.lint([tool("tickets_delete")])
        self.finding(p, "HIGH", "tickets_delete", "destructive")

    def test_destructive_name_variants(self):
        for n in ("remove_ticket", "purge_tickets", "drop_table", "reset_db", "wipe"):
            p = self.lint([tool(n)], 1)
            self.finding(p, "HIGH", n, "destructive")

    def test_destructive_with_dry_run_has_no_finding(self):
        t = tool("delete_ticket", props={"ticket_id": prop(), "dry_run": prop("Preview only.", "boolean")},
                 required=["ticket_id", "dry_run"])
        p = self.lint([t], 0)
        self.assertNotIn("destructive", p.stdout)

    def test_destructive_with_each_safety_param(self):
        for s in ("dry_run", "confirm", "confirmation", "preview"):
            t = tool("delete_ticket", props={"ticket_id": prop(), s: prop("safety", "boolean")})
            self.assertNotIn("destructive", self.lint([t]).stdout, s)

    def test_destructive_with_irreversible_warning_has_no_finding(self):
        t = tool("delete_ticket", desc="Permanently deletes a ticket by id. This is irreversible, so confirm first.")
        self.assertNotIn("destructive", self.lint([t], 0).stdout)

    def test_destructive_regex_needs_word_boundary(self):
        # 'undeleted' / 'resetting' must not match
        self.assertNotIn("destructive", self.lint([tool("undelete_ticket")]).stdout)

    def test_list_without_limit(self):
        p = self.lint([tool("tickets_list")], 0)  # name doesn't start with list
        self.assertNotIn("flood", p.stdout)
        p = self.lint([tool("list_tickets")], 1)
        self.finding(p, "HIGH", "list_tickets", "no limit or pagination")

    def test_listish_prefixes(self):
        for n in ("search_tickets", "find_users", "query_logs", "get_all_things", "list_all_x"):
            self.finding(self.lint([tool(n)], 1), "HIGH", n, "no limit or pagination")

    def test_list_with_any_bound_param_ok(self):
        for b in ("limit", "max_results", "page_size", "cursor", "page", "offset", "top_k"):
            t = tool("list_tickets", props={"ticket_id": prop(), b: prop("bound", "integer")})
            self.assertNotIn("flood", self.lint([t]).stdout, b)

    def test_ambiguous_param_is_med(self):
        p = self.lint([tool("tickets_get", props={"id": prop()})], 0)
        self.finding(p, "MED", "tickets_get", "param 'id' is ambiguous")

    def test_each_ambiguous_name_flagged(self):
        for a in ("user", "q", "data", "input", "value", "obj", "item", "fields", "params", "args"):
            p = self.lint([tool("tickets_get", props={a: prop()})])
            self.finding(p, "MED", "tickets_get", f"param '{a}' is ambiguous")

    def test_ambiguous_case_insensitive(self):
        self.finding(self.lint([tool("tickets_get", props={"ID": prop()})]), "MED", "tickets_get", "ambiguous")

    def test_param_without_description_is_med(self):
        p = self.lint([tool("tickets_get", props={"ticket_id": {"type": "string"}})], 0)
        self.finding(p, "MED", "tickets_get", "param 'ticket_id' has no description")

    def test_json_in_string_param_description(self):
        t = tool("tickets_create", props={"details": prop("A JSON object with title and body.")})
        p = self.lint([t], 1)
        self.finding(p, "HIGH", "tickets_create", "param 'details' is JSON packed in a string")

    def test_json_via_payload_param_and_tool_description(self):
        t = tool("tickets_create", desc="Creates a ticket; pass the payload as JSON, returns the new ticket id.",
                 props={"payload": prop("The ticket content.")})
        self.finding(self.lint([t], 1), "HIGH", "tickets_create", "JSON packed in a string")

    def test_json_word_in_tool_desc_only_matters_for_payload_params(self):
        t = tool("tickets_create", desc="Creates a ticket; returns JSON with the new ticket id and url.",
                 props={"title": prop("Ticket title.")})
        self.assertNotIn("JSON packed", self.lint([t], 0).stdout)

    def test_json_typed_object_ok(self):
        t = tool("tickets_create", props={"details": prop("A JSON object with title and body.", "object")})
        self.assertNotIn("JSON packed", self.lint([t]).stdout)

    def test_optional_without_default_is_low(self):
        t = tool("tickets_get", props={"ticket_id": prop(), "verbose": prop("More detail.", "boolean")},
                 required=["ticket_id"])
        p = self.lint([t], 0)
        self.finding(p, "LOW", "tickets_get", "param 'verbose' optional with no default")

    def test_optional_with_default_not_flagged(self):
        t = tool("tickets_get", props={"ticket_id": prop(), "fmt": prop("Format.", default="short")},
                 required=["ticket_id"])
        self.assertNotIn("optional with no default", self.lint([t]).stdout)

    def test_required_param_not_low(self):
        self.assertNotIn("optional with no default", self.lint([tool("tickets_get")]).stdout)

    def test_falsy_default_counts_as_default(self):
        # regression: lint_tools.py uses `not props[p].get("default")`, so an explicit default of false/0/"" is treated as
        # "no default stated" and gets a spurious LOW finding.
        t = tool("tickets_get", props={"ticket_id": prop(), "verbose": prop("More detail.", "boolean", default=False)},
                 required=["ticket_id"])
        self.assertNotIn("optional with no default", self.lint([t]).stdout)

    def test_overlapping_names(self):
        p = self.lint([tool("tickets_get"), tool("tickets_get_all", props={"ticket_id": prop(), "limit": prop(typ="integer")})], 1)
        self.finding(p, "HIGH", "tickets_get / tickets_get_all", "overlapping names")
        self.assertIn("['get', 'tickets']", p.stdout)

    def test_overlap_with_different_separators(self):
        p = self.lint([tool("tickets-get"), tool("tickets.get.all")], 1)
        self.assertIn("overlapping names", p.stdout)

    def test_non_subset_shared_token_not_overlap(self):
        p = self.lint([tool("tickets_get"), tool("tickets_close")], 0)
        self.assertNotIn("overlapping", p.stdout)

    def test_no_shared_prefix_with_more_than_three_tools(self):
        tools = [tool(n) for n in ("alpha_get", "beta_get_x", "gamma_close", "delta_open")]
        p = self.lint(tools, 0)
        self.finding(p, "LOW", "*", "no shared namespace prefix")

    def test_prefix_check_needs_more_than_three_tools(self):
        tools = [tool(n) for n in ("alpha_get", "beta_close", "gamma_open")]
        self.assertNotIn("namespace prefix", self.lint(tools, 0).stdout)

    def test_shared_prefix_not_flagged(self):
        self.assertNotIn("namespace prefix", self.lint(clean_set()).stdout)

    def test_mcp_input_schema_form(self):
        tools = [tool("delete_ticket", key="inputSchema")]
        p = self.lint(tools, 1)
        self.assertIn("destructive", p.stdout)
        # props were read from inputSchema: no description warning means the described param was seen
        self.assertNotIn("has no description", p.stdout)
        tools = [tool("tickets_get", props={"id": prop()}, key="inputSchema")]
        self.finding(self.lint(tools, 0), "MED", "tickets_get", "'id' is ambiguous")

    def test_tools_wrapper_object(self):
        p = self.lint({"tools": clean_set()}, 0)
        self.assertIn("4 tools, 0 findings", p.stdout)
        p = self.lint({"tools": [tool("run")]}, 1)
        self.assertIn("generic name", p.stdout)

    def test_tool_without_schema_does_not_crash(self):
        p = self.lint([{"name": "tickets_get", "description": DESC}], 0)
        self.assertIn("1 tools", p.stdout)

    def test_findings_sorted_high_first(self):
        t = tool("run", props={"id": {"type": "string"}}, required=[])
        sevs = [l.split()[0] for l in self.lint([t], 1).stdout.split("\n\n")[0].splitlines()]
        self.assertEqual(sevs, sorted(sevs, key={"HIGH": 0, "MED": 1, "LOW": 2}.get))
        self.assertEqual(sevs[0], "HIGH")
        self.assertIn("LOW", sevs)

    def test_invalid_json_errors(self):
        (self.tmp / "bad.json").write_text("{not json")
        self.assertNotEqual(run(L, self.tmp / "bad.json").returncode, 0)


if __name__ == "__main__":
    unittest.main()
