# herdr-hunk

Opens a Hunk pane that watches for changes and closes when you quit Hunk.

## Requirements

- [Hunk](https://github.com/modem-dev/hunk)

## Install

```shell
herdr plugin install goofansu/herdr-hunk
```

## Usage

```markdown
[[keys.command]]
key = "prefix+ctrl+r"
type = "plugin_action"
command = "herdr-hunk.live-review"
description = "review changes live in Hunk"
```
