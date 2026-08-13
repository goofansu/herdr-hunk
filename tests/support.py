"""Test harness for the Herdr Hunk plugin.

The seam under test is the plugin script's argv interface. Every test runs the
script as a subprocess with a hermetic ``PATH`` containing fake ``herdr``,
``hunk``, and ``git`` executables. The fakes append their argv to a log file and
reply from a rule table supplied through the environment, so a test can assert
exactly which CLI calls the plugin made and drive each of them to a scripted
outcome.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(REPO_ROOT, "herdr_hunk.py")

MISSING = object()

FAKE_BODY = '''
import json
import os
import sys

argv = [PROGRAM] + sys.argv[1:]

with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as handle:
    handle.write(json.dumps(argv) + "\\n")


def matches(match, argv):
    """True when ``match`` appears in ``argv`` as an ordered subsequence."""
    index = 0
    for token in match:
        while index < len(argv) and argv[index] != token:
            index += 1
        if index == len(argv):
            return False
        index += 1
    return True


def prior_calls(match):
    """How many logged calls so far match, including the one in flight."""
    seen = 0
    with open(os.environ["FAKE_LOG"], encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                entry = json.loads(line)
                if entry[0] == PROGRAM and matches(match, entry):
                    seen += 1
    return seen


for rule in json.loads(os.environ.get("FAKE_RULES", "[]")):
    if rule["program"] == PROGRAM and matches(rule["match"], argv):
        if prior_calls(rule["match"]) <= rule.get("after", 0):
            continue
        sys.stdout.write(rule.get("stdout", ""))
        sys.stderr.write(rule.get("stderr", ""))
        sys.exit(rule.get("exit", 0))

sys.exit(0)
'''


def envelope(result: dict) -> str:
    """Render a Herdr CLI response envelope."""
    return json.dumps({"id": "cli:test", "result": result}) + "\n"


def pane_info(pane_id: str, **overrides) -> dict:
    """A ``PaneInfo`` payload with the fields the plugin reads."""
    pane = {
        "pane_id": pane_id,
        "terminal_id": "term_" + pane_id.replace(":", "_"),
        "workspace_id": pane_id.split(":")[0],
        "tab_id": pane_id.split(":")[0] + ":t1",
        "focused": False,
        "agent_status": "unknown",
        "revision": 1,
    }
    pane.update(overrides)
    return pane


class PluginTestCase(unittest.TestCase):
    """Base class that installs the fakes and runs the plugin."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="herdr-hunk-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.bin_dir = os.path.join(self.tmp, "bin")
        self.state_dir = os.path.join(self.tmp, "state")
        self.checkout = os.path.join(self.tmp, "checkout")
        self.repo_root = os.path.join(self.tmp, "repo")
        for path in (self.bin_dir, self.state_dir, self.checkout, self.repo_root):
            os.makedirs(path)
        self.log = os.path.join(self.tmp, "calls.jsonl")
        self.rules: list[dict] = []
        self.programs = {"herdr", "hunk", "git"}

    # -- fakes ---------------------------------------------------------------

    def _write_fakes(self) -> None:
        for program in sorted(self.programs):
            path = os.path.join(self.bin_dir, program)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("#!" + sys.executable + "\n")
                handle.write("PROGRAM = " + repr(program) + "\n")
                handle.write(FAKE_BODY)
            os.chmod(path, 0o755)

    def rule(
        self,
        program: str,
        match: list[str],
        stdout: str = "",
        stderr: str = "",
        exit_code: int = 0,
        after: int = 0,
    ) -> None:
        """Append a reply rule. Earlier rules win over later ones.

        ``after`` delays a rule until the program has already been called that
        many times with a matching argv, which models state that settles — a
        Hunk session that only registers with its daemon a moment later.
        """
        self.rules.append(
            {
                "program": program,
                "match": match,
                "stdout": stdout,
                "stderr": stderr,
                "exit": exit_code,
                "after": after,
            }
        )

    def herdr_result(self, match: list[str], result: dict) -> None:
        self.rule("herdr", match, stdout=envelope(result))

    # -- convenient defaults -------------------------------------------------

    def stub_checkout(self, toplevel: str | None = None) -> None:
        """``git rev-parse --show-toplevel`` resolves to the checkout."""
        self.rule(
            "git",
            ["rev-parse", "--show-toplevel"],
            stdout=(toplevel or self.checkout) + "\n",
        )

    def stub_merge_base(self, head: str = "cafe1234", base: str = "beef5678") -> None:
        self.rule(
            "git", ["-C", self.repo_root, "rev-parse", "HEAD"], stdout=head + "\n"
        )
        self.rule("git", ["merge-base", head, "HEAD"], stdout=base + "\n")

    def stub_split(self, pane_id: str = "w1:p9") -> None:
        self.herdr_result(
            ["pane", "split"], {"type": "pane_info", "pane": pane_info(pane_id)}
        )

    def stub_live_session(self, live: bool = True) -> None:
        if live:
            self.rule("hunk", ["session", "get"], stdout="Session: s1\n")
        else:
            self.rule(
                "hunk",
                ["session", "get"],
                stderr="No live Hunk session for this repo.\n",
                exit_code=1,
            )

    def stub_live_pane(self, pane_id: str, alive: bool = True) -> None:
        if alive:
            self.herdr_result(
                ["pane", "get", pane_id],
                {"type": "pane_info", "pane": pane_info(pane_id)},
            )
        else:
            self.rule(
                "herdr",
                ["pane", "get", pane_id],
                stderr="pane not found\n",
                exit_code=1,
            )

    def seed_pane_map(
        self, checkout: str, pane_id: str, origin: str | None = None
    ) -> None:
        record = {"review_pane": pane_id}
        if origin:
            record["origin_pane"] = origin
        self.write_pane_map({checkout: record})

    def stub_process_info(self, pane_id: str, foreground: str | None = None) -> None:
        """``pane process-info``: idle at the shell, or running ``foreground``."""
        shell_pid = 100
        processes = [{"name": "fish", "pid": shell_pid}]
        pgid = shell_pid
        if foreground:
            pgid = shell_pid + 1
            processes = [{"name": foreground, "pid": pgid}]
        self.herdr_result(
            ["process-info", "--pane", pane_id],
            {
                "type": "pane_process_info",
                "process_info": {
                    "pane_id": pane_id,
                    "shell_pid": shell_pid,
                    "foreground_process_group_id": pgid,
                    "foreground_processes": processes,
                },
            },
        )

    def stub_agent_list(self, sequences: dict) -> None:
        """``herdr agent list`` reporting a state-change sequence per pane."""
        self.herdr_result(
            ["agent", "list"],
            {
                "type": "agent_list",
                "agents": [
                    {"pane_id": pane_id, "agent": "claude", "state_change_seq": seq}
                    for pane_id, seq in sequences.items()
                ],
            },
        )

    def write_pane_map(self, entries: dict) -> None:
        """Write the state file verbatim, including legacy shapes."""
        with open(
            os.path.join(self.state_dir, "review-panes.json"), "w", encoding="utf-8"
        ) as handle:
            json.dump(entries, handle)

    # -- invocation ----------------------------------------------------------

    def context(self, **overrides) -> dict:
        context = {
            "workspace_id": "w1",
            "workspace_label": "herdr-hunk",
            "workspace_cwd": self.checkout,
            "tab_id": "w1:t1",
            "focused_pane_id": "w1:p1",
            "focused_pane_cwd": self.checkout,
            "invocation_source": "keybinding",
        }
        context.update(overrides)
        return {key: value for key, value in context.items() if value is not None}

    def worktree(self, **overrides) -> dict:
        worktree = {
            "repo_key": "herdr-hunk",
            "repo_name": "herdr-hunk",
            "repo_root": self.repo_root,
            "checkout_path": self.checkout,
            "is_linked_worktree": True,
        }
        worktree.update(overrides)
        return worktree

    def run_plugin(self, action: str, context=MISSING, extra_env: dict | None = None):
        self._write_fakes()
        env = {
            "PATH": self.bin_dir,
            "HOME": self.tmp,
            "FAKE_LOG": self.log,
            "FAKE_RULES": json.dumps(self.rules),
            "HERDR_PLUGIN_ID": "herdr-hunk",
            "HERDR_PLUGIN_ROOT": REPO_ROOT,
            "HERDR_PLUGIN_STATE_DIR": self.state_dir,
            "HERDR_ENV": "1",
        }
        if context is not MISSING:
            env["HERDR_PLUGIN_CONTEXT_JSON"] = (
                context if isinstance(context, str) else json.dumps(context)
            )
        # A None value unsets the variable rather than setting it.
        for key, value in (extra_env or {}).items():
            if value is None:
                env.pop(key, None)
            else:
                env[key] = value
        return subprocess.run(
            [sys.executable, PLUGIN, action],
            capture_output=True,
            text=True,
            env=env,
            cwd=self.tmp,
        )

    # -- assertions ----------------------------------------------------------

    def calls(self, program: str | None = None) -> list[list[str]]:
        if not os.path.exists(self.log):
            return []
        with open(self.log, encoding="utf-8") as handle:
            entries = [json.loads(line) for line in handle if line.strip()]
        if program is None:
            return entries
        return [entry for entry in entries if entry[0] == program]

    def calls_matching(self, program: str, *tokens: str) -> list[list[str]]:
        return [
            call
            for call in self.calls(program)
            if all(token in call for token in tokens)
        ]

    def review_panes(self) -> dict:
        """``checkout -> review pane id``, dropping the rest of each record."""
        return {
            checkout: record["review_pane"]
            for checkout, record in self.pane_map().items()
        }

    def pane_map(self) -> dict:
        path = os.path.join(self.state_dir, "review-panes.json")
        if not os.path.exists(path):
            return {}
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)

    def assertFailed(self, result, *fragments: str) -> None:
        self.assertNotEqual(
            result.returncode, 0, f"expected failure, got:\n{result.stdout}"
        )
        message = result.stderr
        for fragment in fragments:
            self.assertIn(fragment, message)

    def assertSucceeded(self, result) -> None:
        self.assertEqual(
            result.returncode, 0, f"expected success, stderr:\n{result.stderr}"
        )
