"""Failure surfacing: an action is never reported successful when nothing happened."""

from __future__ import annotations

import json
import unittest

from tests.support import PluginTestCase, hunk_session, pane_info


class MissingHunkTest(PluginTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.programs.discard("hunk")
        self.stub_checkout()
        self.stub_split("w1:p9")

    def test_review_reports_how_to_install_hunk(self) -> None:
        result = self.run_plugin("review-changes", self.context())
        self.assertFailed(result, "hunk", "npm install -g hunkdiff")

    def test_review_does_not_mutate_the_layout(self) -> None:
        self.run_plugin("review-changes", self.context())
        self.assertEqual(self.calls_matching("herdr", "split"), [])

    def test_send_comments_reports_how_to_install_hunk(self) -> None:
        result = self.run_plugin("send-comments", self.context())
        self.assertFailed(result, "npm install -g hunkdiff")
        self.assertEqual(self.calls_matching("herdr", "send-text"), [])


class HerdrFailureTest(PluginTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.stub_checkout()
        self.rule("hunk", ["session", "list"], stdout=json.dumps({"sessions": []}))

    def test_a_failed_split_is_surfaced(self) -> None:
        self.rule(
            "herdr",
            ["plugin", "pane", "open"],
            stderr='{"error":"pane_not_found"}\n',
            exit_code=1,
        )
        result = self.run_plugin("review-changes", self.context())
        self.assertFailed(result, "pane_not_found")
        self.assertEqual(self.pane_map(), {})

    def test_a_failed_managed_pane_launch_is_surfaced_without_state(self) -> None:
        self.rule(
            "herdr",
            ["plugin", "pane", "open"],
            stderr='{"error":"pane_busy"}\n',
            exit_code=1,
        )
        result = self.run_plugin("review-changes", self.context())
        self.assertFailed(result, "pane_busy")
        self.assertEqual(self.pane_map(), {})

    def test_a_failed_reload_is_surfaced_and_leaves_the_pane_recorded(self) -> None:
        origin = pane_info("w1:p1", agent="claude")
        self.write_pane_map(
            {
                self.checkout: [
                    {
                        "origin_pane": "w1:p1",
                        "origin_terminal_id": origin["terminal_id"],
                        "review_pane": "w1:p9",
                        "session_id": "s1",
                        "target": ["diff"],
                    }
                ]
            }
        )
        self.herdr_result(
            ["pane", "get", "w1:p1"], {"type": "pane_info", "pane": origin}
        )
        self.stub_live_pane("w1:p9")
        self.stub_live_session(True)
        self.rule("hunk", ["session", "reload"], stderr="reload failed\n", exit_code=1)
        result = self.run_plugin("review-changes", self.context())
        self.assertFailed(result, "reload failed")
        self.assertEqual(self.calls_matching("herdr", "split"), [])
        self.assertEqual(self.review_panes(), {self.checkout: "w1:p9"})

    def test_a_failed_pane_list_is_surfaced(self) -> None:
        session = hunk_session(self.checkout)
        self.rules.insert(
            0,
            {
                "program": "hunk",
                "match": ["session", "comment", "list"],
                "stdout": json.dumps(
                    {
                        "comments": [
                            {
                                "noteId": "n1",
                                "source": "user",
                                "filePath": "a.py",
                                "body": "b",
                            }
                        ]
                    }
                ),
                "stderr": "",
                "exit": 0,
                "after": 0,
            },
        )
        self.rule(
            "hunk",
            ["session", "list"],
            stdout=json.dumps({"sessions": [session]}),
        )
        self.rule(
            "herdr",
            ["pane", "list"],
            stderr='{"error":"workspace_not_found"}\n',
            exit_code=1,
        )
        result = self.run_plugin("send-comments", self.context(focused_pane_agent=None))
        self.assertFailed(result, "workspace_not_found")
        self.assertEqual(self.calls_matching("herdr", "send-text"), [])

    def test_a_failed_send_text_is_surfaced(self) -> None:
        origin = pane_info("w1:p2", agent="claude")
        review = pane_info("w1:p9")
        self.write_pane_map(
            {
                self.checkout: [
                    {
                        "origin_pane": "w1:p2",
                        "origin_terminal_id": origin["terminal_id"],
                        "review_pane": "w1:p9",
                        "review_terminal_id": review["terminal_id"],
                        "plugin_pane": True,
                        "session_id": "s1",
                        "target": ["diff"],
                    }
                ]
            }
        )
        self.herdr_result(
            ["pane", "get", "w1:p9"], {"type": "pane_info", "pane": review}
        )
        self.herdr_result(
            ["pane", "get", "w1:p2"], {"type": "pane_info", "pane": origin}
        )
        self.stub_live_session()
        self.rules.insert(
            0,
            {
                "program": "hunk",
                "match": ["session", "comment", "list", "s1"],
                "stdout": json.dumps(
                    {
                        "comments": [
                            {
                                "noteId": "n1",
                                "source": "user",
                                "filePath": "a.py",
                                "body": "b",
                            }
                        ]
                    }
                ),
                "stderr": "",
                "exit": 0,
                "after": 0,
            },
        )
        self.rule(
            "herdr", ["send-text"], stderr='{"error":"pane_not_found"}\n', exit_code=1
        )
        result = self.run_plugin(
            "send-comments",
            self.context(focused_pane_id="w1:p9", focused_pane_agent=None),
        )
        self.assertFailed(result, "pane_not_found")

    def test_unparseable_herdr_output_is_surfaced(self) -> None:
        self.rule("herdr", ["plugin", "pane", "open"], stdout="not json\n")
        result = self.run_plugin("review-changes", self.context())
        self.assertFailed(result)
        self.assertEqual(self.calls_matching("herdr", "run"), [])


class StateDirTest(PluginTestCase):
    def test_a_missing_state_dir_fails_without_acting(self) -> None:
        self.stub_checkout()
        self.stub_split("w1:p9")
        result = self.run_plugin(
            "review-changes", self.context(), extra_env={"HERDR_PLUGIN_STATE_DIR": None}
        )
        self.assertFailed(result, "HERDR_PLUGIN_STATE_DIR")
        self.assertEqual(self.calls_matching("herdr", "split"), [])

    def test_a_corrupt_pane_map_is_treated_as_empty(self) -> None:
        self.stub_checkout()
        self.stub_split("w1:p9")
        with open(
            f"{self.state_dir}/review-panes.json", "w", encoding="utf-8"
        ) as handle:
            handle.write("{oops")
        result = self.run_plugin("review-changes", self.context())
        self.assertSucceeded(result)
        self.assertEqual(self.review_panes(), {self.checkout: "w1:p9"})


if __name__ == "__main__":
    unittest.main()
