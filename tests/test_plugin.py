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

if program == "herdr" and sys.argv[1:3] == ["pane", "list"]:
    panes = json.loads(os.environ.get("PANES", "[]"))
    print(json.dumps({{"result": {{"panes": panes}}}}))
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
        for program in ("herdr", "hunk"):
            path = os.path.join(self.bin, program)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(FAKE.format(python=sys.executable, program=program))
            os.chmod(path, 0o755)

    def context(self) -> dict:
        return {
            "workspace_id": "w1",
            "tab_id": "w1:t1",
            "focused_pane_id": "w1:p1",
            "focused_pane_cwd": "/work/tree with spaces",
        }

    def invoke(self, command: str, panes=None, **extra):
        env = {
            "PATH": self.bin,
            "CALL_LOG": self.log,
            "PANES": json.dumps(panes if panes is not None else []),
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

    def test_opens_hunk_in_a_right_split_from_a_lone_pane(self) -> None:
        panes = [
            {"pane_id": "w1:p1", "tab_id": "w1:t1"},
            {"pane_id": "w1:p9", "tab_id": "w1:t2"},
        ]
        result = self.invoke("review-live-changes", panes)
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
                "review",
                "--placement",
                "split",
                "--target-pane",
                "w1:p1",
                "--direction",
                "right",
                "--cwd",
                "/work/tree with spaces",
                "--focus",
            ],
        )

    def test_opens_last_commit_review_in_a_right_split(self) -> None:
        panes = [{"pane_id": "w1:p1", "tab_id": "w1:t1"}]
        result = self.invoke("review-last-commit", panes)
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
                "--placement",
                "split",
                "--target-pane",
                "w1:p1",
                "--direction",
                "right",
                "--cwd",
                "/work/tree with spaces",
                "--focus",
            ],
        )

    def test_refuses_to_open_when_the_current_tab_has_two_panes(self) -> None:
        panes = [
            {"pane_id": "w1:p1", "tab_id": "w1:t1"},
            {"pane_id": "w1:p2", "tab_id": "w1:t1"},
        ]
        result = self.invoke("review-live-changes", panes)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("found 2", result.stderr)
        self.assertEqual(
            self.calls(),
            [
                ["herdr", "pane", "list", "--workspace", "w1"],
                [
                    "herdr",
                    "notification",
                    "show",
                    "Hunk review not opened",
                    "--body",
                    (
                        "Review live changes requires exactly one pane in the "
                        "current tab; found 2."
                    ),
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
