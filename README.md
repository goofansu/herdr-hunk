# Hunk Review — a Herdr plugin

Review the changes an agent made, in Hunk, beside the pane that made them. When
an agent commits its work, `hunk diff` in that worktree shows nothing; this
plugin resolves the base to compare against so committed and uncommitted work
appear together, reuses one review pane per checkout, and sends your review notes
back to the agent that wrote the code.

## Requirements

Herdr 0.8.0+, Hunk, Git, and Python 3.9+ on the Herdr server's `PATH`. Nothing is
bundled and there is no build step.

```bash
npm install -g hunkdiff
```

A Hunk installed *after* the Herdr server started is not visible to plugin
commands until the server restarts.

## Install

```bash
herdr plugin install goofansu/herdr-hunk
herdr plugin action list --plugin herdr-hunk
```

To work on the plugin instead, link a local copy — edits take effect on the next
invocation, with no rebuild:

```bash
herdr plugin link /path/to/herdr-hunk
```

## Usage

| Action | What it does |
| --- | --- |
| `herdr-hunk.review` | Open or reuse a Hunk review of this checkout, including committed work. |
| `herdr-hunk.review-commit` | The same pane, targeting the most recent commit. |
| `herdr-hunk.send-comments` | Stage this checkout's Hunk review notes in the agent pane that wrote the code. |

Bind the actions in `~/.config/herdr/config.toml`:

```toml
[[keys.command]]
key = "prefix+d"
type = "plugin_action"
command = "herdr-hunk.review"
description = "review changes in Hunk"

[[keys.command]]
key = "prefix+ctrl+d"
type = "plugin_action"
command = "herdr-hunk.review-commit"
description = "review last commit in Hunk"

[[keys.command]]
key = "prefix+ctrl+r"
type = "plugin_action"
command = "herdr-hunk.send-comments"
description = "send Hunk review notes to the agent"
```

Then `herdr server reload-config`.

## Development

```bash
python3 -m unittest discover -s tests -t .
uvx ruff@0.16.2 check .        # the version CI pins
uvx ruff@0.16.2 format --check .
```

Tests drive the script's argv interface as a subprocess against fake `herdr`,
`hunk`, and `git` executables on `PATH` that record their argv and reply from a
scripted rule table. Interactive Hunk is never launched by the test suite.
