#!/usr/bin/env python3
"""Herdr plugin: review agent-authored changes with Hunk.

Three actions, all invoked by the Herdr manifest as ``herdr_hunk.py <action>``:

``review``
    Resolve the invoking pane's checkout and its correct review target, then
    open or reuse a single Hunk review pane beside it.
``review-commit``
    The same, targeting the most recent commit via ``hunk show``.
``send-comments``
    Collect the user's review notes from the live Hunk session and stage them
    into the agent pane that produced the code.

Standard library only, no build step. Herdr, Hunk, and Git must already be
installed; every call this plugin makes is a documented CLI subcommand.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys

PANE_MAP_FILE = "review-panes.json"
NOTES_DIR = "notes"
HUNK_INSTALL_HINT = "npm install -g hunkdiff"

USAGE = "usage: herdr_hunk.py (review | review-commit | send-comments)"


class PluginError(Exception):
    """A diagnosable failure. Raised before any layout mutation wherever possible."""


# ---------------------------------------------------------------------------
# process helpers
# ---------------------------------------------------------------------------


def _run(argv: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(argv, capture_output=True, text=True)
    except OSError as error:
        raise PluginError(f"could not run {argv[0]}: {error}") from error


def _diagnostic(result: subprocess.CompletedProcess) -> str:
    for stream in (result.stderr, result.stdout):
        for line in (stream or "").splitlines():
            if line.strip():
                return line.strip()
    return f"exit status {result.returncode}"


def herdr_bin() -> str:
    return os.environ.get("HERDR_BIN_PATH") or "herdr"


def response_result(stdout: str) -> dict | None:
    """The ``result`` object of a Herdr CLI response envelope, or None if unreadable."""
    try:
        payload = json.loads(stdout)
    except ValueError:
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("result"), dict):
        return None
    return payload["result"]


def herdr(args: list[str]) -> None:
    """Call the Herdr CLI, raising on failure and ignoring the response body."""
    result = _run([herdr_bin(), *args])
    if result.returncode != 0:
        raise PluginError("herdr " + " ".join(args) + f" failed: {_diagnostic(result)}")


def herdr_json(args: list[str]) -> dict:
    """Call the Herdr CLI and return the ``result`` object, raising on failure."""
    result = _run([herdr_bin(), *args])
    label = "herdr " + " ".join(args)
    if result.returncode != 0:
        raise PluginError(f"{label} failed: {_diagnostic(result)}")
    payload = response_result(result.stdout)
    if payload is None:
        raise PluginError(f"{label} returned an unreadable response")
    return payload


def hunk(args: list[str]) -> subprocess.CompletedProcess:
    return _run(["hunk", *args])


def git(args: list[str]) -> subprocess.CompletedProcess:
    return _run(["git", *args])


def require_hunk() -> None:
    if shutil.which("hunk") is None:
        raise PluginError(
            "hunk is not installed or not on PATH; install it with: "
            + HUNK_INSTALL_HINT
        )


# ---------------------------------------------------------------------------
# invocation context
# ---------------------------------------------------------------------------


def read_context() -> dict:
    raw = os.environ.get("HERDR_PLUGIN_CONTEXT_JSON")
    if not raw:
        raise PluginError("no invocation context: HERDR_PLUGIN_CONTEXT_JSON is not set")
    try:
        context = json.loads(raw)
    except ValueError as error:
        raise PluginError(f"unreadable invocation context: {error}") from error
    if not isinstance(context, dict):
        raise PluginError("unreadable invocation context: expected a JSON object")
    return context


def _text(context: dict, key: str) -> str | None:
    value = context.get(key)
    return value if isinstance(value, str) and value else None


def invoking_pane_id(context: dict) -> str:
    pane_id = _text(context, "focused_pane_id")
    if not pane_id:
        raise PluginError("no invoking pane in the invocation context")
    return pane_id


def workspace_id(context: dict) -> str:
    identifier = _text(context, "workspace_id")
    if not identifier:
        raise PluginError("no workspace in the invocation context")
    return identifier


def start_directory(context: dict) -> str:
    """Where to begin resolving the checkout: the invoking pane, then the workspace."""
    for key in ("focused_pane_cwd", "workspace_cwd"):
        directory = _text(context, key)
        if directory:
            return directory
    raise PluginError("no working directory in the invocation context")


# ---------------------------------------------------------------------------
# review target
# ---------------------------------------------------------------------------


def resolve_checkout(start_dir: str) -> str:
    result = git(["-C", start_dir, "rev-parse", "--show-toplevel"])
    toplevel = result.stdout.strip()
    if result.returncode != 0 or not toplevel:
        raise PluginError(f"not a Git repository: {start_dir}")
    return toplevel


def diff_target(context: dict, checkout: str) -> list[str]:
    """``diff <merge-base>`` inside a linked worktree, plain ``diff`` otherwise.

    A committed changeset is invisible to a plain working-tree diff, which is the
    silent failure this plugin exists to remove. When the base cannot be read the
    review degrades to the working tree rather than failing.
    """
    worktree = context.get("worktree")
    if not isinstance(worktree, dict):
        return ["diff"]
    repo_root = _text(worktree, "repo_root")
    if not repo_root or os.path.realpath(repo_root) == os.path.realpath(checkout):
        return ["diff"]

    head = git(["-C", repo_root, "rev-parse", "HEAD"])
    if head.returncode != 0 or not head.stdout.strip():
        return ["diff"]
    base = git(["-C", checkout, "merge-base", head.stdout.strip(), "HEAD"])
    if base.returncode != 0 or not base.stdout.strip():
        return ["diff"]
    return ["diff", base.stdout.strip()]


# ---------------------------------------------------------------------------
# plugin state
# ---------------------------------------------------------------------------


def state_dir() -> str:
    directory = os.environ.get("HERDR_PLUGIN_STATE_DIR")
    if not directory:
        raise PluginError("HERDR_PLUGIN_STATE_DIR is not set")
    os.makedirs(directory, exist_ok=True)
    return directory


def load_reviews() -> dict:
    """``checkout -> {"review_pane": id, "origin_pane": id}``, stale entries included.

    A bare string value is the older single-pane format and still reads.
    """
    path = os.path.join(state_dir(), PANE_MAP_FILE)
    try:
        with open(path, encoding="utf-8") as handle:
            entries = json.load(handle)
    except (OSError, ValueError):
        return {}
    if not isinstance(entries, dict):
        return {}
    reviews = {}
    for checkout, record in entries.items():
        if isinstance(record, str):
            reviews[checkout] = {"review_pane": record}
        elif isinstance(record, dict) and isinstance(record.get("review_pane"), str):
            reviews[checkout] = record
    return reviews


def save_reviews(reviews: dict) -> None:
    path = os.path.join(state_dir(), PANE_MAP_FILE)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(reviews, handle, indent=2, sort_keys=True)


def review_pane_ids(reviews: dict) -> set[str]:
    """Every pane this plugin opened for Hunk, across all checkouts."""
    return {record["review_pane"] for record in reviews.values()}


# ---------------------------------------------------------------------------
# review
# ---------------------------------------------------------------------------


def get_pane(pane_id: str) -> dict | None:
    """The pane's info, or None when Herdr no longer knows the pane."""
    result = _run([herdr_bin(), "pane", "get", pane_id])
    if result.returncode != 0:
        return None
    pane = (response_result(result.stdout) or {}).get("pane")
    return pane if isinstance(pane, dict) else None


def session_is_live(checkout: str) -> bool:
    return hunk(["session", "get", "--repo", checkout]).returncode == 0


def reload_session(checkout: str, target: list[str]) -> None:
    # --watch on every reload: a reload drops watch mode, and under reuse-by-default
    # the reload path is the common one, so omitting it freezes the review.
    result = hunk(["session", "reload", "--repo", checkout, "--", *target, "--watch"])
    if result.returncode != 0:
        raise PluginError(f"hunk session reload failed: {_diagnostic(result)}")


def open_review_pane(invoking_pane: str, checkout: str, target: list[str]) -> str:
    split = herdr_json(
        [
            "pane",
            "split",
            invoking_pane,
            "--direction",
            "right",
            "--cwd",
            checkout,
            "--focus",
        ]
    )
    pane = split.get("pane")
    pane_id = pane.get("pane_id") if isinstance(pane, dict) else None
    if not isinstance(pane_id, str) or not pane_id:
        raise PluginError("herdr pane split did not report a new pane")

    command = ["hunk", *target, "--watch"]
    try:
        herdr(["pane", "run", pane_id, *(shlex.quote(word) for word in command)])
    except PluginError:
        # Leave the layout as we found it rather than stranding an empty pane.
        _run([herdr_bin(), "pane", "close", pane_id])
        raise
    return pane_id


def action_review(action: str) -> int:
    context = read_context()
    invoking_pane = invoking_pane_id(context)
    require_hunk()
    reviews = load_reviews()
    checkout = resolve_checkout(start_directory(context))
    target = ["show"] if action == "review-commit" else diff_target(context, checkout)

    recorded = reviews.get(checkout, {}).get("review_pane")
    review_pane = get_pane(recorded) if recorded else None
    if review_pane and session_is_live(checkout):
        reload_session(checkout, target)
        # pane focus is directional only, so the review pane is surfaced by
        # focusing the workspace that holds it.
        workspace = _text(review_pane, "workspace_id") or _text(context, "workspace_id")
        if workspace:
            herdr(["workspace", "focus", workspace])
        print(f"reloaded Hunk review in {recorded}: {' '.join(target)}")
        return 0

    pane_id = open_review_pane(invoking_pane, checkout, target)
    # The pane the review was split from is the one that produced the code, which
    # is what send-comments needs when a workspace holds more than one agent.
    reviews[checkout] = {"review_pane": pane_id, "origin_pane": invoking_pane}
    save_reviews(reviews)
    print(f"opened Hunk review in {pane_id}: {' '.join(target)}")
    return 0


# ---------------------------------------------------------------------------
# send-comments
# ---------------------------------------------------------------------------


def list_user_notes(checkout: str) -> list[dict]:
    result = hunk(
        ["session", "comment", "list", "--repo", checkout, "--type", "user", "--json"]
    )
    if result.returncode != 0:
        raise PluginError(f"no live Hunk review for {checkout}: {_diagnostic(result)}")
    try:
        payload = json.loads(result.stdout)
    except ValueError as error:
        raise PluginError(f"unreadable note list from hunk: {error}") from error
    notes = payload.get("comments") if isinstance(payload, dict) else None
    if not isinstance(notes, list):
        raise PluginError("unreadable note list from hunk: no comments array")
    return [note for note in notes if isinstance(note, dict)]


def note_location(note: dict) -> str:
    path = note.get("filePath") or "(unknown file)"
    for key in ("newRange", "oldRange"):
        span = note.get(key)
        if isinstance(span, list) and span and isinstance(span[0], int):
            return f"{path}:{span[0]}"
    index = note.get("hunkIndex")
    if isinstance(index, int):
        return f"{path} (hunk {index + 1})"
    return str(path)


def format_notes(checkout: str, notes: list[dict]) -> str:
    lines = ["# Hunk review notes", "", f"Checkout: {checkout}", ""]
    for note in notes:
        lines.append(f"## {note_location(note)}")
        lines.append("")
        title = note.get("title")
        if isinstance(title, str) and title.strip():
            lines.append(f"**{title.strip()}**")
            lines.append("")
        body = note.get("body")
        lines.append(body.strip() if isinstance(body, str) else "(empty note)")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def notes_path(checkout: str) -> str:
    directory = os.path.join(state_dir(), NOTES_DIR)
    os.makedirs(directory, exist_ok=True)
    digest = hashlib.sha256(checkout.encode("utf-8")).hexdigest()[:12]
    name = os.path.basename(checkout.rstrip(os.sep)) or "checkout"
    safe = "".join(char if char.isalnum() or char in "-_" else "-" for char in name)
    return os.path.join(directory, f"{safe}-{digest}.md")


def find_agent_pane(
    workspace: str,
    context: dict,
    checkout: str,
    record: dict,
    review_panes: set[str],
) -> tuple[str, list[str]]:
    """The agent pane that produced the code, and any candidates passed over.

    Only an empty workspace is a failure. A wrong pick is staged unsubmitted in a
    pane the caller then focuses, so it is visible and inert; refusing to act on a
    review the user has already written is the worse outcome. The job here is
    therefore to pick well, not to abstain.
    """
    agents = agent_panes(workspace, review_panes)
    if not agents:
        raise PluginError(f"no agent pane in workspace {workspace}")

    # The pane this checkout's review was opened beside.
    origin = record.get("origin_pane")
    if isinstance(origin, str) and origin in agents:
        return origin, []
    # Failing that, the invoking pane, when send-comments is fired from the agent.
    invoking = _text(context, "focused_pane_id")
    if invoking and invoking in agents:
        return invoking, []

    # Narrow by the strongest signals a pane listing carries. An agent working in
    # this checkout is a likelier author than one somewhere else, and one sharing
    # the invoking tab is likelier still — which is the Hunk-opened-by-hand case,
    # where the review sits beside the agent that produced the code.
    tab = _text(context, "tab_id")
    here = [pane for pane in agents.values() if pane_is_within(pane, checkout)]
    same_tab = [pane for pane in here if _text(pane, "tab_id") == tab]
    for group in (same_tab, here, list(agents.values())):
        if len(group) == 1:
            return group[0]["pane_id"], []

    group = same_tab or here or list(agents.values())
    candidates = [pane["pane_id"] for pane in group]
    chosen = most_recently_active(candidates)
    return chosen, [pane_id for pane_id in candidates if pane_id != chosen]


def agent_panes(workspace: str, review_panes: set[str]) -> dict:
    """Panes in the workspace running an agent, minus any this plugin opened."""
    listing = herdr_json(["pane", "list", "--workspace", workspace])
    panes = listing.get("panes")
    if not isinstance(panes, list):
        raise PluginError("herdr pane list returned an unexpected response shape")
    return {
        pane["pane_id"]: pane
        for pane in panes
        if isinstance(pane, dict)
        and isinstance(pane.get("pane_id"), str)
        and pane.get("agent")
        and pane["pane_id"] not in review_panes
    }


def pane_is_within(pane: dict, checkout: str) -> bool:
    root = os.path.realpath(checkout)
    for key in ("cwd", "foreground_cwd"):
        directory = _text(pane, key)
        if not directory:
            continue
        directory = os.path.realpath(directory)
        if directory == root or directory.startswith(root + os.sep):
            return True
    return False


def most_recently_active(candidates: list[str]) -> str:
    """Of several agents, the one whose lifecycle state changed last.

    The agent that just finished the work being reviewed is the one that moved
    most recently. Falls back to listing order when the sequence is unavailable.
    """
    result = _run([herdr_bin(), "agent", "list"])
    ranked: dict = {}
    if result.returncode == 0:
        agents = (response_result(result.stdout) or {}).get("agents")
        if isinstance(agents, list):
            for agent in agents:
                if isinstance(agent, dict) and isinstance(
                    agent.get("state_change_seq"), int
                ):
                    ranked[agent.get("pane_id")] = agent["state_change_seq"]
    return max(candidates, key=lambda pane_id: ranked.get(pane_id, -1))


def announce_ambiguity(chosen: str, passed_over: list[str]) -> None:
    """Say which agent was picked when more than one could have been the author.

    Plugin stdout is only visible in the command log, so a toast is what actually
    reaches the user. Best effort: this is a courtesy, not the work.
    """
    others = ", ".join(sorted(passed_over))
    print(f"herdr-hunk: chose {chosen}; also running: {others}", file=sys.stderr)
    _run(
        [
            herdr_bin(),
            "notification",
            "show",
            "Review notes staged",
            "--body",
            f"Staged in {chosen}. Other agents here: {others}.",
            "--sound",
            "none",
        ]
    )


def action_send_comments() -> int:
    context = read_context()
    workspace = workspace_id(context)
    require_hunk()
    checkout = resolve_checkout(start_directory(context))

    notes = list_user_notes(checkout)
    if not notes:
        raise PluginError(f"no review notes in the live Hunk review for {checkout}")

    path = notes_path(checkout)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(format_notes(checkout, notes))

    # Exclude every review pane this plugin knows about, not just this checkout's:
    # a pane recorded under another key is still Hunk, and must never be the target.
    reviews = load_reviews()
    agent_pane, passed_over = find_agent_pane(
        workspace,
        context,
        checkout,
        reviews.get(checkout, {}),
        review_pane_ids(reviews),
    )

    # One line: send-text delivers embedded newlines as Enter, which would submit
    # the message line by line into the agent's terminal.
    instruction = (
        f"Read my review notes in {path} and address each one; "
        "the file lists every note by file and line."
    )
    herdr(["pane", "send-text", agent_pane, instruction])
    if passed_over:
        announce_ambiguity(agent_pane, passed_over)

    # The text is staged, not submitted, so put the user in front of it to decide.
    # Best effort: the notes are already staged, and a failure to focus must not
    # report the action as failed when the work it was asked to do happened.
    focus = _run([herdr_bin(), "agent", "focus", agent_pane])
    if focus.returncode != 0:
        print(
            f"herdr-hunk: could not focus {agent_pane}: {_diagnostic(focus)}",
            file=sys.stderr,
        )

    print(f"staged {len(notes)} review note(s) in {agent_pane} via {path}")
    return 0


# ---------------------------------------------------------------------------
# entrypoint
# ---------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(USAGE, file=sys.stderr)
        return 2
    action = argv[0]
    try:
        if action in ("review", "review-commit"):
            return action_review(action)
        if action == "send-comments":
            return action_send_comments()
    except PluginError as error:
        print(f"herdr-hunk: {error}", file=sys.stderr)
        return 1
    print(f"herdr-hunk: unknown action {action!r}\n{USAGE}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
