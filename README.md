# herdr-hunk

Opens a Hunk pane for live changes or the last commit and closes it when you quit Hunk.

## Requirements

- [Hunk](https://github.com/modem-dev/hunk)

## Install

```shell
herdr plugin install goofansu/herdr-hunk
```

## Usage

Bind either review workflow independently:

```toml
[[keys.command]]
key = "prefix+ctrl+r"
type = "plugin_action"
command = "herdr-hunk.review-live-changes"
description = "review live changes in Hunk"

[[keys.command]]
key = "prefix+ctrl+s"
type = "plugin_action"
command = "herdr-hunk.review-last-commit"
description = "review the last commit in Hunk"
```
