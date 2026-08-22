# herdr-hunk

Provides quick Herdr review actions that open a temporary Hunk overlay. Quitting Hunk closes the overlay and restores your workspace.

## Requirements

- [Hunk](https://github.com/modem-dev/hunk)
- [GitHub CLI](https://cli.github.com/) authenticated with `gh auth login` for pull request reviews

## Install

```shell
herdr plugin install goofansu/herdr-hunk
```

## Actions

| Action | Review scope | Hunk command |
| --- | --- | --- |
| `review-uncommitted-changes` | Staged, unstaged, and untracked changes | `hunk diff HEAD --watch` |
| `review-branch-changes` | Committed and uncommitted changes since the branch diverged from the default branch | `hunk diff <merge-base> --watch` |
| `review-pull-request` | Changes published in the current branch's GitHub pull request | `hunk patch <pull-request.diff>` |

The pull-request review finds the current branch's pull request before opening the overlay. The overlay displays `Loading pull request #123…` while `gh pr diff` streams the patch to a temporary file, then opens it in Hunk.

Uncommitted and branch reviews watch the worktree and reload as it changes. The branch review uses the merge-base with `origin/HEAD`, falling back to local `main` or `master` when the remote default branch is unavailable.

Each action validates that the focused directory is a Git repository with at least one commit before opening an overlay. Errors use the title `Hunk review failed` and the body format `[working-directory] reason`.

## Usage

Bind each review action independently:

```toml
[[keys.command]]
key = "prefix+r"
type = "plugin_action"
command = "herdr-hunk.review-uncommitted-changes"
description = "review uncommitted changes in Hunk"

[[keys.command]]
key = "prefix+ctrl+r"
type = "plugin_action"
command = "herdr-hunk.review-branch-changes"
description = "review branch changes in Hunk"

[[keys.command]]
key = "prefix+alt+r"
type = "plugin_action"
command = "herdr-hunk.review-pull-request"
description = "review pull request in Hunk"
```
