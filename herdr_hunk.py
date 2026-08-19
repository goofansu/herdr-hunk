"""Open a self-closing Hunk review beside a Herdr pane."""

from __future__ import annotations

import json
import os
import subprocess
import sys

PLUGIN_ID = "herdr-hunk"
LIVE_REVIEW_ENTRYPOINT = "review"
LAST_COMMIT_ENTRYPOINT = "last-commit-review"
BRANCH_REVIEW_ENTRYPOINT = "branch-review"
REVIEW_BASE_ENV = "HERDR_HUNK_REVIEW_BASE"
REMOTE_HEAD_REF = "refs/remotes/origin/HEAD"
LOCAL_DEFAULT_REFS = ("refs/heads/main", "refs/heads/master")
USAGE = (
    "usage: herdr_hunk.py "
    "(review-live-changes | review-last-commit | review-branch-changes | "
    "run-live-changes-review | run-last-commit-review | "
    "run-branch-changes-review)"
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


def not_opened(message: str) -> PluginError:
    """Explain a refused review in a notification as well as on stderr."""
    notify("Hunk review not opened", message)
    return PluginError(message)


def git(cwd: str, *query: str) -> subprocess.CompletedProcess:
    return run(["git", "-C", cwd, *query], capture_output=True, text=True)


def git_output(cwd: str, *query: str) -> str | None:
    result = git(cwd, *query)
    if result.returncode != 0:
        return None
    return text(result.stdout.strip())


def require_git_repository(cwd: str) -> None:
    if git_output(cwd, "rev-parse", "--is-inside-work-tree") == "true":
        return
    raise not_opened(f"[{cwd}] is not a Git repository.")


def default_branch_ref(cwd: str) -> str | None:
    """Prefer the remote's HEAD, then a local main or master branch."""
    ref = git_output(cwd, "symbolic-ref", "--quiet", REMOTE_HEAD_REF)
    if ref:
        return ref
    for candidate in LOCAL_DEFAULT_REFS:
        if git_output(cwd, "rev-parse", "--verify", "--quiet", candidate):
            return candidate
    return None


def working_tree_is_dirty(cwd: str) -> bool:
    """Report uncommitted work, keeping a failed query out of the clean answer."""
    result = git(cwd, "status", "--porcelain")
    if result.returncode != 0:
        raise not_opened(
            f"[{cwd}] could not be checked for uncommitted changes: "
            f"{diagnostic(result)}"
        )
    return bool(result.stdout.strip())


def branch_review_base(cwd: str) -> str:
    """Resolve the branch's starting commit before any pane opens.

    The base is frozen here as a commit id, so later movement on the default
    branch cannot shift a review that is already open, and every way of
    failing to find it stays a notification instead of a pane that dies.
    """
    ref = default_branch_ref(cwd)
    if not ref:
        raise not_opened(f"[{cwd}] has no default branch to compare against.")

    base = git_output(cwd, "merge-base", ref, "HEAD")
    if not base:
        raise not_opened(f"[{cwd}] has no merge base with {ref}.")

    head = git_output(cwd, "rev-parse", "HEAD")
    if head == base and not working_tree_is_dirty(cwd):
        raise not_opened(f"[{cwd}] has no changes since {ref}.")
    return base


def review_target() -> tuple[str, str]:
    """Read the invoking pane out of Herdr's context and validate its checkout."""
    context = read_context()
    pane_id = required_context_value(context, "focused_pane_id", "focused pane")
    cwd = required_context_value(context, "focused_pane_cwd", "focused pane cwd")
    require_git_repository(cwd)
    return pane_id, cwd


def open_review(entrypoint: str, pane_id: str, cwd: str, *env: str) -> int:
    """Open a review pane while hiding Herdr's layout and context plumbing."""
    argv = [
        herdr_bin(),
        "plugin",
        "pane",
        "open",
        "--plugin",
        PLUGIN_ID,
        "--entrypoint",
        entrypoint,
        "--target-pane",
        pane_id,
        "--cwd",
        cwd,
    ]
    for setting in env:
        argv += ["--env", setting]

    result = run(argv, capture_output=True, text=True)
    if result.returncode != 0:
        raise PluginError(f"could not open Hunk pane: {diagnostic(result)}")
    return 0


def review_live_changes() -> int:
    pane_id, cwd = review_target()
    return open_review(LIVE_REVIEW_ENTRYPOINT, pane_id, cwd)


def review_last_commit() -> int:
    pane_id, cwd = review_target()
    return open_review(LAST_COMMIT_ENTRYPOINT, pane_id, cwd)


def review_branch_changes() -> int:
    pane_id, cwd = review_target()
    base = branch_review_base(cwd)
    return open_review(
        BRANCH_REVIEW_ENTRYPOINT, pane_id, cwd, f"{REVIEW_BASE_ENV}={base}"
    )


def run_review(hunk_args: list[str]) -> int:
    # Herdr removes a plugin pane when its initial process exits. Let this
    # wrapper end naturally with Hunk instead of explicitly closing the pane;
    # an explicit close races with the runtime's PaneDied event.
    return run(["hunk", *hunk_args]).returncode


def run_branch_review() -> int:
    # The action resolves the base and passes it in the pane's environment, so
    # a missing value here can only mean a mis-wired invocation.
    base = text(os.environ.get(REVIEW_BASE_ENV))
    if not base:
        raise PluginError(f"{REVIEW_BASE_ENV} is not set")
    return run_review(["diff", base, "--watch"])


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(USAGE, file=sys.stderr)
        return 2
    try:
        if argv[0] == "review-live-changes":
            return review_live_changes()
        if argv[0] == "review-last-commit":
            return review_last_commit()
        if argv[0] == "review-branch-changes":
            return review_branch_changes()
        if argv[0] == "run-live-changes-review":
            return run_review(["diff", "--watch"])
        if argv[0] == "run-last-commit-review":
            return run_review(["show"])
        if argv[0] == "run-branch-changes-review":
            return run_branch_review()
    except PluginError as error:
        print(f"herdr-hunk: {error}", file=sys.stderr)
        return 1
    print(f"herdr-hunk: unknown command {argv[0]!r}\n{USAGE}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
