# herdr-hunk

Provides quick Herdr review actions that open a temporary Hunk overlay. Quitting Hunk closes the overlay and restores your workspace.

## Requirements

- [Hunk](https://github.com/modem-dev/hunk)

## Install

```shell
herdr plugin install goofansu/herdr-hunk
```

## Actions

| Action | Review scope | Hunk command |
| --- | --- | --- |
| `review-uncommitted-changes` | Staged, unstaged, and untracked changes | `hunk diff HEAD --sidebar` |
| `review-last-commit` | Changes introduced by the latest commit | `hunk show --sidebar` |
| `review-branch-changes` | Committed and uncommitted changes since the branch diverged from the default branch | `hunk diff <merge-base> --sidebar` |

The branch review uses the merge-base with `origin/HEAD`, falling back to local `main` or `master` when the remote default branch is unavailable.

Each action validates that the focused directory is a Git repository with at least one commit before opening an overlay. Errors use the title `Hunk review failed` and the body format `[working-directory] reason`.

## Usage

Bind each review action independently:

```toml
[[keys.command]]
key = "prefix+ctrl+r"
type = "plugin_action"
command = "herdr-hunk.review-uncommitted-changes"
description = "review uncommitted changes in Hunk"

[[keys.command]]
key = "prefix+alt+r"
type = "plugin_action"
command = "herdr-hunk.review-branch-changes"
description = "review branch changes in Hunk"

[[keys.command]]
key = "prefix+alt+d"
type = "plugin_action"
command = "herdr-hunk.review-last-commit"
description = "review last commit in Hunk"
```
