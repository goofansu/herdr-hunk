# herdr-hunk

Opens a Hunk pane for uncommitted changes, the last commit, or everything this branch changed, and closes it when you quit Hunk.

## Requirements

- [Hunk](https://github.com/modem-dev/hunk)

## Install

```shell
herdr plugin install goofansu/herdr-hunk
```

## Usage

Bind each review workflow independently:

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
