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
CWD = "/work/tree with spaces"

FAKE = r"""#!{python}
import json
import os
import sys

program = {program!r}
with open(os.environ["CALL_LOG"], "a", encoding="utf-8") as handle:
    handle.write(json.dumps([program, *sys.argv[1:]]) + "\n")

if program == "git":
    # Every Git query the plugin issues runs as `git -C <cwd> ...`, so the
    # query shape starts after the directory flag. Each shape answers from its
    # own variable, letting a test stage one step without touching the others.
    query = sys.argv[3:]
    if query == ["rev-parse", "--is-inside-work-tree"]:
        print(os.environ.get("GIT_INSIDE", "true"))
        sys.exit(int(os.environ.get("GIT_EXIT", "0")))
    if query[0] == "symbolic-ref":
        remote_head = os.environ.get("GIT_REMOTE_HEAD", "refs/remotes/origin/main")
        if not remote_head:
            sys.exit(1)
        print(remote_head)
        sys.exit(0)
    if query[:2] == ["rev-parse", "--verify"]:
        if query[-1] not in os.environ.get("GIT_LOCAL_BRANCHES", "").split():
            sys.exit(1)
        print("localbranchcommit")
        sys.exit(0)
    if query[0] == "merge-base":
        merge_base = os.environ.get("GIT_MERGE_BASE", "mergebasecommit")
        if not merge_base:
            sys.exit(1)
        print(merge_base)
        sys.exit(0)
if program == "hunk":
    signal = os.environ.get("HUNK_SIGNAL")
    if signal:
        os.kill(os.getpid(), int(signal))
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
            "focused_pane_cwd": CWD,
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

    def git_query(self, *query: str) -> list[str]:
        return ["git", "-C", CWD, *query]

    def pane_open(self, entrypoint: str, *env: str) -> list[str]:
        settings = []
        for setting in env:
            settings += ["--env", setting]
        return [
            "herdr",
            "plugin",
            "pane",
            "open",
            "--plugin",
            "herdr-hunk",
            "--entrypoint",
            entrypoint,
            "--target-pane",
            "w1:p1",
            "--cwd",
            CWD,
            *settings,
        ]

    def notification(
        self, body: str, title: str = "Hunk review not opened"
    ) -> list[str]:
        return ["herdr", "notification", "show", title, "--body", body]

    def failure(self, body: str) -> list[str]:
        return self.notification(body, title="Hunk review failed")

    def test_opens_hunk_without_inspecting_the_current_tab_layout(self) -> None:
        result = self.invoke("review-uncommitted-changes")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.calls(),
            [
                self.git_query("rev-parse", "--is-inside-work-tree"),
                self.pane_open("uncommitted-review"),
            ],
        )

    def test_opens_last_commit_review_in_a_right_split(self) -> None:
        result = self.invoke("review-last-commit")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls()[1], self.pane_open("last-commit-review"))

    def test_notifies_instead_of_opening_hunk_outside_a_git_repository(self) -> None:
        result = self.invoke("review-uncommitted-changes", GIT_EXIT=128, GIT_INSIDE="")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(f"[{CWD}] is not a Git repository", result.stderr)
        self.assertEqual(
            self.calls(),
            [
                self.git_query("rev-parse", "--is-inside-work-tree"),
                self.notification(f"[{CWD}] is not a Git repository."),
            ],
        )

    def test_runs_watched_hunk_diff_without_explicitly_closing_the_pane(self) -> None:
        result = self.invoke("run-uncommitted-changes-review")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [["hunk", "diff", "--watch"]])

    def test_runs_hunk_show_for_last_commit(self) -> None:
        result = self.invoke("run-last-commit-review")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [["hunk", "show"]])

    def test_notifies_hunk_failure_without_explicitly_closing_the_pane(self) -> None:
        result = self.invoke("run-uncommitted-changes-review", HUNK_EXIT=7)
        self.assertEqual(result.returncode, 7)
        self.assertEqual(
            self.calls(),
            [
                ["hunk", "diff", "--watch"],
                self.failure("Hunk exited with status 7."),
            ],
        )

    def test_notifies_when_hunk_is_not_installed(self) -> None:
        os.remove(os.path.join(self.bin, "hunk"))
        result = self.invoke("run-uncommitted-changes-review")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("could not run hunk", result.stderr)
        self.assertEqual(len(self.calls()), 1)
        self.assertEqual(self.calls()[0][:3], ["herdr", "notification", "show"])
        self.assertEqual(self.calls()[0][3], "Hunk review failed")
        self.assertIn("could not run hunk", self.calls()[0][-1])

    def test_opens_branch_review_with_the_merge_base_in_the_pane_environment(
        self,
    ) -> None:
        result = self.invoke("review-branch-changes")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.calls(),
            [
                self.git_query("rev-parse", "--is-inside-work-tree"),
                self.git_query("symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"),
                self.git_query("merge-base", "refs/remotes/origin/main", "HEAD"),
                self.pane_open(
                    "branch-review",
                    "HERDR_HUNK_REVIEW_BASE=mergebasecommit",
                ),
            ],
        )

    def test_resolves_the_default_branch_from_the_remote_head(self) -> None:
        result = self.invoke(
            "review-branch-changes",
            GIT_REMOTE_HEAD="refs/remotes/origin/trunk",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            self.git_query("merge-base", "refs/remotes/origin/trunk", "HEAD"),
            self.calls(),
        )

    def test_falls_back_to_a_local_main_branch_when_no_remote_head_is_set(self) -> None:
        result = self.invoke(
            "review-branch-changes",
            GIT_REMOTE_HEAD="",
            GIT_LOCAL_BRANCHES="refs/heads/main refs/heads/master",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.calls()[1:4],
            [
                self.git_query("symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"),
                self.git_query("rev-parse", "--verify", "--quiet", "refs/heads/main"),
                self.git_query("merge-base", "refs/heads/main", "HEAD"),
            ],
        )

    def test_falls_back_to_a_local_master_branch_when_main_is_absent(self) -> None:
        result = self.invoke(
            "review-branch-changes",
            GIT_REMOTE_HEAD="",
            GIT_LOCAL_BRANCHES="refs/heads/master",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.calls()[1:5],
            [
                self.git_query("symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"),
                self.git_query("rev-parse", "--verify", "--quiet", "refs/heads/main"),
                self.git_query("rev-parse", "--verify", "--quiet", "refs/heads/master"),
                self.git_query("merge-base", "refs/heads/master", "HEAD"),
            ],
        )

    def test_notifies_instead_of_opening_a_branch_review_without_a_default_branch(
        self,
    ) -> None:
        result = self.invoke("review-branch-changes", GIT_REMOTE_HEAD="")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            f"[{CWD}] has no default branch to compare against", result.stderr
        )
        self.assertEqual(
            self.calls()[-1],
            self.notification(f"[{CWD}] has no default branch to compare against."),
        )

    def test_notifies_instead_of_opening_a_branch_review_without_a_merge_base(
        self,
    ) -> None:
        result = self.invoke("review-branch-changes", GIT_MERGE_BASE="")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            f"[{CWD}] has no merge base with refs/remotes/origin/main", result.stderr
        )
        self.assertEqual(
            self.calls()[-1],
            self.notification(
                f"[{CWD}] has no merge base with refs/remotes/origin/main."
            ),
        )

    def test_opens_branch_review_when_the_branch_has_nothing_of_its_own(self) -> None:
        result = self.invoke("review-branch-changes", GIT_MERGE_BASE="samecommit")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.calls()[-1],
            self.pane_open("branch-review", "HERDR_HUNK_REVIEW_BASE=samecommit"),
        )

    def test_notifies_instead_of_opening_a_branch_review_outside_a_git_repository(
        self,
    ) -> None:
        result = self.invoke("review-branch-changes", GIT_EXIT=128, GIT_INSIDE="")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(f"[{CWD}] is not a Git repository", result.stderr)
        self.assertEqual(
            self.calls(),
            [
                self.git_query("rev-parse", "--is-inside-work-tree"),
                self.notification(f"[{CWD}] is not a Git repository."),
            ],
        )

    def test_runs_watched_hunk_diff_against_the_base_from_the_pane_environment(
        self,
    ) -> None:
        result = self.invoke(
            "run-branch-changes-review",
            HERDR_HUNK_REVIEW_BASE="mergebasecommit",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.calls(), [["hunk", "diff", "mergebasecommit", "--watch"]])

    def test_fails_without_running_hunk_when_the_branch_review_base_is_missing(
        self,
    ) -> None:
        for base in ({}, {"HERDR_HUNK_REVIEW_BASE": ""}):
            with self.subTest(base=base):
                result = self.invoke("run-branch-changes-review", **base)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("HERDR_HUNK_REVIEW_BASE is not set", result.stderr)
                # Both rounds append to one call log, so read the latest.
                self.assertEqual(
                    self.calls()[-1],
                    self.failure("HERDR_HUNK_REVIEW_BASE is not set"),
                )

    def test_notifies_hunk_failure_from_the_branch_review(self) -> None:
        result = self.invoke(
            "run-branch-changes-review",
            HERDR_HUNK_REVIEW_BASE="mergebasecommit",
            HUNK_EXIT=7,
        )
        self.assertEqual(result.returncode, 7)
        self.assertEqual(
            self.calls(),
            [
                ["hunk", "diff", "mergebasecommit", "--watch"],
                self.failure("Hunk exited with status 7."),
            ],
        )

    def test_reports_nothing_when_a_signal_ends_hunk(self) -> None:
        """A pane the reviewer closed kills Hunk, which is not a failure."""
        result = self.invoke("run-uncommitted-changes-review", HUNK_SIGNAL="15")
        self.assertEqual(result.returncode, 143)
        self.assertEqual(self.calls(), [["hunk", "diff", "--watch"]])


if __name__ == "__main__":
    unittest.main()
