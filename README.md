# herdr-hunk

Give each coding agent an on-demand Hunk copilot for reviewing its changes and
sending notes back to that same agent.

## Requirements

The plugin requires Herdr 0.8.0+, Hunk, Git, and Python 3.9+ on the Herdr
server's `PATH`. Nothing is bundled, and there is no build step.

```bash
npm install -g hunkdiff
```

If Hunk is installed *after* the Herdr server starts, it is not visible to
plugin commands until the server restarts.

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
| `herdr-hunk.review-changes` | Open or reuse this agent's watched `hunk diff`, including committed work. |
| `herdr-hunk.review-commit` | Reuse this agent's Hunk for the most recent commit. |
| `herdr-hunk.send-comments` | Stage this Hunk's review notes in its paired agent. |

Bind the actions in `~/.config/herdr/config.toml`:

```toml
[[keys.command]]
key = "prefix+d"
type = "plugin_action"
command = "herdr-hunk.review-changes"
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

Each agent gets a separate Hunk even when several agents use the same checkout.
Repeated review actions reload only that pair's exact Hunk session. You may move
the Hunk to another tab or workspace: the plugin leaves it there, focuses it on
reuse, and continues routing comments to its original agent. If the paired agent
exits or is replaced, invoke a review action from the current agent to build a
new pair.

Review actions also work from an ordinary shell when exactly one agent is in the
current tab. With several agents, invoke from the intended agent instead of the
shell; the plugin deliberately refuses to guess. Manually opened Hunk sessions
remain usable, but when several sessions show the same checkout, invoke
`send-comments` from the intended Hunk so its process identifies the session.

`send-comments` writes a Markdown file that labels every note with its Hunk
target, then stages one non-submitting instruction in the paired agent. Review
targets can be switched freely; notes that existed before a switch retain their
previous target label. Pressing `send-comments` again with identical notes only
focuses the already targeted agent instead of appending duplicate text.

### Narrow review panes

The plugin keeps Hunk's responsive `auto` layout and your rendering settings.
For a half-width pane, use Hunk's built-in controls:

- `w` toggles line wrapping;
- `0`, `1`, and `2` select auto, split, and stacked layouts;
- `s` hides the sidebar;
- Left/Right scroll code horizontally (add Shift for faster scrolling).

Herdr's default `Ctrl+B`, then `z` temporarily zooms the focused Hunk pane to the
full tab. The plugin does not force wrapping, move a Hunk back beside its agent,
or impose a wider layout.

## Development

```bash
python3 -m unittest discover -s tests -t .
uvx ruff@0.16.2 check .        # the version CI pins
uvx ruff@0.16.2 format --check .
```

Tests drive the script's argv interface as a subprocess against fake `herdr`,
`hunk`, and `git` executables on `PATH` that record their argv and reply from a
scripted rule table. Interactive Hunk is never launched by the test suite.
