from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(ROOT, "herdr_hunk.py")

FAKE = r"""#!{python}
import json
import os
import sys

program = {program!r}
with open(os.environ["CALL_LOG"], "a", encoding="utf-8") as handle:
    handle.write(json.dumps([program, *sys.argv[1:]]) + "\n")

if program == "git" and sys.argv[-2:] == ["rev-parse", "--is-inside-work-tree"]:
    print(os.environ.get("GIT_INSIDE", "true"))
    sys.exit(int(os.environ.get("GIT_EXIT", "0")))
if program == "hunk":
    sys.exit(int(os.environ.get("HUNK_EXIT", "0")))
sys.exit(int(os.environ.get("HERDR_EXIT", "0")))
"""


class PluginTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.bin = os.path.join(self.tmp, "bin")
        os.mkdir(self.bin)
        self.log = os.path.join(self.tmp, "calls.jsonl")
        for program in ("git", "herdr", "hunk"):
            path = os.path.join(self.bin, program)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(FAKE.format(python=sys.executable, program=program))
            os.chmod(path, 0o755)

    def context(self) -> dict:
        return {
            "focused_pane_id": "w1:p1",
            "focused_pane_cwd": "/work/tree with spaces",
        }

    def invoke(self, command: str, **extra):
        env = {
            "PATH": self.bin,
            "CALL_LOG": self.log,
            "HERDR_PLUGIN_CONTEXT_JSON": json.dumps(self.context()),
        }
        env.update({key: str(value) for key, value in extra.items()})
        return subprocess.run(
            [sys.executable, PLUGIN, command],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )

    def calls(self) -> list[list[str]]:
        if not os.path.exists(self.log):
            return []
        with open(self.log, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle]

    def test_opens_hunk_without_inspecting_the_current_tab_layout(self) -> None:
        result = self.invoke("review-live-changes")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.calls(),
            [
                [
                    "git",
                    "-C",
                    "/work/tree with spaces",
                    "rev-parse",
                    "--is-inside-work-tree",
                ],
                [
                    "herdr",
                    "plugin",
                    "pane",
                    "open",
                    "--plugin",
                    "herdr-hunk",
                    "--entrypoint",
                    "review",
                    "--target-pane",
                    "w1:p1",
                    "--cwd",
                    "/work/tree with spaces",
                ],
            ],
        )

    def test_opens_last_commit_review_in_a_right_split(self) -> None:
        result = self.invoke("review-last-commit")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.calls()[1],
            [
                "herdr",
                "plugin",
                "pane",
                "open",
                "--plugin",
                "herdr-hunk",
                "--entrypoint",
                "last-commit-review",
                "--target-pane",
                "w1:p1",
                "--cwd",
                "/work/tree with spaces",
            ],
        )

    def test_notifies_instead_of_opening_hunk_outside_a_git_repository(self) -> None:
        result = self.invoke("review-live-changes", GIT_EXIT=128, GIT_INSIDE="")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("[/work/tree with spaces] is not a Git repository", result.stderr)
        self.assertEqual(
            self.calls(),
            [
                [
                    "git",
                    "-C",
                    "/work/tree with spaces",
                    "rev-parse",
                    "--is-inside-work-tree",
                ],
                [
                    "herdr",
                    "notification",
                    "show",
                    "Hunk review not opened",
                    "--body",
                    "[/work/tree with spaces] is not a Git repository.",
                ],
            ],
        )

    def test_runs_watched_hunk_diff_without_explicitly_closing_the_pane(self) -> None:
        result = self.invoke("run-live-changes-review")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [["hunk", "diff", "--watch"]])

    def test_runs_hunk_show_for_last_commit(self) -> None:
        result = self.invoke("run-last-commit-review")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [["hunk", "show"]])

    def test_propagates_hunk_failure_without_explicitly_closing_the_pane(self) -> None:
        result = self.invoke("run-live-changes-review", HUNK_EXIT=7)
        self.assertEqual(result.returncode, 7)
        self.assertEqual(self.calls(), [["hunk", "diff", "--watch"]])


if __name__ == "__main__":
    unittest.main()
