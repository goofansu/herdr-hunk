"""Failure surfacing: an action is never reported successful when nothing happened."""

from __future__ import annotations

import json
import unittest

from tests.support import PluginTestCase, envelope, pane_info


class MissingHunkTest(PluginTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.programs.discard("hunk")
        self.stub_checkout()
        self.stub_split("w1:p9")

    def test_review_reports_how_to_install_hunk(self) -> None:
        result = self.run_plugin("review", self.context())
        self.assertFailed(result, "hunk", "npm install -g hunkdiff")

    def test_review_does_not_mutate_the_layout(self) -> None:
        self.run_plugin("review", self.context())
        self.assertEqual(self.calls_matching("herdr", "split"), [])

    def test_send_comments_reports_how_to_install_hunk(self) -> None:
        result = self.run_plugin("send-comments", self.context())
        self.assertFailed(result, "npm install -g hunkdiff")
        self.assertEqual(self.calls_matching("herdr", "send-text"), [])


class HerdrFailureTest(PluginTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.stub_checkout()

    def test_a_failed_split_is_surfaced(self) -> None:
        self.rule(
            "herdr",
            ["pane", "split"],
            stderr='{"error":"pane_not_found"}\n',
            exit_code=1,
        )
        result = self.run_plugin("review", self.context())
        self.assertFailed(result, "pane_not_found")
        self.assertEqual(self.calls_matching("herdr", "run"), [])
        self.assertEqual(self.pane_map(), {})

    def test_a_failed_hunk_launch_closes_the_pane_it_opened(self) -> None:
        self.stub_split("w1:p9")
        self.rule(
            "herdr", ["pane", "run"], stderr='{"error":"pane_busy"}\n', exit_code=1
        )
        result = self.run_plugin("review", self.context())
        self.assertFailed(result, "pane_busy")
        self.assertEqual(
            self.calls_matching("herdr", "close"), [["herdr", "pane", "close", "w1:p9"]]
        )
        self.assertEqual(self.pane_map(), {})

    def test_a_failed_reload_is_surfaced_and_leaves_the_pane_recorded(self) -> None:
        self.seed_pane_map(self.checkout, "w1:p9")
        self.stub_live_pane("w1:p9")
        self.stub_live_session(True)
        self.rule("hunk", ["session", "reload"], stderr="reload failed\n", exit_code=1)
        result = self.run_plugin("review", self.context())
        self.assertFailed(result, "reload failed")
        self.assertEqual(self.calls_matching("herdr", "split"), [])
        self.assertEqual(self.review_panes(), {self.checkout: "w1:p9"})

    def test_a_failed_pane_list_is_surfaced(self) -> None:
        self.rule(
            "hunk",
            ["comment", "list"],
            stdout=json.dumps(
                {
                    "comments": [
                        {
                            "noteId": "n1",
                            "source": "user",
                            "filePath": "a.py",
                            "body": "b",
                            "createdAt": "now",
                            "editable": True,
                        }
                    ]
                }
            ),
        )
        self.rule(
            "herdr",
            ["pane", "list"],
            stderr='{"error":"workspace_not_found"}\n',
            exit_code=1,
        )
        result = self.run_plugin("send-comments", self.context())
        self.assertFailed(result, "workspace_not_found")
        self.assertEqual(self.calls_matching("herdr", "send-text"), [])

    def test_a_failed_send_text_is_surfaced(self) -> None:
        self.rule(
            "hunk",
            ["comment", "list"],
            stdout=json.dumps(
                {
                    "comments": [
                        {
                            "noteId": "n1",
                            "source": "user",
                            "filePath": "a.py",
                            "body": "b",
                            "createdAt": "now",
                            "editable": True,
                        }
                    ]
                }
            ),
        )
        self.rule(
            "herdr",
            ["pane", "list", "--workspace"],
            stdout=envelope(
                {"type": "pane_list", "panes": [pane_info("w1:p2", agent="claude")]}
            ),
        )
        self.rule(
            "herdr", ["send-text"], stderr='{"error":"pane_not_found"}\n', exit_code=1
        )
        result = self.run_plugin("send-comments", self.context())
        self.assertFailed(result, "pane_not_found")

    def test_unparseable_herdr_output_is_surfaced(self) -> None:
        self.rule("herdr", ["pane", "split"], stdout="not json\n")
        result = self.run_plugin("review", self.context())
        self.assertFailed(result)
        self.assertEqual(self.calls_matching("herdr", "run"), [])


class StateDirTest(PluginTestCase):
    def test_a_missing_state_dir_fails_without_acting(self) -> None:
        self.stub_checkout()
        self.stub_split("w1:p9")
        result = self.run_plugin(
            "review", self.context(), extra_env={"HERDR_PLUGIN_STATE_DIR": None}
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
        result = self.run_plugin("review", self.context())
        self.assertSucceeded(result)
        self.assertEqual(self.review_panes(), {self.checkout: "w1:p9"})


if __name__ == "__main__":
    unittest.main()
