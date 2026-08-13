"""Sending Hunk review notes back to the agent that wrote the code."""

from __future__ import annotations

import json
import os
import unittest

from tests.support import PluginTestCase, envelope, pane_info

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
            "editable": True,
        },
        {
            "noteId": "n2",
            "source": "user",
            "filePath": "README.md",
            "oldRange": [7, 9],
            "body": "Stale instructions.",
            "createdAt": "2026-08-13T00:01:00Z",
            "editable": True,
        },
    ]
}


class SendCommentsTest(PluginTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.restub()

    def restub(self, panes=None, checkout=None, **note_reply) -> None:
        """Reset the rule table: checkout, note listing, and pane listing."""
        self.rules = []
        self.stub_checkout(toplevel=checkout)
        self.rule(
            "hunk", ["comment", "list"], **(note_reply or {"stdout": json.dumps(NOTES)})
        )
        self.stub_panes(panes)

    def stub_panes(self, panes=None) -> None:
        if panes is None:
            panes = [
                pane_info("w1:p1"),
                pane_info("w1:p9"),
                pane_info("w1:p2", agent="claude", agent_status="idle"),
            ]
        self.rule(
            "herdr",
            ["pane", "list", "--workspace"],
            stdout=envelope({"type": "pane_list", "panes": panes}),
        )

    def notes_file(self) -> str:
        send = self.calls_matching("herdr", "send-text")[0]
        text = send[-1]
        for token in text.split():
            if token.endswith(".md"):
                return token
        raise AssertionError(f"no notes path in staged text: {text!r}")

    def test_notes_are_written_as_markdown(self) -> None:
        result = self.run_plugin("send-comments", self.context())
        self.assertSucceeded(result)
        with open(self.notes_file(), encoding="utf-8") as handle:
            body = handle.read()
        self.assertIn("# Hunk review notes", body)
        self.assertIn("## src/review.py:42", body)
        self.assertIn("Dead branch", body)
        self.assertIn("This branch never runs.", body)
        self.assertIn("## README.md:7", body)
        self.assertIn("Stale instructions.", body)

    def test_the_notes_file_lives_under_the_plugin_state_dir(self) -> None:
        self.run_plugin("send-comments", self.context())
        self.assertTrue(self.notes_file().startswith(self.state_dir))

    def test_only_user_notes_are_requested(self) -> None:
        self.run_plugin("send-comments", self.context())
        listing = self.calls_matching("hunk", "comment", "list")[0]
        self.assertEqual(
            listing,
            [
                "hunk",
                "session",
                "comment",
                "list",
                "--repo",
                self.checkout,
                "--type",
                "user",
                "--json",
            ],
        )

    def test_the_staged_text_is_exactly_one_line(self) -> None:
        self.run_plugin("send-comments", self.context())
        text = self.calls_matching("herdr", "send-text")[0][-1]
        self.assertNotIn("\n", text)
        self.assertNotIn("\r", text)

    def test_the_text_is_staged_and_not_submitted(self) -> None:
        self.run_plugin("send-comments", self.context())
        self.assertEqual(len(self.calls_matching("herdr", "send-text")), 1)
        self.assertEqual(self.calls_matching("herdr", "run"), [])
        self.assertEqual(self.calls_matching("herdr", "send-keys"), [])

    def test_the_agent_pane_receives_the_text(self) -> None:
        self.run_plugin("send-comments", self.context())
        send = self.calls_matching("herdr", "send-text")[0]
        self.assertEqual(send[:4], ["herdr", "pane", "send-text", "w1:p2"])

    def test_the_review_pane_is_excluded_from_agent_discovery(self) -> None:
        """A Hunk pane that Herdr reports as an agent must never receive the notes."""
        self.restub(
            panes=[
                pane_info("w1:p9", agent="hunk", agent_status="idle"),
                pane_info("w1:p2", agent="claude", agent_status="idle"),
            ]
        )
        self.seed_pane_map(self.checkout, "w1:p9")
        result = self.run_plugin("send-comments", self.context())
        self.assertSucceeded(result)
        self.assertEqual(self.calls_matching("herdr", "send-text")[0][3], "w1:p2")

    def test_a_review_pane_recorded_for_another_checkout_is_also_excluded(self) -> None:
        """Every pane this plugin opened is Hunk, whichever checkout it was keyed on."""
        self.restub(
            panes=[
                pane_info("w1:p9", agent="hunk", agent_status="idle"),
                pane_info("w1:p2", agent="claude", agent_status="idle"),
            ]
        )
        self.seed_pane_map("/some/other/checkout", "w1:p9")
        result = self.run_plugin("send-comments", self.context())
        self.assertSucceeded(result)
        self.assertEqual(self.calls_matching("herdr", "send-text")[0][3], "w1:p2")

    def test_the_agent_pane_is_focused_after_the_text_is_staged(self) -> None:
        self.run_plugin("send-comments", self.context())
        self.assertEqual(
            self.calls_matching("herdr", "agent", "focus"),
            [["herdr", "agent", "focus", "w1:p2"]],
        )

    def test_staged_notes_are_not_reported_as_failed_when_focus_fails(self) -> None:
        self.rule("herdr", ["agent", "focus"], stderr="agent_not_found\n", exit_code=1)
        result = self.run_plugin("send-comments", self.context())
        self.assertSucceeded(result)
        self.assertEqual(len(self.calls_matching("herdr", "send-text")), 1)
        self.assertIn("could not focus", result.stderr)

    def test_the_pane_the_review_was_opened_beside_wins(self) -> None:
        """Two agents in one workspace: the review's origin pane wrote this code."""
        self.restub(
            panes=[
                pane_info("w1:p2", agent="claude", agent_status="idle"),
                pane_info("w1:p7", agent="pi", agent_status="idle"),
            ]
        )
        self.seed_pane_map(self.checkout, "w1:p9", origin="w1:p7")
        result = self.run_plugin("send-comments", self.context())
        self.assertSucceeded(result)
        self.assertEqual(self.calls_matching("herdr", "send-text")[0][3], "w1:p7")

    def test_the_invoking_agent_pane_wins_when_no_origin_was_recorded(self) -> None:
        self.restub(
            panes=[
                pane_info("w1:p2", agent="claude", agent_status="idle"),
                pane_info("w1:p7", agent="pi", agent_status="idle"),
            ]
        )
        result = self.run_plugin("send-comments", self.context(focused_pane_id="w1:p7"))
        self.assertSucceeded(result)
        self.assertEqual(self.calls_matching("herdr", "send-text")[0][3], "w1:p7")

    def test_two_agent_panes_with_no_hint_fails_rather_than_guessing(self) -> None:
        self.restub(
            panes=[
                pane_info("w1:p2", agent="claude", agent_status="idle"),
                pane_info("w1:p7", agent="pi", agent_status="idle"),
            ]
        )
        result = self.run_plugin("send-comments", self.context())
        self.assertFailed(result, "2 agent panes", "w1:p2", "w1:p7")
        self.assertEqual(self.calls_matching("herdr", "send-text"), [])
        self.assertEqual(self.calls_matching("herdr", "agent", "focus"), [])

    def test_a_stale_origin_pane_falls_back_to_the_only_agent(self) -> None:
        self.seed_pane_map(self.checkout, "w1:p9", origin="w1:pGONE")
        result = self.run_plugin("send-comments", self.context())
        self.assertSucceeded(result)
        self.assertEqual(self.calls_matching("herdr", "send-text")[0][3], "w1:p2")

    def test_an_origin_pane_that_is_now_hunk_is_never_targeted(self) -> None:
        self.restub(
            panes=[
                pane_info("w1:p9", agent="hunk", agent_status="idle"),
                pane_info("w1:p2", agent="claude", agent_status="idle"),
            ]
        )
        self.seed_pane_map(self.checkout, "w1:p9", origin="w1:p9")
        result = self.run_plugin("send-comments", self.context())
        self.assertSucceeded(result)
        self.assertEqual(self.calls_matching("herdr", "send-text")[0][3], "w1:p2")

    def test_a_workspace_with_no_agent_pane_fails_without_sending(self) -> None:
        self.restub(panes=[pane_info("w1:p1"), pane_info("w1:p9")])
        result = self.run_plugin("send-comments", self.context())
        self.assertFailed(result, "no agent pane")
        self.assertEqual(self.calls_matching("herdr", "send-text"), [])

    def test_no_live_review_fails_without_sending(self) -> None:
        self.restub(stderr="No live Hunk session for this repo.\n", exit_code=1)
        result = self.run_plugin("send-comments", self.context())
        self.assertFailed(result, "no live Hunk review")
        self.assertEqual(self.calls_matching("herdr", "send-text"), [])

    def test_an_empty_note_list_fails_without_sending(self) -> None:
        self.restub(stdout=json.dumps({"comments": []}))
        result = self.run_plugin("send-comments", self.context())
        self.assertFailed(result, "no review notes")
        self.assertEqual(self.calls_matching("herdr", "send-text"), [])

    def test_unparseable_note_json_fails_without_sending(self) -> None:
        self.restub(stdout="not json")
        result = self.run_plugin("send-comments", self.context())
        self.assertFailed(result)
        self.assertEqual(self.calls_matching("herdr", "send-text"), [])

    def test_a_note_with_an_unexpected_range_still_names_its_file(self) -> None:
        self.restub(
            stdout=json.dumps(
                {
                    "comments": [
                        {
                            "noteId": "n1",
                            "source": "user",
                            "filePath": "src/odd.py",
                            "newRange": {"start": 3},
                            "body": "Still worth reading.",
                            "createdAt": "2026-08-13T00:00:00Z",
                            "editable": True,
                        }
                    ]
                }
            )
        )
        result = self.run_plugin("send-comments", self.context())
        self.assertSucceeded(result)
        with open(self.notes_file(), encoding="utf-8") as handle:
            body = handle.read()
        self.assertIn("## src/odd.py", body)
        self.assertIn("Still worth reading.", body)

    def test_a_missing_workspace_in_context_fails_without_sending(self) -> None:
        result = self.run_plugin(
            "send-comments",
            self.context(workspace_id=None),
            extra_env={"HERDR_WORKSPACE_ID": "w1"},
        )
        self.assertFailed(result, "no workspace in the invocation context")
        self.assertEqual(self.calls_matching("herdr", "send-text"), [])

    def test_notes_for_different_checkouts_use_different_files(self) -> None:
        self.run_plugin("send-comments", self.context())
        first = self.notes_file()

        other = os.path.join(self.tmp, "other")
        os.makedirs(other)
        self.restub(checkout=other)
        self.run_plugin("send-comments", self.context(focused_pane_cwd=other))
        second = self.calls_matching("herdr", "send-text")[-1][-1]
        self.assertNotIn(os.path.basename(first), second)


if __name__ == "__main__":
    unittest.main()
