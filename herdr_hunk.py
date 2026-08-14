"""Open a self-closing Hunk review beside a lone Herdr pane."""

from __future__ import annotations

import json
import os
import subprocess
import sys

PLUGIN_ID = "herdr-hunk"
LIVE_REVIEW_ENTRYPOINT = "review"
LAST_COMMIT_ENTRYPOINT = "last-commit-review"
USAGE = (
    "usage: herdr_hunk.py "
    "(review-live-changes | review-last-commit | run-live-changes-review | "
    "run-last-commit-review)"
)


class PluginError(Exception):
    pass


def text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def herdr_bin() -> str:
    return os.environ.get("HERDR_BIN_PATH") or "herdr"


def run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(argv, check=False, **kwargs)
    except OSError as error:
        raise PluginError(f"could not run {argv[0]}: {error}") from error


def diagnostic(result: subprocess.CompletedProcess) -> str:
    for output in (result.stderr, result.stdout):
        if isinstance(output, str) and output.strip():
            return output.strip().splitlines()[0]
    return f"exit status {result.returncode}"


def read_context() -> dict:
    raw = os.environ.get("HERDR_PLUGIN_CONTEXT_JSON")
    if not raw:
        raise PluginError("HERDR_PLUGIN_CONTEXT_JSON is not set")
    try:
        context = json.loads(raw)
    except ValueError as error:
        raise PluginError(f"could not read plugin context: {error}") from error
    if not isinstance(context, dict):
        raise PluginError("plugin context is not an object")
    return context


def herdr_json(args: list[str]) -> dict:
    result = run([herdr_bin(), *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise PluginError(f"herdr {' '.join(args)} failed: {diagnostic(result)}")
    try:
        response = json.loads(result.stdout)
        payload = response["result"]
    except (ValueError, KeyError, TypeError) as error:
        raise PluginError(f"herdr {' '.join(args)} returned invalid JSON") from error
    if not isinstance(payload, dict):
        raise PluginError(f"herdr {' '.join(args)} returned an invalid result")
    return payload


def required_context_value(context: dict, key: str, label: str) -> str:
    value = text(context.get(key))
    if not value:
        raise PluginError(f"plugin context has no {label}")
    return value


def notify(title: str, body: str) -> None:
    run(
        [herdr_bin(), "notification", "show", title, "--body", body],
        capture_output=True,
        text=True,
    )


def open_review(entrypoint: str, action_title: str) -> int:
    """Open a review pane while hiding Herdr's layout and context plumbing."""
    context = read_context()
    workspace_id = required_context_value(context, "workspace_id", "workspace")
    tab_id = required_context_value(context, "tab_id", "tab")
    pane_id = required_context_value(context, "focused_pane_id", "focused pane")
    cwd = required_context_value(context, "focused_pane_cwd", "focused pane cwd")

    payload = herdr_json(["pane", "list", "--workspace", workspace_id])
    panes = payload.get("panes")
    if not isinstance(panes, list):
        raise PluginError("herdr pane list returned no panes")
    current_tab_panes = [
        pane
        for pane in panes
        if isinstance(pane, dict) and pane.get("tab_id") == tab_id
    ]
    if len(current_tab_panes) != 1:
        message = (
            f"{action_title} requires exactly one pane in the current tab; "
            f"found {len(current_tab_panes)}."
        )
        notify("Hunk review not opened", message)
        raise PluginError(message)
    if current_tab_panes[0].get("pane_id") != pane_id:
        raise PluginError("the focused pane does not match the current tab's pane")

    result = run(
        [
            herdr_bin(),
            "plugin",
            "pane",
            "open",
            "--plugin",
            PLUGIN_ID,
            "--entrypoint",
            entrypoint,
            "--placement",
            "split",
            "--target-pane",
            pane_id,
            "--direction",
            "right",
            "--cwd",
            cwd,
            "--focus",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise PluginError(f"could not open Hunk pane: {diagnostic(result)}")
    return 0


def review_live_changes() -> int:
    return open_review(LIVE_REVIEW_ENTRYPOINT, "Review live changes")


def review_last_commit() -> int:
    return open_review(LAST_COMMIT_ENTRYPOINT, "Review last commit")


def run_review(hunk_args: list[str]) -> int:
    # Herdr removes a plugin pane when its initial process exits. Let this
    # wrapper end naturally with Hunk instead of explicitly closing the pane;
    # an explicit close races with the runtime's PaneDied event.
    return run(["hunk", *hunk_args]).returncode


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(USAGE, file=sys.stderr)
        return 2
    try:
        if argv[0] == "review-live-changes":
            return review_live_changes()
        if argv[0] == "review-last-commit":
            return review_last_commit()
        if argv[0] == "run-live-changes-review":
            return run_review(["diff", "--watch"])
        if argv[0] == "run-last-commit-review":
            return run_review(["show"])
    except PluginError as error:
        print(f"herdr-hunk: {error}", file=sys.stderr)
        return 1
    print(f"herdr-hunk: unknown command {argv[0]!r}\n{USAGE}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
