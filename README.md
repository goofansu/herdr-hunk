# herdr-hunk

Herdr action `herdr-hunk.review-changes` opens `hunk diff` in a new right pane
when the current tab has exactly one pane. Quitting Hunk closes the review pane.

Herdr configuration cannot provide this behavior by itself: a `type = "pane"`
command closes on exit, but it is always a temporary zoomed pane and keybindings
cannot test the current tab's pane count. This plugin supplies the condition and
the persistent right split, then explicitly closes that split.

Requires Herdr 0.8.0+, Hunk, and Python 3.9+.

```bash
herdr plugin install goofansu/herdr-hunk
```

Bind the action in `~/.config/herdr/config.toml`:

```toml
[[keys.command]]
key = "prefix+d"
type = "plugin_action"
command = "herdr-hunk.review-changes"
description = "review changes in Hunk"
```

Then run `herdr server reload-config`.

## Development

```bash
python3 -m unittest discover -s tests -t .
ruff check .
ruff format --check .
```
