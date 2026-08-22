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

| Action | Use when | Hunk command |
| --- | --- | --- |
| `review-uncommitted-changes` | Checking staged, unstaged, and untracked edits before committing | `hunk diff HEAD --watch` |
| `review-branch-changes` | Reviewing the complete local branch before publishing or merging | `hunk diff <merge-base> --watch` |
| `review-pull-request` | Checking the exact patch currently published for GitHub reviewers | `hunk patch <pull-request.diff>` |

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

## Review behavior

Uncommitted and branch reviews include local work and reload as the worktree changes. A branch review starts where the branch diverged from `origin/HEAD`, falling back to local `main` or `master` when the remote default branch is unavailable.

A pull-request review finds the current branch's GitHub pull request and streams its patch into a temporary file while the overlay displays `Loading pull request #123…`. Hunk opens that file after the download completes; the file remains available for the review, then is deleted when Hunk exits (or when loading fails). The snapshot excludes local uncommitted and unpushed changes and does not auto-refresh.

Each action validates that the focused directory is a Git repository with at least one commit before opening an overlay. Errors use the title `Hunk review failed` and the body format `[working-directory] reason`.
