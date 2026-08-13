"""Review pane lifecycle: open once, then reuse."""

from __future__ import annotations

import unittest

from tests.support import PluginTestCase


class OpenReviewTest(PluginTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.stub_checkout()
        self.stub_split("w1:p9")

    def test_the_review_pane_is_split_to_the_right_and_focused(self) -> None:
        result = self.run_plugin("review-changes", self.context())
        self.assertSucceeded(result)
        split = self.calls_matching("herdr", "split")[0]
        self.assertEqual(
            split,
            [
                "herdr",
                "pane",
                "split",
                "w1:p1",
                "--direction",
                "right",
                "--cwd",
                self.checkout,
                "--focus",
            ],
        )

    def test_hunk_launches_with_watch_and_no_rendering_overrides(self) -> None:
        result = self.run_plugin("review-changes", self.context())
        self.assertSucceeded(result)
        run = self.calls_matching("herdr", "run")[0]
        self.assertEqual(run[run.index("hunk") :], ["hunk", "diff", "--watch"])
        self.assertNotIn("--no-transparent-bg", run)
        self.assertNotIn("--theme", run)
        self.assertNotIn("--mode", run)

    def test_the_new_pane_id_is_recorded_against_the_checkout(self) -> None:
        self.run_plugin("review-changes", self.context())
        self.assertEqual(self.review_panes(), {self.checkout: "w1:p9"})

    def test_the_agent_pane_that_asked_for_the_review_is_recorded(self) -> None:
        """send-comments needs it to know which agent produced the code."""
        self.run_plugin(
            "review-changes",
            self.context(focused_pane_id="w1:p4", focused_pane_agent="claude"),
        )
        self.assertEqual(
            self.pane_map(),
            {self.checkout: {"review_pane": "w1:p9", "origin_pane": "w1:p4"}},
        )

    def test_a_review_opened_from_a_shell_records_no_origin(self) -> None:
        """A worktree's root pane is an ordinary shell, not the code's author."""
        self.run_plugin("review-changes", self.context(focused_pane_id="w1:p1"))
        self.assertEqual(self.pane_map(), {self.checkout: {"review_pane": "w1:p9"}})

    def test_a_legacy_single_pane_state_file_is_still_reused(self) -> None:
        self.write_pane_map({self.checkout: "w1:p9"})
        self.stub_live_pane("w1:p9")
        self.stub_live_session(True)
        result = self.run_plugin("review-changes", self.context())
        self.assertSucceeded(result)
        self.assertEqual(len(self.calls_matching("hunk", "reload")), 1)
        self.assertEqual(self.calls_matching("herdr", "split"), [])

    def test_a_split_that_reports_no_pane_id_fails_without_running_hunk(self) -> None:
        self.rules = []
        self.stub_checkout()
        self.herdr_result(["pane", "split"], {"type": "pane_info"})
        result = self.run_plugin("review-changes", self.context())
        self.assertFailed(result, "did not report a new pane")
        self.assertEqual(self.calls_matching("herdr", "run"), [])
        self.assertEqual(self.pane_map(), {})


class ReuseReviewTest(PluginTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.stub_checkout()
        self.stub_split("w1:p9")

    def test_a_live_pane_and_live_session_reload_instead_of_splitting(self) -> None:
        self.seed_pane_map(self.checkout, "w1:p9")
        self.stub_live_pane("w1:p9")
        self.stub_live_session(True)
        result = self.run_plugin("review-changes", self.context())
        self.assertSucceeded(result)
        self.assertEqual(len(self.calls_matching("hunk", "reload")), 1)
        self.assertEqual(self.calls_matching("herdr", "split"), [])

    def test_a_reused_pane_is_brought_into_view(self) -> None:
        """The review pane may have been moved, so its own workspace is authoritative."""
        self.seed_pane_map(self.checkout, "w2:p9")
        self.stub_live_pane("w2:p9")
        self.stub_live_session(True)
        self.run_plugin("review-changes", self.context())
        self.assertEqual(
            self.calls_matching("herdr", "workspace", "focus"),
            [["herdr", "workspace", "focus", "w2"]],
        )

    def test_a_pane_without_a_workspace_falls_back_to_the_context_workspace(
        self,
    ) -> None:
        self.seed_pane_map(self.checkout, "w1:p9")
        self.herdr_result(
            ["pane", "get", "w1:p9"],
            {"type": "pane_info", "pane": {"pane_id": "w1:p9", "revision": 1}},
        )
        self.stub_live_session(True)
        result = self.run_plugin("review-changes", self.context())
        self.assertSucceeded(result)
        self.assertEqual(
            self.calls_matching("herdr", "workspace", "focus"),
            [["herdr", "workspace", "focus", "w1"]],
        )

    def test_a_successful_reload_is_never_reported_as_a_failure(self) -> None:
        """No workspace to focus must not turn a completed reload into an error."""
        self.seed_pane_map(self.checkout, "w1:p9")
        self.herdr_result(
            ["pane", "get", "w1:p9"],
            {"type": "pane_info", "pane": {"pane_id": "w1:p9", "revision": 1}},
        )
        self.stub_live_session(True)
        result = self.run_plugin("review-changes", self.context(workspace_id=None))
        self.assertSucceeded(result)
        self.assertEqual(len(self.calls_matching("hunk", "reload")), 1)
        for call in self.calls_matching("herdr", "workspace", "focus"):
            self.assertNotIn("", call)

    def test_reusing_a_review_repoints_it_at_the_agent_that_asked(self) -> None:
        """Two agents share a review pane; the notes must follow the latest caller."""
        self.seed_pane_map(self.checkout, "w1:p9", origin="w1:pTOP")
        self.stub_live_pane("w1:p9")
        self.stub_live_session(True)
        result = self.run_plugin(
            "review-commit",
            self.context(focused_pane_id="w1:pBOTTOM", focused_pane_agent="claude"),
        )
        self.assertSucceeded(result)
        self.assertEqual(self.calls_matching("herdr", "split"), [])
        self.assertEqual(self.pane_map()[self.checkout]["origin_pane"], "w1:pBOTTOM")

    def test_relaunching_a_review_repoints_it_too(self) -> None:
        self.seed_pane_map(self.checkout, "w1:p9", origin="w1:pTOP")
        self.stub_live_pane("w1:p9")
        self.stub_live_session(False)
        self.stub_process_info("w1:p9")
        result = self.run_plugin(
            "review-changes",
            self.context(focused_pane_id="w1:pBOTTOM", focused_pane_agent="claude"),
        )
        self.assertSucceeded(result)
        self.assertEqual(self.pane_map()[self.checkout]["origin_pane"], "w1:pBOTTOM")

    def test_reusing_from_the_review_pane_keeps_the_known_origin(self) -> None:
        """Pressing the key while reading the diff must not forget the author."""
        self.seed_pane_map(self.checkout, "w1:p9", origin="w1:pTOP")
        self.stub_live_pane("w1:p9")
        self.stub_live_session(True)
        result = self.run_plugin(
            "review-changes", self.context(focused_pane_id="w1:p9")
        )
        self.assertSucceeded(result)
        self.assertEqual(self.pane_map()[self.checkout]["origin_pane"], "w1:pTOP")

    def test_a_dead_pane_opens_a_new_review(self) -> None:
        self.seed_pane_map(self.checkout, "w1:p9")
        self.stub_live_pane("w1:p9", alive=False)
        self.stub_live_session(True)
        result = self.run_plugin("review-changes", self.context())
        self.assertSucceeded(result)
        self.assertEqual(len(self.calls_matching("herdr", "split")), 1)
        self.assertEqual(self.calls_matching("hunk", "reload"), [])

    def test_a_pane_left_at_a_shell_relaunches_hunk_in_place(self) -> None:
        """Quitting Hunk must not strand the pane and split another beside it."""
        self.seed_pane_map(self.checkout, "w1:p9")
        self.stub_live_pane("w1:p9")
        self.stub_live_session(False)
        self.stub_process_info("w1:p9")
        result = self.run_plugin("review-changes", self.context())
        self.assertSucceeded(result)
        self.assertEqual(self.calls_matching("herdr", "split"), [])
        run = self.calls_matching("herdr", "run")[0]
        self.assertEqual(run[:4], ["herdr", "pane", "run", "w1:p9"])
        self.assertEqual(run[run.index("hunk") :], ["hunk", "diff", "--watch"])
        self.assertEqual(self.review_panes(), {self.checkout: "w1:p9"})

    def test_a_hunk_still_registering_is_waited_for_not_duplicated(self) -> None:
        """A pane opened moments ago has a Hunk the daemon has yet to hear about."""
        self.seed_pane_map(self.checkout, "w1:p9")
        self.stub_live_pane("w1:p9")
        # The session only registers on the third probe.
        self.rule("hunk", ["session", "get"], stdout="Session: s1\n", after=2)
        self.rule("hunk", ["session", "get"], stderr="not registered\n", exit_code=1)
        self.stub_process_info("w1:p9", foreground="hunk")
        result = self.run_plugin("review-changes", self.context())
        self.assertSucceeded(result)
        self.assertEqual(self.calls_matching("herdr", "split"), [])
        self.assertEqual(len(self.calls_matching("hunk", "reload")), 1)
        self.assertGreater(len(self.calls_matching("hunk", "session", "get")), 1)

    def test_a_hunk_whose_daemon_never_answers_does_not_stack_a_second(self) -> None:
        self.seed_pane_map(self.checkout, "w1:p9")
        self.stub_live_pane("w1:p9")
        self.stub_live_session(False)
        self.stub_process_info("w1:p9", foreground="hunk")
        result = self.run_plugin("review-changes", self.context())
        self.assertFailed(result, "daemon may be unreachable")
        self.assertEqual(self.calls_matching("herdr", "split"), [])
        self.assertEqual(self.calls_matching("herdr", "run"), [])

    def test_a_pane_running_something_else_opens_a_new_review(self) -> None:
        """Our pane was taken over, so splitting is the only safe move left."""
        self.seed_pane_map(self.checkout, "w1:p9")
        self.stub_live_pane("w1:p9")
        self.stub_live_session(False)
        self.stub_process_info("w1:p9", foreground="vim")
        result = self.run_plugin("review-changes", self.context())
        self.assertSucceeded(result)
        self.assertEqual(len(self.calls_matching("herdr", "split")), 1)
        self.assertEqual(self.calls_matching("hunk", "reload"), [])

    def test_a_pane_recorded_for_another_checkout_is_not_reused(self) -> None:
        self.seed_pane_map("/somewhere/else", "w1:p9")
        self.stub_live_pane("w1:p9")
        self.stub_live_session(True)
        self.run_plugin("review-changes", self.context())
        self.assertEqual(len(self.calls_matching("herdr", "split")), 1)

    def test_reload_passes_watch_and_targets_the_checkout(self) -> None:
        self.seed_pane_map(self.checkout, "w1:p9")
        self.stub_live_pane("w1:p9")
        self.stub_live_session(True)
        self.stub_merge_base(head="cafe1234", base="beef5678")
        self.run_plugin("review-changes", self.context(worktree=self.worktree()))
        reload = self.calls_matching("hunk", "reload")[0]
        self.assertEqual(
            reload,
            [
                "hunk",
                "session",
                "reload",
                "--repo",
                self.checkout,
                "--",
                "diff",
                "beef5678",
                "--watch",
            ],
        )

    def test_repeated_invocation_never_increases_the_pane_count(self) -> None:
        """The principal success condition for a keybound action."""
        self.stub_live_pane("w1:p9")
        # First invocation: no live session yet, so it opens.
        self.rule("hunk", ["session", "get"], stderr="no live session\n", exit_code=1)
        first = self.run_plugin("review-changes", self.context())
        self.assertSucceeded(first)

        # Subsequent invocations see a live session and a live pane.
        self.rules = []
        self.stub_checkout()
        self.stub_split("w1:pSHOULD-NOT-HAPPEN")
        self.stub_live_pane("w1:p9")
        self.stub_live_session(True)
        for _ in range(2):
            self.assertSucceeded(self.run_plugin("review-changes", self.context()))

        self.assertEqual(len(self.calls_matching("herdr", "split")), 1)
        self.assertEqual(len(self.calls_matching("hunk", "reload")), 2)
        self.assertEqual(self.review_panes(), {self.checkout: "w1:p9"})

    def test_every_reload_passes_watch(self) -> None:
        self.seed_pane_map(self.checkout, "w1:p9")
        self.stub_live_pane("w1:p9")
        self.stub_live_session(True)
        for action in ("review-changes", "review-commit", "review-changes"):
            self.assertSucceeded(self.run_plugin(action, self.context()))
        reloads = self.calls_matching("hunk", "reload")
        self.assertEqual(len(reloads), 3)
        for reload in reloads:
            self.assertIn("--watch", reload)

    def test_review_changes_and_review_commit_share_one_pane(self) -> None:
        self.stub_live_pane("w1:p9")
        self.rule("hunk", ["session", "get"], stderr="no live session\n", exit_code=1)
        self.assertSucceeded(self.run_plugin("review-changes", self.context()))

        self.rules = []
        self.stub_checkout()
        self.stub_split("w1:pSHOULD-NOT-HAPPEN")
        self.stub_live_pane("w1:p9")
        self.stub_live_session(True)
        self.assertSucceeded(self.run_plugin("review-commit", self.context()))
        self.assertSucceeded(self.run_plugin("review-changes", self.context()))

        self.assertEqual(len(self.calls_matching("herdr", "split")), 1)
        targets = [
            call[call.index("--") + 1] for call in self.calls_matching("hunk", "reload")
        ]
        self.assertEqual(targets, ["show", "diff"])


if __name__ == "__main__":
    unittest.main()
