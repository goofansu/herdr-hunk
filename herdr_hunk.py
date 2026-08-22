"""Open a self-closing Hunk review overlay from a Herdr action."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

PLUGIN_ID = "herdr-hunk"
UNCOMMITTED_REVIEW_ENTRYPOINT = "uncommitted-review"
PULL_REQUEST_REVIEW_ENTRYPOINT = "pull-request-review"
BRANCH_REVIEW_ENTRYPOINT = "branch-review"
REVIEW_BASE_ENV = "HERDR_HUNK_REVIEW_BASE"
PULL_REQUEST_NUMBER_ENV = "HERDR_HUNK_PR_NUMBER"
PANE_COMMANDS = frozenset(
    {
        "run-uncommitted-changes-review",
        "run-pull-request-review",
        "run-branch-changes-review",
    }
)
NOTIFICATION_TITLE = "Hunk review failed"
REMOTE_HEAD_REF = "refs/remotes/origin/HEAD"
LOCAL_DEFAULT_REFS = ("refs/heads/main", "refs/heads/master")
USAGE = (
    "usage: herdr_hunk.py "
    "(review-uncommitted-changes | review-pull-request | review-branch-changes | "
    "run-uncommitted-changes-review | run-pull-request-review | "
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


def notification_body(cwd: str, reason: str) -> str:
    return f"[{cwd}] {reason}"


def review_failed(reason: str) -> None:
    """Report a failure from inside a review pane, which closes along with it.

    Herdr removes the pane the moment this process exits, taking Hunk's own
    output and our stderr line with it, so the notification is the only part a
    failure leaves behind.
    """
    notify(NOTIFICATION_TITLE, notification_body(os.getcwd(), reason))


def not_opened(cwd: str, reason: str, detail: str | None = None) -> PluginError:
    """Explain a refused review in a notification as well as on stderr."""
    message = notification_body(cwd, reason)
    notify(NOTIFICATION_TITLE, message)
    if detail:
        message = f"{message} {detail}"
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
    raise not_opened(cwd, "is not a Git repository.")


def require_commit(cwd: str) -> None:
    if git_output(cwd, "rev-parse", "--verify", "--quiet", "HEAD"):
        return
    raise not_opened(cwd, "has no commits.")


def default_branch_ref(cwd: str) -> str | None:
    """Prefer the remote's HEAD, then a local main or master branch."""
    ref = git_output(cwd, "symbolic-ref", "--quiet", REMOTE_HEAD_REF)
    if ref:
        return ref
    for candidate in LOCAL_DEFAULT_REFS:
        if git_output(cwd, "rev-parse", "--verify", "--quiet", candidate):
            return candidate
    return None


def branch_review_base(cwd: str) -> str:
    """Resolve the branch's starting commit before any pane opens.

    The base is frozen here as a commit id, so later movement on the default
    branch cannot shift a review that is already open, and a base that cannot
    be resolved stays a notification instead of a pane that dies. How much the
    branch changed is Hunk's to render, including nothing at all.
    """
    ref = default_branch_ref(cwd)
    if not ref:
        raise not_opened(cwd, "has no default branch to compare against.")

    base = git_output(cwd, "merge-base", ref, "HEAD")
    if not base:
        raise not_opened(cwd, f"has no merge base with {ref}.")
    return base


def review_cwd() -> str:
    """Read and validate the invoking pane's checkout from Herdr's context."""
    context = read_context()
    cwd = required_context_value(context, "focused_pane_cwd", "focused pane cwd")
    require_git_repository(cwd)
    require_commit(cwd)
    return cwd


def open_review(entrypoint: str, cwd: str, *env: str) -> int:
    """Open a review overlay while hiding Herdr's context plumbing."""
    argv = [
        herdr_bin(),
        "plugin",
        "pane",
        "open",
        "--plugin",
        PLUGIN_ID,
        "--entrypoint",
        entrypoint,
        "--cwd",
        cwd,
    ]
    for setting in env:
        argv += ["--env", setting]

    result = run(argv, capture_output=True, text=True)
    if result.returncode != 0:
        raise not_opened(cwd, f"could not open Hunk overlay: {diagnostic(result)}")
    return 0


def review_uncommitted_changes() -> int:
    return open_review(UNCOMMITTED_REVIEW_ENTRYPOINT, review_cwd())


def pull_request_number(cwd: str) -> str:
    try:
        result = run(
            ["gh", "pr", "view", "--json", "number", "--jq", ".number"],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
    except PluginError as error:
        raise not_opened(cwd, str(error)) from error
    if result.returncode != 0:
        raise not_opened(
            cwd,
            "pull request lookup failed.",
            diagnostic(result),
        )
    number = text(result.stdout.strip())
    if not number or not number.isdecimal():
        raise not_opened(cwd, "GitHub returned an invalid pull request number.")
    return number


def review_pull_request() -> int:
    cwd = review_cwd()
    number = pull_request_number(cwd)
    return open_review(
        PULL_REQUEST_REVIEW_ENTRYPOINT,
        cwd,
        f"{PULL_REQUEST_NUMBER_ENV}={number}",
    )


def review_branch_changes() -> int:
    cwd = review_cwd()
    base = branch_review_base(cwd)
    return open_review(BRANCH_REVIEW_ENTRYPOINT, cwd, f"{REVIEW_BASE_ENV}={base}")


def run_review(hunk_args: list[str]) -> int:
    # Herdr removes a plugin pane when its initial process exits. Let this
    # wrapper end naturally with Hunk instead of explicitly closing the pane;
    # an explicit close races with the runtime's PaneDied event.
    status = run(["hunk", *hunk_args]).returncode
    if status < 0:
        # A signal ended Hunk, which is how a pane the reviewer closed
        # themselves looks, so it is not a failure to report. Hand back the
        # status a shell would report instead of a negative one, which would
        # wrap into an unrelated exit code.
        return 128 - status
    if status > 0:
        review_failed(f"Hunk exited with status {status}.")
    return status


def run_pull_request_review() -> int:
    number = text(os.environ.get(PULL_REQUEST_NUMBER_ENV))
    if not number:
        raise PluginError(f"{PULL_REQUEST_NUMBER_ENV} is not set")
    if not number.isdecimal():
        raise PluginError(f"{PULL_REQUEST_NUMBER_ENV} is not a pull request number")

    print(f"Loading pull request #{number}\N{HORIZONTAL ELLIPSIS}", flush=True)
    with tempfile.TemporaryDirectory(prefix="herdr-hunk-") as directory:
        patch = os.path.join(directory, f"pull-request-{number}.diff")
        with open(patch, "w", encoding="utf-8") as output:
            result = run(
                ["gh", "pr", "diff", number, "--color=never"],
                stdout=output,
                stderr=subprocess.PIPE,
                text=True,
            )
        if result.returncode != 0:
            review_failed(
                f"could not load pull request #{number}: {diagnostic(result)}"
            )
            return result.returncode
        return run_review(["patch", patch])


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
        if argv[0] == "review-uncommitted-changes":
            return review_uncommitted_changes()
        if argv[0] == "review-pull-request":
            return review_pull_request()
        if argv[0] == "review-branch-changes":
            return review_branch_changes()
        if argv[0] == "run-uncommitted-changes-review":
            return run_review(["diff", "HEAD", "--watch"])
        if argv[0] == "run-pull-request-review":
            return run_pull_request_review()
        if argv[0] == "run-branch-changes-review":
            return run_branch_review()
    except PluginError as error:
        if argv[0] in PANE_COMMANDS:
            review_failed(str(error))
        print(f"herdr-hunk: {error}", file=sys.stderr)
        return 1
    print(f"herdr-hunk: unknown command {argv[0]!r}\n{USAGE}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
