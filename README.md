# Hunk Review — a Herdr plugin

Herdr knows which agent produced which change, in which checkout, and whether it
has finished. Hunk knows how to render and annotate a changeset. This plugin
connects them.

The case that matters: when an agent **commits** its work — the normal end state
for an agent on a branch in a worktree — `hunk diff` in that checkout renders
`0 files` and `No files match the current filter`. The reviewer sees an empty
screen and concludes the agent did nothing. Reviewing the real changeset needs
the base to compare against, which is Herdr's knowledge. This plugin supplies it.

## Actions

| Action | What it does |
| --- | --- |
| `herdr-hunk.review` | Resolve the invoking pane's checkout and its correct review target, then open or reuse a single Hunk review pane to its right. |
| `herdr-hunk.review-commit` | The same, targeting the most recent commit via `hunk show`. |
| `herdr-hunk.send-comments` | Collect your review notes from the live Hunk session and stage them into the agent pane that produced the code. |

`review` and `review-commit` share one review pane per checkout, and each
checkout — including each linked worktree of one repository — gets its own.

## Requirements

Herdr 0.8.0+, Hunk, Git, and Python 3 on the Herdr server's `PATH`. Nothing is
bundled and there is no build step.

```bash
npm install -g hunkdiff
```

A Hunk installed *after* the Herdr server started is not visible to plugin
commands until the server restarts.

## Install

```bash
herdr plugin link /path/to/herdr-hunk
herdr plugin action list --plugin herdr-hunk
```

Bind the actions in `~/.config/herdr/config.toml`:

```toml
[[keys.command]]
key = "prefix+r"
type = "plugin_action"
command = "herdr-hunk.review"
description = "review changes in Hunk"

[[keys.command]]
key = "prefix+R"
type = "plugin_action"
command = "herdr-hunk.review-commit"
description = "review last commit in Hunk"

[[keys.command]]
key = "prefix+C"
type = "plugin_action"
command = "herdr-hunk.send-comments"
description = "send Hunk review notes to the agent"
```

Then `herdr server reload-config`.

## How it resolves the review target

The invoking pane's `cwd` (falling back to the workspace `cwd`) is resolved to a
checkout root with `git rev-parse --show-toplevel`, so invoking from a nested
directory still works. That root is the identity key throughout — it is also what
`hunk session --repo` keys on, so linked worktrees never collide.

- **In a Herdr worktree workspace**, the base is
  `git merge-base <parent repo HEAD> HEAD` evaluated in the checkout, and the
  target is `hunk diff <base>` — committed *and* uncommitted work together.
- **Anywhere else**, the target is a plain `hunk diff` working-tree review.
- A worktree whose parent `HEAD` or merge-base cannot be read degrades to a
  working-tree review rather than failing.

Hunk is launched with `--watch` and nothing else: themes, layout mode, line
numbers, and `transparent_background` are documented Hunk config keys, and the
plugin does not override preferences you set deliberately.

## Reuse

Repeated invocation is the normal case for a keybound action, so the plugin keeps
a `checkout -> {review pane, origin pane}` map under `HERDR_PLUGIN_STATE_DIR`.
The origin pane is the one the review was split from, which is what
`send-comments` uses to find the right agent.

A pane the plugin already owns gets reused unless something else has taken it
over. Splitting is the last resort, not the answer to every unhealthy session:

| recorded pane | Hunk session | what happens |
| --- | --- | --- |
| alive | live | reload in place |
| alive | still registering | wait for it, then reload |
| alive | Hunk running, daemon silent | focus it and report — never stack a second Hunk |
| alive | Hunk exited, pane at a shell | relaunch Hunk in that same pane |
| alive | something else running there | split a new pane |
| gone | — | split a new pane |

The row that matters most in practice is *Hunk exited, pane at a shell*. Quitting
Hunk is the normal way to finish a review, and treating that as "unusable" left a
dead pane behind on every cycle — the exact layout decay the reuse rule exists to
prevent.

`--watch` is re-passed on **every** reload. A reload drops watch mode from the
live session, so omitting it would leave a reused pane silently frozen.

## Sending review notes

`send-comments` reads `hunk session comment list --type user --json`, writes the
notes as Markdown under `HERDR_PLUGIN_STATE_DIR/notes/`, and stages a
**single-line** instruction referencing that file with `herdr pane send-text`.

The text is staged, not submitted — you decide when the agent acts. `send-text`
does not append Enter, but embedded newlines *are* delivered as Enter and would
submit line by line, which is why the instruction is one line and points at a
file instead of pasting the notes.

Once staged, the agent pane is focused with `herdr agent focus` — the text is
waiting for you to accept or edit, so the plugin puts you in front of it. A
failure to focus is reported but does not fail the action: the notes are already
staged, and work that happened must not be reported as work that didn't.

### Which agent gets the notes

A workspace can hold several agents. The plugin resolves the target in order:

1. The pane the review was split from — recorded at `review` time, and the
   definition of "the agent that produced this code".
2. Otherwise the invoking pane, if you ran `send-comments` from the agent itself.
3. Otherwise the sole agent working in this checkout, in the tab you invoked
   from. This is the Hunk-opened-by-hand case: you split the review beside the
   agent, so they share a tab.
4. Otherwise the sole agent working in this checkout.
5. Otherwise the sole agent in the workspace.
6. Otherwise the most recently active of the narrowest candidate set — the agent
   that moved last is the one that just finished the work you're reviewing.

Every pane this plugin has opened for Hunk is excluded at each step, across all
checkouts. The focused pane is never *assumed* to be the agent: a worktree
workspace's root pane is an ordinary shell, and the pane you invoke from is
usually the review itself.

Only a workspace with no agent at all is an error. When step 6 has to choose, the
plugin says which agent it picked and which it passed over, as a toast and in the
command log. It does not refuse: the notes are staged unsubmitted in a pane it
then focuses, so a wrong pick is sitting in front of you and one keystroke from
being cleared — whereas refusing strands a review you have already written.

## Development

The plugin directory is the source; edit and re-invoke, no rebuild.

```bash
python3 -m unittest discover -s tests -t .
ruff check .
```

Tests drive the script's argv interface as a subprocess against fake `herdr`,
`hunk`, and `git` executables on `PATH` that record their argv and reply from a
scripted rule table. Interactive Hunk is never launched by the test suite.
