"""Agent–Hunk pair creation, identity, session targeting, and focus."""

from __future__ import annotations

import json
import unittest

from tests.support import PluginTestCase, hunk_session, pane_info


class PairTestCase(PluginTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.stub_checkout()

    def stub_origin(self, pane_id="w1:p1", **overrides) -> dict:
        pane = pane_info(pane_id, agent="claude", agent_status="idle")
        pane.update(overrides)
        self.herdr_result(["pane", "get", pane_id], {"type": "pane_info", "pane": pane})
        return pane

    def seed_pair(
        self,
        origin="w1:p1",
        review="w1:p9",
        session_id="s1",
        managed=True,
        target=None,
        origin_overrides=None,
        review_overrides=None,
    ) -> tuple[dict, dict, dict]:
        origin_pane = pane_info(origin, agent="claude", agent_status="idle")
        origin_pane.update(origin_overrides or {})
        review_pane = pane_info(review)
        review_pane.update(review_overrides or {})
        record = {
            "origin_pane": origin,
            "origin_terminal_id": origin_pane["terminal_id"],
            "review_pane": review,
            "review_terminal_id": review_pane["terminal_id"],
            "session_id": session_id,
            "target": target or ["diff"],
        }
        if isinstance(origin_pane.get("agent_session"), dict):
            record["origin_agent_session"] = origin_pane["agent_session"]
        if managed:
            record["plugin_pane"] = True
        self.write_pane_map({self.checkout: [record]})
        self.herdr_result(
            ["pane", "get", origin],
            {"type": "pane_info", "pane": origin_pane},
        )
        self.herdr_result(
            ["pane", "get", review],
            {"type": "pane_info", "pane": review_pane},
        )
        self.stub_live_session(session_id=session_id)
        return record, origin_pane, review_pane

    def records(self) -> list[dict]:
        return self.pane_map()[self.checkout]


class OpenReviewTest(PairTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.stub_origin()
        self.stub_split("w1:p9")

    def test_opens_a_managed_split_beside_the_agent(self) -> None:
        result = self.run_plugin("review-changes", self.context())
        self.assertSucceeded(result)
        opened = self.calls_matching("herdr", "plugin", "pane", "open")[0]
        self.assertEqual(
            opened,
            [
                "herdr",
                "plugin",
                "pane",
                "open",
                "--plugin",
                "herdr-hunk",
                "--entrypoint",
                "review",
                "--placement",
                "split",
                "--target-pane",
                "w1:p1",
                "--direction",
                "right",
                "--cwd",
                self.checkout,
                "--env",
                'HERDR_HUNK_TARGET_JSON=["diff"]',
                "--focus",
            ],
        )

    def test_persists_both_terminal_identities_session_and_target(self) -> None:
        self.assertSucceeded(self.run_plugin("review-changes", self.context()))
        record = self.records()[0]
        self.assertEqual(record["origin_pane"], "w1:p1")
        self.assertEqual(record["origin_terminal_id"], "term_w1_p1")
        self.assertEqual(record["review_pane"], "w1:p9")
        self.assertEqual(record["review_terminal_id"], "term_w1_p9")
        self.assertEqual(record["session_id"], "s1")
        self.assertEqual(record["target"], ["diff"])
        self.assertTrue(record["plugin_pane"])

    def test_persists_the_native_agent_session_identity_when_available(self) -> None:
        self.rules = []
        self.stub_checkout()
        self.stub_origin(agent_session={"id": "agent-session-1"})
        self.stub_split("w1:p9")
        self.assertSucceeded(self.run_plugin("review-changes", self.context()))
        self.assertEqual(
            self.records()[0]["origin_agent_session"], {"id": "agent-session-1"}
        )

    def test_does_not_override_hunks_rendering_preferences(self) -> None:
        self.assertSucceeded(self.run_plugin("review-changes", self.context()))
        opened = self.calls_matching("herdr", "plugin", "pane", "open")[0]
        for option in ("--mode", "--wrap", "--theme", "--transparent-bg"):
            self.assertNotIn(option, opened)

    def test_open_failure_is_surfaced_without_state(self) -> None:
        self.rules = []
        self.stub_checkout()
        self.stub_origin()
        self.rule("hunk", ["session", "list"], stdout=json.dumps({"sessions": []}))
        self.rule(
            "herdr",
            ["plugin", "pane", "open"],
            stderr='{"error":"pane_not_found"}\n',
            exit_code=1,
        )
        result = self.run_plugin("review-changes", self.context())
        self.assertFailed(result, "pane_not_found")
        self.assertEqual(self.pane_map(), {})

    def test_open_response_must_contain_the_managed_pane_id(self) -> None:
        self.rules = []
        self.stub_checkout()
        self.stub_origin()
        self.rule("hunk", ["session", "list"], stdout=json.dumps({"sessions": []}))
        self.herdr_result(["plugin", "pane", "open"], {"type": "plugin_pane_opened"})
        result = self.run_plugin("review-changes", self.context())
        self.assertFailed(result, "did not report a new pane")
        self.assertEqual(self.pane_map(), {})

    def test_shell_with_one_agent_opens_that_agents_pair(self) -> None:
        self.rules = []
        self.stub_checkout()
        self.stub_split("w1:p9")
        agent = pane_info("w1:p4", agent="claude", tab_id="w1:t1")
        self.herdr_result(
            ["pane", "list", "--workspace"],
            {"type": "pane_list", "panes": [agent]},
        )
        result = self.run_plugin(
            "review-changes", self.context(focused_pane_agent=None)
        )
        self.assertSucceeded(result)
        opened = self.calls_matching("herdr", "plugin", "pane", "open")[0]
        self.assertEqual(opened[opened.index("--target-pane") + 1], "w1:p4")
        self.assertEqual(self.records()[0]["origin_pane"], "w1:p4")

    def test_shell_with_no_agent_fails_instead_of_guessing(self) -> None:
        self.herdr_result(
            ["pane", "list", "--workspace"],
            {"type": "pane_list", "panes": []},
        )
        result = self.run_plugin(
            "review-changes", self.context(focused_pane_agent=None)
        )
        self.assertFailed(result, "no agent pane")
        self.assertEqual(self.calls_matching("herdr", "plugin", "pane", "open"), [])

    def test_shell_with_multiple_agents_fails_instead_of_using_recency(self) -> None:
        panes = [
            pane_info("w1:p2", agent="claude"),
            pane_info("w1:p7", agent="amp"),
        ]
        self.herdr_result(
            ["pane", "list", "--workspace"],
            {"type": "pane_list", "panes": panes},
        )
        result = self.run_plugin(
            "review-changes", self.context(focused_pane_agent=None)
        )
        self.assertFailed(result, "multiple agent panes", "w1:p2", "w1:p7")
        self.assertEqual(self.calls_matching("herdr", "agent", "list"), [])

    def test_existing_manual_session_does_not_hide_the_new_plugin_session(self) -> None:
        self.rules = []
        self.stub_checkout()
        self.stub_origin()
        self.herdr_result(
            ["plugin", "pane", "open"],
            {
                "type": "plugin_pane_opened",
                "plugin_pane": {"pane": pane_info("w1:p9")},
            },
        )
        manual = hunk_session(self.checkout, "manual", 200)
        plugin = hunk_session(self.checkout, "plugin", 201)
        self.rule(
            "hunk",
            ["session", "list"],
            stdout=json.dumps({"sessions": [manual, plugin]}),
            after=1,
        )
        self.rule(
            "hunk", ["session", "list"], stdout=json.dumps({"sessions": [manual]})
        )
        result = self.run_plugin("review-changes", self.context())
        self.assertSucceeded(result)
        self.assertEqual(self.records()[0]["session_id"], "plugin")
        self.assertFalse(any("--repo" in call for call in self.calls("hunk")))

    def test_managed_entrypoint_executes_hunk_with_watch(self) -> None:
        result = self.run_plugin(
            "run-hunk",
            extra_env={"HERDR_HUNK_TARGET_JSON": '["show","HEAD~1"]'},
        )
        self.assertSucceeded(result)
        self.assertIn(["hunk", "show", "HEAD~1", "--watch"], self.calls("hunk"))

    def test_managed_entrypoint_rejects_an_untrusted_target(self) -> None:
        result = self.run_plugin(
            "run-hunk",
            extra_env={"HERDR_HUNK_TARGET_JSON": '["daemon","serve"]'},
        )
        self.assertFailed(result, "unreadable managed Hunk target")
        self.assertEqual(self.calls("hunk"), [])


class ReuseReviewTest(PairTestCase):
    def test_reload_uses_the_pairs_exact_session_id(self) -> None:
        self.seed_pair(session_id="paired")
        result = self.run_plugin("review-commit", self.context())
        self.assertSucceeded(result)
        self.assertIn(
            [
                "hunk",
                "session",
                "reload",
                "paired",
                "--",
                "show",
                "--watch",
            ],
            self.calls("hunk"),
        )
        self.assertFalse(any("--repo" in call for call in self.calls("hunk")))

    def test_managed_pair_focuses_the_exact_pane(self) -> None:
        self.seed_pair()
        self.assertSucceeded(self.run_plugin("review-changes", self.context()))
        self.assertIn(
            ["herdr", "plugin", "pane", "focus", "w1:p9"], self.calls("herdr")
        )

    def test_managed_focus_failure_falls_back_to_the_pairs_tab(self) -> None:
        self.seed_pair(review_overrides={"tab_id": "w1:t7"})
        self.rule(
            "herdr",
            ["plugin", "pane", "focus"],
            stderr="plugin_pane_not_found\n",
            exit_code=1,
        )
        result = self.run_plugin("review-changes", self.context())
        self.assertSucceeded(result)
        self.assertIn(["herdr", "tab", "focus", "w1:t7"], self.calls("herdr"))
        self.assertIn("could not focus managed pane", result.stderr)

    def test_moved_managed_pane_is_focused_without_being_moved_back(self) -> None:
        self.seed_pair(
            review="w1:p9",
            review_overrides={
                "pane_id": "w2:p4",
                "workspace_id": "w2",
                "tab_id": "w2:t8",
            },
        )
        result = self.run_plugin("review-changes", self.context())
        self.assertSucceeded(result)
        self.assertIn(
            ["herdr", "plugin", "pane", "focus", "w2:p4"], self.calls("herdr")
        )
        self.assertEqual(self.calls_matching("herdr", "pane", "move"), [])
        self.assertIn("paired Hunk is in workspace w2", result.stderr)

    def test_legacy_pair_uses_tab_focus_fallback(self) -> None:
        origin = pane_info("w1:p1", agent="claude")
        review = pane_info("w1:p9", tab_id="w1:t7")
        self.write_pane_map(
            {
                self.checkout: {
                    "review_pane": "w1:p9",
                    "origin_pane": "w1:p1",
                    "session_id": "s1",
                }
            }
        )
        self.herdr_result(
            ["pane", "get", "w1:p1"], {"type": "pane_info", "pane": origin}
        )
        self.herdr_result(
            ["pane", "get", "w1:p9"], {"type": "pane_info", "pane": review}
        )
        self.stub_live_session()
        self.assertSucceeded(self.run_plugin("review-changes", self.context()))
        self.assertIn(["herdr", "tab", "focus", "w1:t7"], self.calls("herdr"))

    def test_originless_legacy_review_is_not_silently_taken_over(self) -> None:
        self.write_pane_map({self.checkout: "w1:pOLD"})
        self.rules = []
        self.stub_checkout()
        self.stub_origin()
        self.stub_split("w1:pNEW")
        result = self.run_plugin("review-changes", self.context())
        self.assertSucceeded(result)
        self.assertEqual(
            {record["review_pane"] for record in self.records()},
            {"w1:pOLD", "w1:pNEW"},
        )

    def test_two_agents_in_one_checkout_keep_two_pairs(self) -> None:
        first, _, _ = self.seed_pair(origin="w1:p1", review="w1:p9")
        self.rules = []
        self.stub_checkout()
        second_origin = self.stub_origin("w1:p2", agent="amp")
        self.herdr_result(
            ["plugin", "pane", "open"],
            {
                "type": "plugin_pane_opened",
                "plugin_pane": {"pane": pane_info("w1:p8")},
            },
        )
        first_session = hunk_session(self.checkout, "s1", 101)
        second_session = hunk_session(self.checkout, "s2", 102)
        self.rule(
            "hunk",
            ["session", "list"],
            stdout=json.dumps({"sessions": [first_session, second_session]}),
            after=1,
        )
        self.rule(
            "hunk",
            ["session", "list"],
            stdout=json.dumps({"sessions": [first_session]}),
        )
        result = self.run_plugin(
            "review-changes",
            self.context(focused_pane_id="w1:p2", focused_pane_agent="amp"),
        )
        self.assertSucceeded(result)
        records = self.records()
        self.assertEqual(len(records), 2)
        self.assertIn(first, records)
        pairs = {(item["origin_pane"], item["review_pane"]) for item in records}
        self.assertEqual(pairs, {("w1:p1", "w1:p9"), ("w1:p2", "w1:p8")})
        self.assertEqual(second_origin["pane_id"], "w1:p2")
        self.assertEqual(self.calls_matching("hunk", "reload"), [])

    def test_agent_replacement_discards_the_old_pair_and_opens_a_new_one(self) -> None:
        self.seed_pair(origin_overrides={"terminal_id": "old-terminal"})
        self.rules = []
        self.stub_checkout()
        self.stub_origin("w1:p1", terminal_id="new-terminal")
        self.stub_split("w1:p8")
        result = self.run_plugin("review-changes", self.context())
        self.assertSucceeded(result)
        self.assertEqual(len(self.records()), 1)
        self.assertEqual(self.records()[0]["review_pane"], "w1:p8")
        self.assertEqual(self.records()[0]["origin_terminal_id"], "new-terminal")

    def test_agent_session_replacement_in_the_same_terminal_opens_a_new_pair(
        self,
    ) -> None:
        self.seed_pair(origin_overrides={"agent_session": {"id": "old"}})
        self.rules = []
        self.stub_checkout()
        self.stub_origin("w1:p1", agent_session={"id": "new"})
        self.stub_split("w1:p8")
        result = self.run_plugin("review-changes", self.context())
        self.assertSucceeded(result)
        self.assertEqual(len(self.records()), 1)
        self.assertEqual(self.records()[0]["review_pane"], "w1:p8")
        self.assertEqual(self.records()[0]["origin_agent_session"], {"id": "new"})

    def test_invoking_from_the_hunk_reuses_its_known_pair(self) -> None:
        self.seed_pair()
        context = self.context(
            focused_pane_id="w1:p9", focused_pane_agent=None, focused_pane_cwd=None
        )
        result = self.run_plugin("review-commit", context)
        self.assertSucceeded(result)
        self.assertEqual(self.calls_matching("herdr", "plugin", "pane", "open"), [])
        self.assertIn(
            ["hunk", "session", "reload", "s1", "--", "show", "--watch"],
            self.calls("hunk"),
        )

    def test_target_switch_records_existing_notes_against_the_old_target(self) -> None:
        self.seed_pair(target=["diff", "main"])
        notes = {
            "comments": [
                {"noteId": "n1", "filePath": "a.py", "newRange": [3, 3], "body": "fix"}
            ]
        }
        self.rule("hunk", ["comment", "list", "s1"], stdout=json.dumps(notes))
        self.rules.insert(0, self.rules.pop())
        self.assertSucceeded(self.run_plugin("review-commit", self.context()))
        record = self.records()[0]
        self.assertEqual(record["note_targets"]["n1"], ["diff", "main"])
        self.assertEqual(record["target"], ["show"])

    def test_multiple_repo_sessions_do_not_change_the_stored_selector(self) -> None:
        self.seed_pair(session_id="s1")
        second = hunk_session(self.checkout, "s2", 202)
        self.rule(
            "hunk",
            ["session", "list"],
            stdout=json.dumps({"sessions": [hunk_session(self.checkout), second]}),
        )
        self.assertSucceeded(self.run_plugin("review-changes", self.context()))
        reloads = self.calls_matching("hunk", "reload")
        self.assertEqual(reloads[0][3], "s1")

    def test_missing_stored_session_can_be_recovered_by_pane_pid(self) -> None:
        self.seed_pair(session_id="gone")
        self.rules = [
            rule
            for rule in self.rules
            if not (
                rule["program"] == "hunk"
                and rule["match"]
                in (
                    ["session", "get", "gone", "--json"],
                    ["session", "list", "--json"],
                )
            )
        ]
        self.rule("hunk", ["session", "get", "gone"], stderr="gone\n", exit_code=1)
        sessions = [
            hunk_session(self.checkout, "other", 400),
            hunk_session(self.checkout, "paired", 101),
        ]
        self.rule(
            "hunk", ["session", "list"], stdout=json.dumps({"sessions": sessions})
        )
        self.stub_process_info("w1:p9", foreground="hunk")
        self.assertSucceeded(self.run_plugin("review-changes", self.context()))
        self.assertEqual(self.calls_matching("hunk", "reload")[0][3], "paired")
        self.assertEqual(self.records()[0]["session_id"], "paired")

    def test_managed_pair_does_not_adopt_a_manual_session_during_recovery(
        self,
    ) -> None:
        self.seed_pair(session_id="gone")
        self.rules = [
            rule
            for rule in self.rules
            if not (
                rule["program"] == "hunk"
                and rule["match"]
                in (
                    ["session", "get", "gone", "--json"],
                    ["session", "list", "--json"],
                )
            )
        ]
        self.rule("hunk", ["session", "get", "gone"], stderr="gone\n", exit_code=1)
        manual = hunk_session(self.checkout, "manual", 400)
        self.rule(
            "hunk",
            ["session", "list"],
            stdout=json.dumps({"sessions": [manual]}),
        )
        self.stub_process_info("w1:p9", foreground="hunk")
        result = self.run_plugin("review-changes", self.context())
        self.assertFailed(result, "session did not register")
        self.assertEqual(self.calls_matching("hunk", "reload"), [])

    def test_dead_hunk_pane_is_rebuilt_for_the_same_agent(self) -> None:
        self.seed_pair()
        self.rules = []
        self.stub_checkout()
        self.stub_origin()
        self.stub_live_pane("w1:p9", alive=False)
        self.stub_split("w1:p8")
        self.assertSucceeded(self.run_plugin("review-changes", self.context()))
        self.assertEqual(self.records()[0]["review_pane"], "w1:p8")

    def test_repeated_action_reloads_without_opening_another_pane(self) -> None:
        self.seed_pair()
        for _ in range(2):
            self.assertSucceeded(self.run_plugin("review-changes", self.context()))
        self.assertEqual(self.calls_matching("herdr", "plugin", "pane", "open"), [])
        self.assertEqual(len(self.calls_matching("hunk", "reload")), 2)


if __name__ == "__main__":
    unittest.main()
