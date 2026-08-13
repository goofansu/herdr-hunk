"""Deterministic comment routing from Hunk to its paired agent."""

from __future__ import annotations

import json
import os
import unittest

from tests.support import PluginTestCase, hunk_session, pane_info

NOTES = {
    "comments": [
        {
            "noteId": "n1",
            "source": "user",
            "filePath": "src/review.py",
            "newRange": [42, 42],
            "body": "This branch never runs.",
            "title": "Dead branch",
            "createdAt": "2026-08-13T00:00:00Z",
        },
        {
            "noteId": "n2",
            "source": "user",
            "filePath": "README.md",
            "oldRange": [7, 9],
            "body": "Stale instructions.",
            "createdAt": "2026-08-13T00:01:00Z",
        },
    ]
}


class SendCommentsTest(PluginTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.stub_checkout()
        self.seed_pair()

    def seed_pair(
        self,
        origin="w1:p2",
        review="w1:p9",
        session_id="s1",
        target=None,
        origin_overrides=None,
        review_overrides=None,
        note_targets=None,
    ) -> dict:
        origin_pane = pane_info(origin, agent="claude", agent_status="idle")
        origin_pane.update(origin_overrides or {})
        review_pane = pane_info(review)
        review_pane.update(review_overrides or {})
        record = {
            "origin_pane": origin,
            "origin_terminal_id": origin_pane["terminal_id"],
            "review_pane": review,
            "review_terminal_id": review_pane["terminal_id"],
            "plugin_pane": True,
            "session_id": session_id,
            "target": target or ["diff"],
        }
        if isinstance(origin_pane.get("agent_session"), dict):
            record["origin_agent_session"] = origin_pane["agent_session"]
        if note_targets:
            record["note_targets"] = note_targets
        self.write_pane_map({self.checkout: [record]})
        self.herdr_result(
            ["pane", "get", review],
            {"type": "pane_info", "pane": review_pane},
        )
        self.herdr_result(
            ["pane", "get", origin],
            {"type": "pane_info", "pane": origin_pane},
        )
        self.stub_live_session(session_id=session_id)
        self.stub_notes()
        return record

    def stub_notes(self, payload=NOTES, stderr=None, exit_code=0) -> None:
        rule = {
            "program": "hunk",
            "match": ["session", "comment", "list"],
            "stdout": "" if stderr else json.dumps(payload),
            "stderr": stderr or "",
            "exit": exit_code,
            "after": 0,
        }
        # The fake's ordered-subsequence matcher would otherwise let the broad
        # `session list --json` rule consume `session comment list ... --json`.
        self.rules.insert(0, rule)

    def paired_context(self, **overrides) -> dict:
        values = {
            "focused_pane_id": "w1:p9",
            "focused_pane_agent": None,
        }
        values.update(overrides)
        return self.context(**values)

    def notes_file(self, index=0) -> str:
        text = self.calls_matching("herdr", "send-text")[index][-1]
        for token in text.split():
            if token.endswith(".md"):
                return token
        raise AssertionError(f"no notes path in staged text: {text!r}")

    def read_notes(self) -> str:
        with open(self.notes_file(), encoding="utf-8") as handle:
            return handle.read()

    def test_lists_user_notes_by_exact_session_id(self) -> None:
        self.assertSucceeded(self.run_plugin("send-comments", self.paired_context()))
        listing = self.calls_matching("hunk", "comment", "list")[0]
        self.assertEqual(
            listing,
            [
                "hunk",
                "session",
                "comment",
                "list",
                "s1",
                "--type",
                "user",
                "--json",
            ],
        )
        self.assertNotIn("--repo", listing)

    def test_routes_directly_to_the_paired_agent(self) -> None:
        self.assertSucceeded(self.run_plugin("send-comments", self.paired_context()))
        self.assertEqual(
            self.calls_matching("herdr", "send-text")[0][:4],
            ["herdr", "pane", "send-text", "w1:p2"],
        )
        self.assertEqual(self.calls_matching("herdr", "pane", "list"), [])

    def test_moved_hunk_still_routes_to_its_agent_in_another_workspace(self) -> None:
        self.rules = []
        self.stub_checkout()
        self.seed_pair(
            origin="w2:p2",
            review="w1:p9",
            origin_overrides={"workspace_id": "w2", "tab_id": "w2:t3"},
            review_overrides={
                "pane_id": "w3:p4",
                "workspace_id": "w3",
                "tab_id": "w3:t8",
            },
        )
        result = self.run_plugin(
            "send-comments",
            self.paired_context(workspace_id="w3", tab_id="w3:t8"),
        )
        self.assertSucceeded(result)
        self.assertEqual(self.calls_matching("herdr", "send-text")[0][3], "w2:p2")

    def test_launch_time_alias_still_resolves_after_state_learns_the_moved_id(
        self,
    ) -> None:
        self.rules = []
        self.stub_checkout()
        self.seed_pair(
            origin="w2:p2",
            review="w3:p4",
            origin_overrides={"workspace_id": "w2", "tab_id": "w2:t3"},
            review_overrides={"workspace_id": "w3", "tab_id": "w3:t8"},
        )
        canonical = pane_info(
            "w3:p4",
            terminal_id="term_w3_p4",
            workspace_id="w3",
            tab_id="w3:t8",
        )
        self.herdr_result(
            ["pane", "get", "w1:p9"],
            {"type": "pane_info", "pane": canonical},
        )
        result = self.run_plugin(
            "send-comments",
            self.paired_context(
                focused_pane_id="w1:p9", workspace_id="w3", tab_id="w3:t8"
            ),
        )
        self.assertSucceeded(result)
        self.assertEqual(self.calls_matching("herdr", "send-text")[0][3], "w2:p2")

    def test_each_hunk_in_one_checkout_routes_to_its_own_agent(self) -> None:
        first = self.pane_map()[self.checkout][0]
        second_origin = pane_info("w1:p7", agent="amp")
        second_review = pane_info("w1:p8")
        second = {
            "origin_pane": "w1:p7",
            "origin_terminal_id": second_origin["terminal_id"],
            "review_pane": "w1:p8",
            "review_terminal_id": second_review["terminal_id"],
            "plugin_pane": True,
            "session_id": "s2",
            "target": ["show"],
        }
        self.write_pane_map({self.checkout: [first, second]})
        self.herdr_result(
            ["pane", "get", "w1:p8"],
            {"type": "pane_info", "pane": second_review},
        )
        self.herdr_result(
            ["pane", "get", "w1:p7"],
            {"type": "pane_info", "pane": second_origin},
        )
        session = hunk_session(self.checkout, "s2", 102)
        self.rule(
            "hunk",
            ["session", "get", "s2", "--json"],
            stdout=json.dumps({"session": session}),
        )
        result = self.run_plugin(
            "send-comments",
            self.paired_context(focused_pane_id="w1:p8"),
        )
        self.assertSucceeded(result)
        self.assertEqual(self.calls_matching("herdr", "send-text")[0][3], "w1:p7")

    def test_invoking_from_an_agent_uses_only_that_agents_pair(self) -> None:
        context = self.context(focused_pane_id="w1:p2", focused_pane_agent="claude")
        self.assertSucceeded(self.run_plugin("send-comments", context))
        self.assertEqual(self.calls_matching("herdr", "send-text")[0][3], "w1:p2")

    def test_shell_with_one_agent_can_send_that_agents_paired_review(self) -> None:
        agent = pane_info("w1:p2", agent="claude")
        self.herdr_result(
            ["pane", "list", "--workspace"],
            {"type": "pane_list", "panes": [agent]},
        )
        context = self.context(focused_pane_id="w1:pSHELL", focused_pane_agent=None)
        result = self.run_plugin("send-comments", context)
        self.assertSucceeded(result)
        self.assertEqual(self.calls_matching("herdr", "send-text")[0][3], "w1:p2")

    def test_shell_with_multiple_agents_refuses_to_guess_a_pair(self) -> None:
        agents = [
            pane_info("w1:p2", agent="claude"),
            pane_info("w1:p7", agent="amp"),
        ]
        self.herdr_result(
            ["pane", "list", "--workspace"],
            {"type": "pane_list", "panes": agents},
        )
        context = self.context(focused_pane_id="w1:pSHELL", focused_pane_agent=None)
        result = self.run_plugin("send-comments", context)
        self.assertFailed(result, "multiple agent panes", "w1:p2", "w1:p7")
        self.assertEqual(self.calls_matching("herdr", "send-text"), [])

    def test_stale_paired_agent_fails_without_workspace_fallback(self) -> None:
        self.rules = []
        self.stub_checkout()
        self.write_pane_map(
            {
                self.checkout: [
                    {
                        "origin_pane": "w1:pGONE",
                        "review_pane": "w1:p9",
                        "plugin_pane": True,
                        "session_id": "s1",
                        "target": ["diff"],
                    }
                ]
            }
        )
        self.herdr_result(
            ["pane", "get", "w1:p9"],
            {"type": "pane_info", "pane": pane_info("w1:p9")},
        )
        self.rule("herdr", ["pane", "get", "w1:pGONE"], stderr="gone\n", exit_code=1)
        self.stub_live_session()
        self.stub_notes()
        result = self.run_plugin("send-comments", self.paired_context())
        self.assertFailed(result, "paired agent is gone or was replaced")
        self.assertEqual(self.calls_matching("herdr", "send-text"), [])
        self.assertEqual(self.calls_matching("herdr", "pane", "list"), [])

    def test_replaced_paired_agent_fails_without_takeover(self) -> None:
        self.rules = []
        self.stub_checkout()
        self.seed_pair(origin_overrides={"terminal_id": "old"})
        replacement = pane_info("w1:p2", agent="claude", terminal_id="new")
        self.rules.insert(
            0,
            {
                "program": "herdr",
                "match": ["pane", "get", "w1:p2"],
                "stdout": json.dumps(
                    {"id": "cli:test", "result": {"pane": replacement}}
                ),
                "stderr": "",
                "exit": 0,
                "after": 0,
            },
        )
        result = self.run_plugin("send-comments", self.paired_context())
        self.assertFailed(result, "paired agent is gone or was replaced")
        self.assertEqual(self.calls_matching("herdr", "send-text"), [])

    def test_replacement_agent_cannot_claim_the_stale_pairs_sole_session(
        self,
    ) -> None:
        self.rules = []
        self.stub_checkout()
        self.seed_pair(origin_overrides={"terminal_id": "old"})
        replacement = pane_info("w1:p2", agent="claude", terminal_id="new")
        self.rules.insert(
            0,
            {
                "program": "herdr",
                "match": ["pane", "get", "w1:p2"],
                "stdout": json.dumps(
                    {"id": "cli:test", "result": {"pane": replacement}}
                ),
                "stderr": "",
                "exit": 0,
                "after": 0,
            },
        )
        result = self.run_plugin(
            "send-comments",
            self.context(focused_pane_id="w1:p2", focused_pane_agent="claude"),
        )
        self.assertFailed(result, "belongs to another agent pair")
        self.assertEqual(self.calls_matching("herdr", "send-text"), [])

    def test_changed_native_agent_session_fails_without_takeover(self) -> None:
        self.rules = []
        self.stub_checkout()
        self.seed_pair(origin_overrides={"agent_session": {"id": "old"}})
        replacement = pane_info("w1:p2", agent="claude", agent_session={"id": "new"})
        self.rules.insert(
            0,
            {
                "program": "herdr",
                "match": ["pane", "get", "w1:p2"],
                "stdout": json.dumps(
                    {"id": "cli:test", "result": {"pane": replacement}}
                ),
                "stderr": "",
                "exit": 0,
                "after": 0,
            },
        )
        result = self.run_plugin(
            "send-comments",
            self.context(focused_pane_id="w1:p2", focused_pane_agent="claude"),
        )
        self.assertFailed(result, "paired agent is gone or was replaced")
        self.assertEqual(self.calls_matching("herdr", "send-text"), [])

    def test_markdown_includes_checkout_target_and_locations(self) -> None:
        self.assertSucceeded(self.run_plugin("send-comments", self.paired_context()))
        body = self.read_notes()
        self.assertIn("# Hunk review notes", body)
        self.assertIn(f"Checkout: {self.checkout}", body)
        self.assertIn("Current review target: `hunk diff`", body)
        self.assertIn("## src/review.py:42", body)
        self.assertIn("Review target: `hunk diff`", body)
        self.assertIn("**Dead branch**", body)
        self.assertIn("## README.md:7", body)

    def test_prior_note_target_is_preserved_after_switching_reviews(self) -> None:
        self.rules = []
        self.stub_checkout()
        self.seed_pair(target=["show"], note_targets={"n1": ["diff", "main"]})
        self.assertSucceeded(self.run_plugin("send-comments", self.paired_context()))
        body = self.read_notes()
        first = body.index("## src/review.py:42")
        second = body.index("## README.md:7")
        self.assertIn("Review target: `hunk diff main`", body[first:second])
        self.assertIn("Review target: `hunk show`", body[second:])

    def test_uncertain_context_applies_only_to_notes_visible_at_recovery(self) -> None:
        state = self.pane_map()
        state[self.checkout][0]["note_context_uncertain"] = True
        self.write_pane_map(state)
        self.rules[0]["stdout"] = json.dumps({"comments": [NOTES["comments"][0]]})

        self.assertSucceeded(self.run_plugin("send-comments", self.paired_context()))
        record = self.pane_map()[self.checkout][0]
        self.assertIn("unknown (review target changed", record["note_targets"]["n1"])
        self.assertNotIn("note_context_uncertain", record)

        self.rules[0]["stdout"] = json.dumps(NOTES)
        self.assertSucceeded(self.run_plugin("send-comments", self.paired_context()))
        record = self.pane_map()[self.checkout][0]
        self.assertEqual(record["note_targets"]["n2"], ["diff"])

    def test_staging_is_one_line_and_never_submits(self) -> None:
        self.assertSucceeded(self.run_plugin("send-comments", self.paired_context()))
        text = self.calls_matching("herdr", "send-text")[0][-1]
        self.assertNotIn("\n", text)
        self.assertNotIn("\r", text)
        self.assertEqual(self.calls_matching("herdr", "send-keys"), [])
        self.assertEqual(self.calls_matching("herdr", "agent", "prompt"), [])

    def test_agent_is_focused_after_staging(self) -> None:
        self.assertSucceeded(self.run_plugin("send-comments", self.paired_context()))
        self.assertIn(["herdr", "agent", "focus", "w1:p2"], self.calls("herdr"))

    def test_focus_failure_does_not_undo_staging(self) -> None:
        self.rule("herdr", ["agent", "focus"], stderr="agent_not_found\n", exit_code=1)
        result = self.run_plugin("send-comments", self.paired_context())
        self.assertSucceeded(result)
        self.assertEqual(len(self.calls_matching("herdr", "send-text")), 1)
        self.assertIn("could not focus", result.stderr)

    def test_identical_notes_are_not_staged_twice(self) -> None:
        first = self.run_plugin("send-comments", self.paired_context())
        second = self.run_plugin("send-comments", self.paired_context())
        self.assertSucceeded(first)
        self.assertSucceeded(second)
        self.assertEqual(len(self.calls_matching("herdr", "send-text")), 1)
        self.assertEqual(len(self.calls_matching("herdr", "agent", "focus")), 2)
        self.assertIn("already staged", second.stdout)

    def test_changed_notes_are_staged_again(self) -> None:
        self.assertSucceeded(self.run_plugin("send-comments", self.paired_context()))
        changed = {"comments": [*NOTES["comments"], {"noteId": "n3", "body": "new"}]}
        self.rules[0]["stdout"] = json.dumps(changed)
        self.assertSucceeded(self.run_plugin("send-comments", self.paired_context()))
        self.assertEqual(len(self.calls_matching("herdr", "send-text")), 2)

    def test_empty_notes_fail_without_staging(self) -> None:
        self.rules[0]["stdout"] = json.dumps({"comments": []})
        result = self.run_plugin("send-comments", self.paired_context())
        self.assertFailed(result, "no review notes", "s1")
        self.assertEqual(self.calls_matching("herdr", "send-text"), [])

    def test_unreadable_notes_fail_without_staging(self) -> None:
        self.rules[0]["stdout"] = "not json"
        result = self.run_plugin("send-comments", self.paired_context())
        self.assertFailed(result, "unreadable note list")
        self.assertEqual(self.calls_matching("herdr", "send-text"), [])

    def test_dead_exact_session_fails_without_using_another_repo_session(self) -> None:
        self.rules = [
            rule
            for rule in self.rules
            if not (
                rule["program"] == "hunk"
                and rule["match"] == ["session", "get", "s1", "--json"]
            )
        ]
        self.rule("hunk", ["session", "get", "s1"], stderr="gone\n", exit_code=1)
        other = hunk_session(self.checkout, "other", 303)
        for rule in self.rules:
            if rule["program"] == "hunk" and rule["match"] == [
                "session",
                "list",
                "--json",
            ]:
                rule["stdout"] = json.dumps({"sessions": [other]})
        result = self.run_plugin("send-comments", self.paired_context())
        self.assertFailed(result, "no live Hunk session for the pair")
        self.assertEqual(self.calls_matching("hunk", "comment", "list"), [])

    def test_notes_file_lives_in_plugin_state(self) -> None:
        self.assertSucceeded(self.run_plugin("send-comments", self.paired_context()))
        self.assertTrue(self.notes_file().startswith(self.state_dir + os.sep))

    def test_manual_hunk_with_one_session_can_send_to_invoking_agent(self) -> None:
        self.write_pane_map({})
        self.rules = []
        self.stub_checkout()
        self.stub_notes()
        session = hunk_session(self.checkout, "manual", 501)
        self.rule(
            "hunk", ["session", "list"], stdout=json.dumps({"sessions": [session]})
        )
        self.rules[0]["match"] = ["session", "comment", "list", "manual"]
        context = self.context(focused_pane_id="w1:p4", focused_pane_agent="amp")
        result = self.run_plugin("send-comments", context)
        self.assertSucceeded(result)
        self.assertEqual(self.calls_matching("herdr", "send-text")[0][3], "w1:p4")

    def test_manual_hunk_uses_its_process_to_disambiguate_sessions(self) -> None:
        self.write_pane_map({})
        self.rules = []
        self.stub_checkout()
        self.stub_notes()
        sessions = [
            hunk_session(self.checkout, "other", 700),
            hunk_session(self.checkout, "here", 101),
        ]
        self.rule(
            "hunk", ["session", "list"], stdout=json.dumps({"sessions": sessions})
        )
        self.rules[0]["match"] = ["session", "comment", "list", "here"]
        self.stub_process_info("w1:p9", foreground="hunk")
        agent = pane_info("w1:p2", agent="claude")
        self.herdr_result(
            ["pane", "list", "--workspace"],
            {"type": "pane_list", "panes": [agent]},
        )
        result = self.run_plugin("send-comments", self.paired_context())
        self.assertSucceeded(result)
        listing = self.calls_matching("hunk", "comment", "list")[0]
        self.assertIn("here", listing)

    def test_manual_hunk_reports_genuine_session_ambiguity(self) -> None:
        self.write_pane_map({})
        self.rules = []
        self.stub_checkout()
        self.stub_notes()
        sessions = [
            hunk_session(self.checkout, "one", 601),
            hunk_session(self.checkout, "two", 602),
        ]
        self.rule(
            "hunk", ["session", "list"], stdout=json.dumps({"sessions": sessions})
        )
        agent = pane_info("w1:p2", agent="claude")
        self.herdr_result(
            ["pane", "list", "--workspace"],
            {"type": "pane_list", "panes": [agent]},
        )
        result = self.run_plugin("send-comments", self.paired_context())
        self.assertFailed(result, "multiple live Hunk sessions", "one", "two")
        self.assertEqual(self.calls_matching("herdr", "send-text"), [])

    def test_unexpected_note_range_still_names_the_file(self) -> None:
        self.rules[0]["stdout"] = json.dumps(
            {
                "comments": [
                    {
                        "noteId": "odd",
                        "filePath": "src/odd.py",
                        "newRange": {"start": 3},
                        "body": "Still worth reading.",
                    }
                ]
            }
        )
        self.assertSucceeded(self.run_plugin("send-comments", self.paired_context()))
        self.assertIn("## src/odd.py", self.read_notes())


if __name__ == "__main__":
    unittest.main()
