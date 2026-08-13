"""Review target resolution: which changeset Hunk is pointed at."""

from __future__ import annotations

import os
import unittest

from tests.support import PluginTestCase


class TargetResolutionTest(PluginTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.stub_checkout()
        self.stub_split("w1:p9")

    def hunk_command(self) -> list[str]:
        """The Hunk command line the plugin asked Herdr to run in the review pane."""
        runs = self.calls_matching("herdr", "run")
        self.assertEqual(len(runs), 1, f"expected one pane run, got {runs}")
        return runs[0][runs[0].index("hunk") :]

    def test_worktree_with_committed_work_targets_the_merge_base(self) -> None:
        self.stub_merge_base(head="cafe1234", base="beef5678")
        result = self.run_plugin("review", self.context(worktree=self.worktree()))
        self.assertSucceeded(result)
        self.assertEqual(self.hunk_command(), ["hunk", "diff", "beef5678", "--watch"])

    def test_merge_base_is_computed_in_the_checkout_against_the_parent_head(
        self,
    ) -> None:
        self.stub_merge_base(head="cafe1234", base="beef5678")
        self.run_plugin("review", self.context(worktree=self.worktree()))
        self.assertIn(
            ["git", "-C", self.repo_root, "rev-parse", "HEAD"],
            self.calls("git"),
        )
        self.assertIn(
            ["git", "-C", self.checkout, "merge-base", "cafe1234", "HEAD"],
            self.calls("git"),
        )

    def test_worktree_rooted_at_the_repo_reviews_the_working_tree(self) -> None:
        """A workspace whose checkout is the repo itself has no parent to diff against."""
        result = self.run_plugin(
            "review",
            self.context(
                worktree=self.worktree(
                    repo_root=self.checkout, is_linked_worktree=False
                )
            ),
        )
        self.assertSucceeded(result)
        self.assertEqual(self.hunk_command(), ["hunk", "diff", "--watch"])

    def test_non_worktree_context_reviews_the_working_tree(self) -> None:
        result = self.run_plugin("review", self.context())
        self.assertSucceeded(result)
        self.assertEqual(self.hunk_command(), ["hunk", "diff", "--watch"])

    def test_unreadable_parent_head_degrades_to_a_working_tree_review(self) -> None:
        self.rule(
            "git",
            ["-C", self.repo_root, "rev-parse", "HEAD"],
            stderr="fatal\n",
            exit_code=128,
        )
        result = self.run_plugin("review", self.context(worktree=self.worktree()))
        self.assertSucceeded(result)
        self.assertEqual(self.hunk_command(), ["hunk", "diff", "--watch"])

    def test_absent_merge_base_degrades_to_a_working_tree_review(self) -> None:
        self.rule(
            "git", ["-C", self.repo_root, "rev-parse", "HEAD"], stdout="cafe1234\n"
        )
        self.rule("git", ["merge-base"], stderr="fatal: no merge base\n", exit_code=1)
        result = self.run_plugin("review", self.context(worktree=self.worktree()))
        self.assertSucceeded(result)
        self.assertEqual(self.hunk_command(), ["hunk", "diff", "--watch"])

    def test_review_commit_targets_the_last_commit(self) -> None:
        self.stub_merge_base()
        result = self.run_plugin(
            "review-commit", self.context(worktree=self.worktree())
        )
        self.assertSucceeded(result)
        self.assertEqual(self.hunk_command(), ["hunk", "show", "--watch"])

    def test_review_commit_does_not_compute_a_merge_base(self) -> None:
        self.stub_merge_base()
        self.run_plugin("review-commit", self.context(worktree=self.worktree()))
        self.assertEqual(self.calls_matching("git", "merge-base"), [])


class CheckoutResolutionTest(PluginTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.stub_split("w1:p9")

    def test_nested_invocation_directory_resolves_to_the_checkout_root(self) -> None:
        nested = os.path.join(self.checkout, "src", "deep")
        os.makedirs(nested)
        self.stub_checkout()
        result = self.run_plugin("review", self.context(focused_pane_cwd=nested))
        self.assertSucceeded(result)
        self.assertIn(
            ["git", "-C", nested, "rev-parse", "--show-toplevel"], self.calls("git")
        )
        split = self.calls_matching("herdr", "split")[0]
        self.assertIn(self.checkout, split)

    def test_a_directory_outside_a_git_repository_fails(self) -> None:
        self.rule(
            "git",
            ["rev-parse", "--show-toplevel"],
            stderr="fatal: not a git repository\n",
            exit_code=128,
        )
        result = self.run_plugin("review", self.context())
        self.assertFailed(result, "not a Git repository")
        self.assertEqual(self.calls_matching("herdr", "split"), [])

    def test_a_path_containing_spaces_survives_to_every_cli_call(self) -> None:
        spaced = os.path.join(self.tmp, "my checkout")
        os.makedirs(spaced)
        self.stub_checkout(toplevel=spaced)
        result = self.run_plugin("review", self.context(focused_pane_cwd=spaced))
        self.assertSucceeded(result)
        self.assertIn(
            ["git", "-C", spaced, "rev-parse", "--show-toplevel"], self.calls("git")
        )
        self.assertIn(spaced, self.calls_matching("herdr", "split")[0])
        self.assertEqual(self.review_panes(), {spaced: "w1:p9"})

    def test_workspace_cwd_is_used_when_the_pane_has_no_cwd(self) -> None:
        self.stub_checkout()
        result = self.run_plugin("review", self.context(focused_pane_cwd=None))
        self.assertSucceeded(result)
        self.assertIn(
            ["git", "-C", self.checkout, "rev-parse", "--show-toplevel"],
            self.calls("git"),
        )


if __name__ == "__main__":
    unittest.main()
