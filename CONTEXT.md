# Hunk Review

This context describes how Hunk review sessions are presented within Herdr.

## Language

**Review action**:
A Herdr command that starts one of the supported Hunk review modes for the focused Git worktree.
_Avoid_: Hunk action

**Review overlay**:
A temporary, full-size Hunk review surface that preserves the underlying workspace layout and restores it when the review ends.
_Avoid_: Split review pane, zoomed review pane

**Uncommitted review**:
A review of staged, unstaged, and untracked changes in the focused Git worktree.
_Avoid_: Working-tree review

**Last-commit review**:
A review of the changes introduced by the focused Git worktree's latest commit.

**Branch review**:
A review of committed and uncommitted changes since the current branch diverged from the default branch.
