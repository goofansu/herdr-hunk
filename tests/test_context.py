"""Invocation context handling: the plugin must never guess an unrelated target."""

from __future__ import annotations

import unittest

from tests.support import PluginTestCase


class ContextTest(PluginTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.stub_checkout()
        self.stub_split("w1:p9")

    def test_pane_context_targets_the_pane_supplied_in_context(self) -> None:
        result = self.run_plugin("review", self.context(focused_pane_id="w1:p4"))
        self.assertSucceeded(result)
        split = self.calls_matching("herdr", "split")[0]
        self.assertIn("w1:p4", split)

    def test_absent_context_fails_without_acting(self) -> None:
        result = self.run_plugin("review")
        self.assertFailed(result, "HERDR_PLUGIN_CONTEXT_JSON")
        self.assertEqual(self.calls("herdr"), [])
        self.assertEqual(self.calls("git"), [])

    def test_malformed_context_fails_without_acting(self) -> None:
        result = self.run_plugin("review", "{not json")
        self.assertFailed(result, "invocation context")
        self.assertEqual(self.calls("herdr"), [])

    def test_non_object_context_fails_without_acting(self) -> None:
        result = self.run_plugin("review", "[1, 2, 3]")
        self.assertFailed(result, "invocation context")
        self.assertEqual(self.calls("herdr"), [])

    def test_missing_pane_id_fails_without_acting(self) -> None:
        result = self.run_plugin("review", self.context(focused_pane_id=None))
        self.assertFailed(result, "invoking pane")
        self.assertEqual(self.calls_matching("herdr", "split"), [])

    def test_missing_working_directory_fails_without_acting(self) -> None:
        """A worktree block is not a substitute: the pane, then the workspace, or fail."""
        result = self.run_plugin(
            "review",
            self.context(
                focused_pane_cwd=None, workspace_cwd=None, worktree=self.worktree()
            ),
        )
        self.assertFailed(result, "working directory")
        self.assertEqual(self.calls("git"), [])

    def test_unknown_action_fails(self) -> None:
        result = self.run_plugin("frobnicate", self.context())
        self.assertFailed(result, "unknown action")

    def test_herdr_bin_path_is_preferred_over_path_lookup(self) -> None:
        import os
        import shutil

        self._write_fakes()
        alias = os.path.join(self.tmp, "herdr-from-env")
        shutil.copyfile(os.path.join(self.bin_dir, "herdr"), alias)
        os.chmod(alias, 0o755)
        os.remove(os.path.join(self.bin_dir, "herdr"))
        self.programs.discard("herdr")

        result = self.run_plugin(
            "review", self.context(), extra_env={"HERDR_BIN_PATH": alias}
        )
        self.assertSucceeded(result)
        self.assertEqual(len(self.calls_matching("herdr", "split")), 1)


if __name__ == "__main__":
    unittest.main()
