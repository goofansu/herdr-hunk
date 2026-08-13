#!/usr/bin/env python3
"""Herdr plugin: review agent-authored changes with Hunk.

Three actions, all invoked by the Herdr manifest as ``herdr_hunk.py <action>``:

``review-changes``
    Resolve the invoking pane's checkout and its correct review target, then
    open or reuse that agent's Hunk review pane beside it via ``hunk diff``.
``review-commit``
    The same, targeting the most recent commit via ``hunk show``.
``send-comments``
    Collect the user's review notes from the live Hunk session and stage them
    into the Hunk pane's paired agent.

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
import time

PANE_MAP_FILE = "review-panes.json"
NOTES_DIR = "notes"
HUNK_INSTALL_HINT = "npm install -g hunkdiff"
HUNK_TARGET_ENV = "HERDR_HUNK_TARGET_JSON"
PLUGIN_ID = "herdr-hunk"
PANE_ENTRYPOINT = "review"

# A just-launched Hunk takes a moment to register with its daemon. Wait out that
# gap rather than treating it as "no session" and splitting a duplicate pane.
# The ceiling is bounded by patience, not by Hunk: this runs on a keypress, and a
# review that has not appeared within a second and a half should say so rather
# than sit there. The cost is only paid when our pane is running Hunk and no
# session has registered, which is either startup or a genuinely broken daemon.
SESSION_WAIT_TIMEOUT = 1.5
SESSION_POLL_SECONDS = 0.25

USAGE = (
    "usage: herdr_hunk.py (review-changes | review-commit | send-comments | run-hunk)"
)


class PluginError(Exception):
    """A diagnosable failure. Raised before any layout mutation wherever possible."""


# ---------------------------------------------------------------------------
# process helpers
# ---------------------------------------------------------------------------


def _run(argv: list[str]) -> subprocess.CompletedProcess:
    try:
        # check=False: a nonzero exit is data here, not an exception. Callers
        # read returncode to tell "no live session" from "command failed".
        return subprocess.run(argv, capture_output=True, text=True, check=False)
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
    """Return ``checkout -> [agent/Hunk pair records]``, including stale entries.

    The two checkout-global formats used before pairing (a pane-id string or one
    record object) are accepted and normalized to one-item lists. They remain
    unpaired unless they already contain ``origin_pane``; an agent must never
    silently adopt an origin-less legacy Hunk.
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
    for checkout, value in entries.items():
        if not isinstance(checkout, str):
            continue
        if isinstance(value, str):
            records = [{"review_pane": value}]
        elif isinstance(value, dict):
            records = [value]
        elif isinstance(value, list):
            records = value
        else:
            continue
        valid = [
            record
            for record in records
            if isinstance(record, dict) and isinstance(record.get("review_pane"), str)
        ]
        if valid:
            reviews[checkout] = valid
    return reviews


def save_reviews(reviews: dict) -> None:
    path = os.path.join(state_dir(), PANE_MAP_FILE)
    temporary = path + f".{os.getpid()}.tmp"
    try:
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(reviews, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def review_pane_ids(reviews: dict) -> set[str]:
    """Every pane this plugin opened for Hunk, across all checkouts."""
    return {record["review_pane"] for records in reviews.values() for record in records}


def all_review_records(reviews: dict):
    for checkout, records in reviews.items():
        for record in records:
            yield checkout, record


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


def list_sessions() -> list[dict]:
    result = hunk(["session", "list", "--json"])
    if result.returncode != 0:
        raise PluginError(f"could not list live Hunk sessions: {_diagnostic(result)}")
    try:
        payload = json.loads(result.stdout)
    except ValueError as error:
        raise PluginError(f"unreadable session list from hunk: {error}") from error
    sessions = payload.get("sessions") if isinstance(payload, dict) else None
    if not isinstance(sessions, list):
        raise PluginError("unreadable session list from hunk: no sessions array")
    return [session for session in sessions if isinstance(session, dict)]


def session_id(session: dict) -> str | None:
    identifier = session.get("sessionId")
    return identifier if isinstance(identifier, str) and identifier else None


def sessions_for_checkout(sessions: list[dict], checkout: str) -> list[dict]:
    checkout = os.path.realpath(checkout)
    return [
        session
        for session in sessions
        if isinstance(session.get("repoRoot"), str)
        and os.path.realpath(session["repoRoot"]) == checkout
        and session_id(session)
    ]


def get_session(identifier: str) -> dict | None:
    result = hunk(["session", "get", identifier, "--json"])
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout)
    except ValueError:
        return None
    session = payload.get("session") if isinstance(payload, dict) else None
    return session if isinstance(session, dict) else None


def hunk_process_ids(pane_id: str) -> set[int]:
    """Foreground PIDs that could own the Hunk session in ``pane_id``."""
    processes = (pane_process_info(pane_id) or {}).get("foreground_processes")
    if not isinstance(processes, list):
        return set()
    return {
        process["pid"]
        for process in processes
        if isinstance(process, dict) and isinstance(process.get("pid"), int)
    }


def identify_session(
    checkout: str,
    pane_id: str,
    sessions: list[dict],
    previous_ids: set[str] | None = None,
    allow_existing: bool = False,
) -> dict | None:
    candidates = sessions_for_checkout(sessions, checkout)
    pids = hunk_process_ids(pane_id)
    by_pid = [session for session in candidates if session.get("pid") in pids]
    if len(by_pid) == 1:
        return by_pid[0]

    if previous_ids is not None:
        new = [
            session for session in candidates if session_id(session) not in previous_ids
        ]
        if len(new) == 1:
            return new[0]
    if allow_existing and len(candidates) == 1:
        return candidates[0]
    return None


def wait_for_session(
    checkout: str,
    pane_id: str,
    previous_ids: set[str] | None = None,
    allow_existing: bool = False,
) -> dict | None:
    """Poll for the session owned by one pane, never by repo recency."""
    deadline = time.monotonic() + SESSION_WAIT_TIMEOUT
    while True:
        session = identify_session(
            checkout,
            pane_id,
            list_sessions(),
            previous_ids=previous_ids,
            allow_existing=allow_existing,
        )
        if session:
            return session
        if time.monotonic() >= deadline:
            return None
        time.sleep(SESSION_POLL_SECONDS)


def record_session(record: dict, checkout: str, pane_id: str) -> dict | None:
    identifier = record.get("session_id")
    if isinstance(identifier, str):
        session = get_session(identifier)
        if session in sessions_for_checkout([session] if session else [], checkout):
            return session
    sessions = list_sessions()
    session = identify_session(checkout, pane_id, sessions)
    if session:
        return session
    # Legacy records did not retain a session id. A sole repo session is only
    # safe to adopt when the recorded pane is itself running Hunk; otherwise it
    # may be an unrelated manually opened session in another pane.
    if not record.get("plugin_pane") and pane_is_running_hunk(pane_id):
        return identify_session(checkout, pane_id, sessions, allow_existing=True)
    return None


def reload_session(identifier: str, target: list[str]) -> None:
    # --watch on every reload: a reload drops watch mode, and under reuse-by-default
    # the reload path is the common one, so omitting it freezes the review.
    result = hunk(["session", "reload", identifier, "--", *target, "--watch"])
    if result.returncode != 0:
        raise PluginError(f"hunk session reload failed: {_diagnostic(result)}")


def launch_hunk(pane_id: str, target: list[str]) -> None:
    command = ["hunk", *target, "--watch"]
    herdr(["pane", "run", pane_id, *(shlex.quote(word) for word in command)])


def pane_process_info(pane_id: str) -> dict | None:
    result = _run([herdr_bin(), "pane", "process-info", "--pane", pane_id])
    if result.returncode != 0:
        return None
    info = (response_result(result.stdout) or {}).get("process_info")
    return info if isinstance(info, dict) else None


def pane_is_idle_shell(pane_id: str) -> bool:
    """Whether the pane is back at its own prompt with nothing in the foreground.

    True of a review pane whose Hunk has exited, which is a pane this plugin can
    reuse rather than abandon.
    """
    info = pane_process_info(pane_id) or {}
    shell = info.get("shell_pid")
    return isinstance(shell, int) and shell == info.get("foreground_process_group_id")


def pane_is_running_hunk(pane_id: str) -> bool:
    info = pane_process_info(pane_id) or {}
    processes = info.get("foreground_processes")
    if not isinstance(processes, list):
        return False
    for process in processes:
        if not isinstance(process, dict):
            continue
        words = [process.get("name"), process.get("argv0"), process.get("cmdline")]
        argv = process.get("argv")
        if isinstance(argv, list):
            words.extend(argv)
        if any(
            isinstance(word, str)
            and (
                os.path.basename(word) in ("hunk", "hunkdiff") or "bin/hunk.cjs" in word
            )
            for word in words
        ):
            return True
    return False


def open_review_pane(invoking_pane: str, checkout: str, target: list[str]) -> dict:
    opened = herdr_json(
        [
            "plugin",
            "pane",
            "open",
            "--plugin",
            PLUGIN_ID,
            "--entrypoint",
            PANE_ENTRYPOINT,
            "--placement",
            "split",
            "--target-pane",
            invoking_pane,
            "--direction",
            "right",
            "--cwd",
            checkout,
            "--env",
            HUNK_TARGET_ENV + "=" + json.dumps(target, separators=(",", ":")),
            "--focus",
        ]
    )
    plugin_pane = opened.get("plugin_pane")
    pane = plugin_pane.get("pane") if isinstance(plugin_pane, dict) else None
    pane_id = pane.get("pane_id") if isinstance(pane, dict) else None
    if not isinstance(pane_id, str) or not pane_id:
        raise PluginError("herdr plugin pane open did not report a new pane")
    return pane


def pane_from_context(context: dict) -> dict:
    pane = {
        "pane_id": invoking_pane_id(context),
        "workspace_id": _text(context, "workspace_id"),
        "tab_id": _text(context, "tab_id"),
        "cwd": _text(context, "focused_pane_cwd"),
        "agent": _text(context, "focused_pane_agent"),
    }
    return {key: value for key, value in pane.items() if value is not None}


def set_origin(record: dict, pane: dict) -> None:
    record["origin_pane"] = pane["pane_id"]
    terminal = _text(pane, "terminal_id")
    if terminal:
        record["origin_terminal_id"] = terminal
    agent_session = pane.get("agent_session")
    if isinstance(agent_session, dict):
        record["origin_agent_session"] = agent_session


def set_review_pane(record: dict, pane: dict) -> None:
    record["review_pane"] = pane["pane_id"]
    terminal = _text(pane, "terminal_id")
    if terminal:
        record["review_terminal_id"] = terminal


def record_for_review_pane(
    reviews: dict, pane_id: str
) -> tuple[str, dict, dict | None] | None:
    """Resolve a pair by its Hunk pane, including a pane moved across workspaces."""
    invoking = get_pane(pane_id)
    invoking_terminal = _text(invoking or {}, "terminal_id")
    for checkout, record in all_review_records(reviews):
        recorded = record.get("review_pane")
        if recorded == pane_id:
            pane = invoking
            expected = record.get("review_terminal_id")
            if pane and (not expected or pane.get("terminal_id") == expected):
                return checkout, record, pane
    for checkout, record in all_review_records(reviews):
        recorded = record.get("review_pane")
        pane = get_pane(recorded) if isinstance(recorded, str) else None
        expected = record.get("review_terminal_id")
        if (
            pane
            and (not expected or pane.get("terminal_id") == expected)
            and (
                _text(pane, "pane_id") == _text(invoking or {}, "pane_id")
                or (
                    invoking_terminal
                    and invoking_terminal == _text(pane, "terminal_id")
                )
            )
        ):
            return checkout, record, pane
    return None


def current_origin(record: dict, context: dict | None = None) -> dict | None:
    origin_id = record.get("origin_pane")
    pane = get_pane(origin_id) if isinstance(origin_id, str) else None
    if (
        pane is None
        and context
        and origin_id == _text(context, "focused_pane_id")
        and _text(context, "focused_pane_agent")
    ):
        pane = pane_from_context(context)
    if not pane or not _text(pane, "agent"):
        return None
    expected_terminal = record.get("origin_terminal_id")
    if expected_terminal and expected_terminal != pane.get("terminal_id"):
        return None
    expected_session = record.get("origin_agent_session")
    current_session = pane.get("agent_session")
    if isinstance(expected_session, dict) and expected_session != current_session:
        return None
    set_origin(record, pane)
    return pane


def record_for_origin(reviews: dict, checkout: str, origin: dict) -> dict | None:
    pane_id = origin["pane_id"]
    terminal_id = _text(origin, "terminal_id")
    for record in reviews.get(checkout, []):
        if record.get("origin_pane") == pane_id:
            expected = record.get("origin_terminal_id")
            if not expected or not terminal_id or expected == terminal_id:
                return record
        if terminal_id and record.get("origin_terminal_id") == terminal_id:
            return record
        recorded = record.get("origin_pane")
        current = get_pane(recorded) if isinstance(recorded, str) else None
        expected = record.get("origin_terminal_id")
        if (
            current
            and _text(current, "pane_id") == pane_id
            and (not expected or not terminal_id or expected == terminal_id)
        ):
            return record
    return None


def discard_replaced_origin(reviews: dict, checkout: str, origin: dict) -> None:
    """Forget pairs owned by a prior occupant of this agent pane."""
    records = reviews.get(checkout, [])
    pane_id = origin["pane_id"]
    terminal_id = _text(origin, "terminal_id")
    agent_session = origin.get("agent_session")
    kept = []
    for record in records:
        same_pane = record.get("origin_pane") == pane_id
        expected_terminal = record.get("origin_terminal_id")
        expected_session = record.get("origin_agent_session")
        replaced = same_pane and (
            (expected_terminal and terminal_id and expected_terminal != terminal_id)
            or (
                isinstance(expected_session, dict)
                and isinstance(agent_session, dict)
                and expected_session != agent_session
            )
        )
        if not replaced:
            kept.append(record)
    if len(kept) != len(records):
        if kept:
            reviews[checkout] = kept
        else:
            reviews.pop(checkout, None)


def current_tab_agent(context: dict, reviews: dict) -> dict:
    workspace = workspace_id(context)
    tab = _text(context, "tab_id")
    if not tab:
        raise PluginError("no tab in the invocation context")
    invoking = invoking_pane_id(context)
    agents = [
        pane
        for pane in agent_panes(workspace, review_pane_ids(reviews)).values()
        if _text(pane, "tab_id") == tab
    ]
    if _text(context, "focused_pane_agent") and invoking not in {
        pane.get("pane_id") for pane in agents
    }:
        agents.append(pane_from_context(context))
    if len(agents) == 1:
        return agents[0]
    if not agents:
        raise PluginError(
            f"no agent pane in tab {tab}; invoke review from an agent or a shell "
            "beside exactly one agent"
        )
    choices = ", ".join(sorted(pane["pane_id"] for pane in agents))
    raise PluginError(
        f"multiple agent panes in tab {tab} ({choices}); invoke the action from "
        "the intended agent instead of guessing"
    )


def pair_origin(
    context: dict,
    reviews: dict,
    known_pair: tuple[str, dict, dict | None] | None,
) -> tuple[dict | None, dict | None]:
    if known_pair:
        record = known_pair[1]
        return current_origin(record, context), record
    if _text(context, "focused_pane_agent"):
        origin = get_pane(invoking_pane_id(context)) or pane_from_context(context)
        return origin, None
    return current_tab_agent(context, reviews), None


def replace_record(
    reviews: dict, checkout: str, old_record: dict | None, new_record: dict
) -> None:
    records = reviews.setdefault(checkout, [])
    if old_record in records:
        records[records.index(old_record)] = new_record
    else:
        records.append(new_record)


def current_review_pane(record: dict) -> dict | None:
    pane_id = record.get("review_pane")
    pane = get_pane(pane_id) if isinstance(pane_id, str) else None
    expected = record.get("review_terminal_id")
    if pane and expected and pane.get("terminal_id") != expected:
        return None
    if pane:
        set_review_pane(record, pane)
    return pane


def focus_review(record: dict, review_pane: dict, origin: dict | None = None) -> None:
    """Focus an exact managed pane; legacy panes can only be focused by tab."""
    pane_id = record["review_pane"]
    focused = False
    if record.get("plugin_pane"):
        result = _run([herdr_bin(), "plugin", "pane", "focus", pane_id])
        focused = result.returncode == 0
        if not focused:
            print(
                f"herdr-hunk: could not focus managed pane {pane_id}: "
                f"{_diagnostic(result)}",
                file=sys.stderr,
            )
    if not focused:
        tab = _text(review_pane, "tab_id")
        if tab:
            result = _run([herdr_bin(), "tab", "focus", tab])
            focused = result.returncode == 0

    if origin and _text(origin, "tab_id") != _text(review_pane, "tab_id"):
        location = (
            f"workspace {_text(review_pane, 'workspace_id') or '?'} / "
            f"tab {_text(review_pane, 'tab_id') or '?'} / pane {pane_id}"
        )
        print(f"herdr-hunk: paired Hunk is in {location}", file=sys.stderr)
        _run(
            [
                herdr_bin(),
                "notification",
                "show",
                "Focused paired Hunk",
                "--body",
                location,
                "--sound",
                "none",
            ]
        )


def capture_note_targets(record: dict, identifier: str, target: list[str]) -> None:
    old_target = record.get("target")
    if old_target == target:
        return
    try:
        notes = list_user_notes(identifier)
    except PluginError:
        record["note_context_uncertain"] = True
        return
    mappings = record.get("note_targets")
    if not isinstance(mappings, dict):
        mappings = {}
        record["note_targets"] = mappings
    previous = (
        old_target if isinstance(old_target, list) else "unknown (pre-pairing review)"
    )
    for note in notes:
        note_key = note_identity(note)
        if note_key not in mappings:
            mappings[note_key] = previous
    record.pop("note_context_uncertain", None)


def action_review(action: str) -> int:
    context = read_context()
    invoking_pane = invoking_pane_id(context)
    require_hunk()
    reviews = load_reviews()
    known_pair = record_for_review_pane(reviews, invoking_pane)
    checkout = (
        known_pair[0] if known_pair else resolve_checkout(start_directory(context))
    )
    target = ["show"] if action == "review-commit" else diff_target(context, checkout)
    origin, known_record = pair_origin(context, reviews, known_pair)
    if known_record and origin is None:
        # Pairing follows the current agent occupant. A dead/replaced agent makes
        # this relationship stale instead of transferable to another agent.
        reviews[checkout].remove(known_record)
        if not reviews[checkout]:
            del reviews[checkout]
        save_reviews(reviews)
        raise PluginError(
            "the paired agent is gone or was replaced; invoke review from the "
            "current agent to create a new pair"
        )
    if origin is not None:
        discard_replaced_origin(reviews, checkout, origin)
    record = known_record or record_for_origin(reviews, checkout, origin)

    if record:
        origin = current_origin(record, context)
        if origin is None:
            reviews[checkout].remove(record)
            if not reviews[checkout]:
                del reviews[checkout]
            record = None

    if record:
        review_pane = current_review_pane(record)
        if review_pane:
            pane_id = record["review_pane"]
            session = record_session(record, checkout, pane_id)
            if not session and pane_is_running_hunk(pane_id):
                session = wait_for_session(
                    checkout,
                    pane_id,
                    allow_existing=not record.get("plugin_pane"),
                )
                if not session:
                    focus_review(record, review_pane, origin)
                    raise PluginError(
                        f"Hunk is running in {pane_id} but its session did not "
                        "register; the Hunk daemon may be unreachable"
                    )

            verb = None
            if session:
                identifier = session_id(session)
                capture_note_targets(record, identifier, target)
                reload_session(identifier, target)
                record["session_id"] = identifier
                verb = "reloaded"
            elif pane_is_idle_shell(pane_id):
                before = {
                    session_id(item) for item in list_sessions() if session_id(item)
                }
                launch_hunk(pane_id, target)
                session = wait_for_session(checkout, pane_id, previous_ids=before)
                if not session:
                    raise PluginError(
                        f"relaunched Hunk in {pane_id}, but its session did not register"
                    )
                record["session_id"] = session_id(session)
                verb = "relaunched"

            if verb:
                set_origin(record, origin)
                record["target"] = target
                save_reviews(reviews)
                focus_review(record, review_pane, origin)
                print(
                    f"{verb} paired Hunk in {pane_id} "
                    f"({_text(review_pane, 'workspace_id') or '?'}/"
                    f"{_text(review_pane, 'tab_id') or '?'}): {' '.join(target)}"
                )
                return 0

    if origin is None:
        raise PluginError(
            "the paired agent is gone or was replaced; invoke review from the "
            "current agent to create a new pair"
        )
    before = {session_id(item) for item in list_sessions() if session_id(item)}
    review_pane = open_review_pane(origin["pane_id"], checkout, target)
    pane_id = review_pane["pane_id"]
    new_record = {"review_pane": pane_id, "plugin_pane": True, "target": target}
    set_origin(new_record, origin)
    set_review_pane(new_record, review_pane)
    replace_record(reviews, checkout, record, new_record)
    save_reviews(reviews)

    session = wait_for_session(checkout, pane_id, previous_ids=before)
    if not session:
        raise PluginError(
            f"opened Hunk in {pane_id}, but its session did not register; "
            "the Hunk daemon may be unreachable"
        )
    new_record["session_id"] = session_id(session)
    save_reviews(reviews)
    print(f"opened paired Hunk in {pane_id}: {' '.join(target)}")
    return 0


# ---------------------------------------------------------------------------
# send-comments
# ---------------------------------------------------------------------------


def list_user_notes(identifier: str) -> list[dict]:
    result = hunk(
        ["session", "comment", "list", identifier, "--type", "user", "--json"]
    )
    if result.returncode != 0:
        raise PluginError(
            f"no live Hunk review for session {identifier}: {_diagnostic(result)}"
        )
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


def target_label(target) -> str:
    if isinstance(target, list) and all(isinstance(word, str) for word in target):
        return "hunk " + " ".join(target)
    if isinstance(target, str) and target:
        return target
    return "unknown Hunk target"


def note_identity(note: dict) -> str:
    """Stable-enough identity for target attribution, including older Hunk notes."""
    for key in ("noteId", "commentId", "id"):
        identifier = note.get(key)
        if isinstance(identifier, str) and identifier:
            return identifier
    fields = [
        note.get("source"),
        note.get("filePath"),
        note.get("newRange"),
        note.get("oldRange"),
        note.get("hunkIndex"),
        note.get("title"),
        note.get("body"),
        note.get("createdAt"),
    ]
    encoded = json.dumps(fields, separators=(",", ":"), sort_keys=True)
    return "content:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def format_notes(
    checkout: str, notes: list[dict], note_targets: dict, current_target
) -> str:
    lines = [
        "# Hunk review notes",
        "",
        f"Checkout: {checkout}",
        f"Current review target: `{target_label(current_target)}`",
        "",
    ]
    for note in notes:
        lines.append(f"## {note_location(note)}")
        lines.append("")
        target = note_targets.get(note_identity(note), current_target)
        lines.append(f"Review target: `{target_label(target)}`")
        lines.append("")
        title = note.get("title")
        if isinstance(title, str) and title.strip():
            lines.append(f"**{title.strip()}**")
            lines.append("")
        body = note.get("body")
        lines.append(body.strip() if isinstance(body, str) else "(empty note)")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def notes_path(checkout: str, pair_key: str) -> str:
    directory = os.path.join(state_dir(), NOTES_DIR)
    os.makedirs(directory, exist_ok=True)
    digest = hashlib.sha256((checkout + "\0" + pair_key).encode("utf-8")).hexdigest()[
        :12
    ]
    name = os.path.basename(checkout.rstrip(os.sep)) or "checkout"
    safe = "".join(char if char.isalnum() or char in "-_" else "-" for char in name)
    return os.path.join(directory, f"{safe}-{digest}.md")


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


def manual_session(context: dict, checkout: str) -> dict:
    sessions = list_sessions()
    session = identify_session(
        checkout, invoking_pane_id(context), sessions, allow_existing=True
    )
    if session:
        return session
    candidates = sessions_for_checkout(sessions, checkout)
    if not candidates:
        raise PluginError(f"no live Hunk review for {checkout}")
    identifiers = ", ".join(session_id(item) for item in candidates)
    raise PluginError(
        f"multiple live Hunk sessions match {checkout} ({identifiers}); invoke "
        "send-comments from the intended Hunk pane so its process can be identified"
    )


def manual_target(session: dict) -> str:
    for key in ("title", "sourceLabel", "inputKind"):
        value = session.get(key)
        if isinstance(value, str) and value:
            return f"unmanaged Hunk session: {value}"
    return f"unmanaged Hunk session {session_id(session) or '(unknown)'}"


def note_target_map(record: dict | None, notes: list[dict], current_target) -> dict:
    if not record:
        return {}
    mappings = record.get("note_targets")
    if not isinstance(mappings, dict):
        mappings = {}
        record["note_targets"] = mappings
    fallback = (
        "unknown (review target changed before note context was captured)"
        if record.get("note_context_uncertain")
        else current_target
    )
    for note in notes:
        note_key = note_identity(note)
        if note_key not in mappings:
            mappings[note_key] = fallback
    # Once every currently visible note has been assigned the uncertainty
    # marker, notes created afterward can safely inherit the current target.
    record.pop("note_context_uncertain", None)
    return mappings


def focus_agent(pane_id: str) -> None:
    focus = _run([herdr_bin(), "agent", "focus", pane_id])
    if focus.returncode != 0:
        print(
            f"herdr-hunk: could not focus {pane_id}: {_diagnostic(focus)}",
            file=sys.stderr,
        )


def action_send_comments() -> int:
    context = read_context()
    invoking = invoking_pane_id(context)
    require_hunk()
    reviews = load_reviews()
    by_review = record_for_review_pane(reviews, invoking)
    checkout = by_review[0] if by_review else resolve_checkout(start_directory(context))

    record = by_review[1] if by_review else None
    if record:
        origin = current_origin(record, context)
        if origin is None:
            raise PluginError(
                "the paired agent is gone or was replaced; invoke review from the "
                "current agent to create a new pair"
            )
    else:
        if _text(context, "focused_pane_agent"):
            origin = get_pane(invoking) or pane_from_context(context)
        else:
            origin = current_tab_agent(context, reviews)
        record = record_for_origin(reviews, checkout, origin)
        if record:
            origin = current_origin(record, context)
            if origin is None:
                raise PluginError(
                    "the paired agent is gone or was replaced; invoke review from "
                    "the current agent to create a new pair"
                )

    if record:
        review_pane = current_review_pane(record)
        if not review_pane:
            raise PluginError(
                "the paired Hunk pane is gone; invoke review from the agent to "
                "create a new pair"
            )
        session = record_session(record, checkout, record["review_pane"])
        if not session:
            raise PluginError(
                f"no live Hunk session for the pair in {record['review_pane']}; "
                "invoke review from the agent to relaunch it"
            )
        identifier = session_id(session)
        record["session_id"] = identifier
        current_target = record.get("target") or manual_target(session)
    else:
        session = manual_session(context, checkout)
        identifier = session_id(session)
        claimed = next(
            (
                pair
                for pair in reviews.get(checkout, [])
                if pair.get("session_id") == identifier
            ),
            None,
        )
        if claimed:
            raise PluginError(
                f"Hunk session {identifier} belongs to another agent pair; invoke "
                "send-comments from that pair's Hunk, or invoke review from the "
                "current agent to create a new pair"
            )
        current_target = manual_target(session)

    notes = list_user_notes(identifier)
    if not notes:
        raise PluginError(f"no review notes in Hunk session {identifier}")
    mappings = note_target_map(record, notes, current_target)
    body = format_notes(checkout, notes, mappings, current_target)
    pair_key = (
        str(record.get("origin_terminal_id") or record.get("origin_pane"))
        if record
        else identifier
    )
    path = notes_path(checkout, pair_key)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(body)

    agent_pane = origin["pane_id"]
    instruction = (
        f"Read my review notes in {path} and address each one; "
        "the file lists every note by review target, file, and line."
    )
    digest = hashlib.sha256((body + "\0" + instruction).encode("utf-8")).hexdigest()
    previous = record.get("last_stage") if record else None
    duplicate = (
        isinstance(previous, dict)
        and previous.get("digest") == digest
        and previous.get("agent_terminal_id") == origin.get("terminal_id")
    )
    if not duplicate:
        # One line: send-text is literal and non-submitting. The user sees it in
        # the paired agent and deliberately decides whether to press Enter.
        herdr(["pane", "send-text", agent_pane, instruction])
        if record is not None:
            record["last_stage"] = {
                "digest": digest,
                "agent_terminal_id": origin.get("terminal_id"),
            }
            save_reviews(reviews)
    elif record is not None:
        save_reviews(reviews)

    focus_agent(agent_pane)
    if duplicate:
        print(
            f"identical review notes were already staged in {agent_pane}; focused "
            "the existing instruction"
        )
    else:
        print(f"staged {len(notes)} review note(s) in {agent_pane} via {path}")
    return 0


def action_run_hunk() -> int:
    """Managed-pane entrypoint: replace this process with the requested Hunk TUI."""
    require_hunk()
    raw = os.environ.get(HUNK_TARGET_ENV)
    try:
        target = json.loads(raw or "")
    except ValueError as error:
        raise PluginError(f"unreadable managed Hunk target: {error}") from error
    if (
        not isinstance(target, list)
        or not target
        or target[0] not in ("diff", "show")
        or not all(isinstance(word, str) and word for word in target)
    ):
        raise PluginError("unreadable managed Hunk target")
    try:
        os.execvp("hunk", ["hunk", *target, "--watch"])
    except OSError as error:
        raise PluginError(f"could not launch hunk: {error}") from error


# ---------------------------------------------------------------------------
# entrypoint
# ---------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(USAGE, file=sys.stderr)
        return 2
    action = argv[0]
    try:
        if action in ("review-changes", "review-commit"):
            return action_review(action)
        if action == "send-comments":
            return action_send_comments()
        if action == "run-hunk":
            return action_run_hunk()
    except PluginError as error:
        print(f"herdr-hunk: {error}", file=sys.stderr)
        return 1
    print(f"herdr-hunk: unknown action {action!r}\n{USAGE}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
