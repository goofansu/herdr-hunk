# Current implementation reference

This document is the starting point for agents changing `herdr-hunk`. It records
the implementation as of 2026-08-13 so that routine work does not require
reconstructing the plugin from source and tests. Source remains authoritative if
it diverges from this document.

## Purpose and boundaries

`herdr-hunk` connects three existing command-line tools:

- **Herdr** owns workspaces, panes, focus, plugin invocation context, and plugin
  state storage.
- **Hunk** renders a review and exposes its live session and user comments through
  its CLI.
- **Git** resolves the checkout and, for linked worktrees, the changeset base.

### Product goal versus current behavior

The product goal, clarified by the maintainer on 2026-08-13, is:

> A Hunk pane is the agent's copilot in the current tab.

In normal use, invoking review from an agent should therefore open or reuse that
agent's Hunk in the same tab, and comments from a plugin-managed Hunk should
return deterministically to its paired agent. Repeated invocation should not
multiply panes unnecessarily, and one agent must never silently take over
another agent's Hunk or comments.

This is a user-facing relationship, not a demand for rigid layout ownership. A
user may move a Hunk pane deliberately; the plugin should respect that rather
than automatically moving it back. Review from an ordinary shell remains useful
when exactly one agent is in the current tab. Pairing only needs to follow the
current agent occupying its pane; it need not survive replacement by another
agent process.

The current implementation only establishes the desired relationship on first
open. Its persisted identity is the checkout path, so later invocations can
reuse the same Hunk from another tab or workspace and repoint its remembered
origin to another agent. Treat that as an implementation gap, not the desired
product model.

The plugin is one standard-library Python script, `herdr_hunk.py`. Herdr runs it
directly from the plugin directory; there is no package, daemon, build artifact,
or install step owned by this repository. `herdr-plugin.toml` declares three
pane-context actions and requires Herdr 0.8.0 or newer:

| Action | Entry point | Result |
| --- | --- | --- |
| `review-changes` | `action_review("review-changes")` | Opens or retargets one watched Hunk diff for the checkout. |
| `review-commit` | `action_review("review-commit")` | Opens or retargets the same pane to Hunk's default `show` target (the last commit). |
| `send-comments` | `action_send_comments()` | Writes user comments to Markdown and stages one instruction in the selected agent pane. |

All subprocess calls use argv lists and captured text output. Expected operational
failures become `PluginError`, print one `herdr-hunk: ...` diagnostic to stderr,
and exit 1. Invalid invocation syntax exits 2.

## Inputs and persistent state

The plugin reads these environment variables:

- `HERDR_PLUGIN_CONTEXT_JSON` (required): invocation context. The implementation
  uses `focused_pane_id`, `focused_pane_cwd`, `focused_pane_agent`,
  `workspace_id`, `workspace_cwd`, `tab_id`, and the optional `worktree` object.
- `HERDR_PLUGIN_STATE_DIR` (required): directory for the pane map and rendered
  review notes.
- `HERDR_BIN_PATH` (optional): Herdr executable; otherwise `herdr` is resolved on
  `PATH`.

It deliberately does not fall back to legacy ambient pane/workspace variables.
The focused pane's cwd is preferred over the workspace cwd. Git
`rev-parse --show-toplevel` turns that directory into the canonical checkout used
for target calculation, Hunk session lookup, and state keys.

State under `HERDR_PLUGIN_STATE_DIR` is:

```text
review-panes.json
notes/
  <checkout-basename>-<first-12-sha256-of-checkout>.md
```

`review-panes.json` maps each checkout path to:

```json
{
  "/path/to/checkout": {
    "review_pane": "workspace:pane",
    "origin_pane": "workspace:agent-pane"
  }
}
```

`review_pane` is the pane created for Hunk. `origin_pane` is optional and is the
latest agent pane that invoked a review action for that checkout. A legacy value
where the record is just a pane-id string is accepted on read. Missing, corrupt,
or wrongly shaped map data is treated as empty. Writes replace the JSON file
directly; there is no locking or atomic rename.

## Review target selection

Both review actions first require `hunk` on `PATH`, read the pane context, and
resolve the checkout.

`review-commit` always chooses:

```text
hunk show --watch
```

`review-changes` chooses one of two targets:

1. In a linked worktree whose `worktree.repo_root` differs from the resolved
   checkout, it reads the parent checkout's current `HEAD`, computes that
   commit's merge base with the linked checkout's `HEAD`, and runs
   `hunk diff <merge-base> --watch`. This includes committed and uncommitted work
   in the agent worktree.
2. Without that context, or if either Git lookup fails, it degrades to
   `hunk diff --watch`, which reviews only the checkout's working-tree changes.

The fallback is intentionally successful and silent. Consequently, incomplete
worktree metadata or a failed merge-base calculation can produce an apparently
valid but incomplete review.

## Review-pane lifecycle

There is at most one plugin-owned review pane per resolved checkout in the state
map. `review-changes` and `review-commit` share it.

```text
no recorded/live pane
  -> split invoking pane right, cwd=checkout, focus new pane
  -> run Hunk with --watch
  -> remember review pane and optional agent origin

recorded pane + live Hunk session
  -> hunk session reload --repo <checkout> -- <target> --watch
  -> refresh origin when invoked by an agent
  -> focus the pane's workspace

recorded pane + no session + idle shell
  -> run Hunk again in the existing pane

recorded pane + Hunk process + no session
  -> poll session registration every 250 ms for at most 1.5 s
  -> reload if it appears
  -> otherwise focus the review workspace and fail without another split

recorded pane + another foreground process
  -> split a replacement review pane and overwrite the checkout's map entry

recorded pane no longer known to Herdr
  -> split a replacement review pane and overwrite the map entry
```

The split command is `herdr pane split <invoking-pane> --direction right
--cwd <checkout> --focus`. Hunk starts through `herdr pane run`; shell words are
quoted before being passed to Herdr. If launch fails, the newly split pane is
closed before the error is surfaced.

Reload always restores `--watch` because Hunk reload drops watch mode. A reused
review is surfaced with `herdr workspace focus`, using the pane's current
workspace rather than assuming it stayed beside the invoking agent. There is no
directional pane-focus CLI call in this flow.

The origin is updated only when `focused_pane_agent` is a non-empty string. An
invocation from Hunk or an ordinary shell preserves the previous origin rather
than misattributing authorship.

## Sending comments

`send-comments` is a staged handoff, not an automatic agent submission:

1. Resolve the invoking checkout and workspace.
2. Run `hunk session comment list --repo <checkout> --type user --json`.
3. Reject a dead session, unreadable response, or empty comment list.
4. Render all returned comment objects to a checkout-specific Markdown file.
   Locations prefer the first line of `newRange`, then `oldRange`, then the
   one-based hunk number, then the file alone. Optional titles become bold text.
5. List panes in the invoking workspace, retain panes with a truthy `agent`
   field, and exclude every review pane recorded in plugin state.
6. Select an agent using the routing order below.
7. Stage one newline-free instruction with `herdr pane send-text`; do not send
   Enter or invoke `pane run`.
8. Best-effort focus the agent with `herdr agent focus` so the user can inspect
   and submit the staged instruction.

The agent routing precedence is:

1. recorded `origin_pane`, if it is still one of the eligible agents;
2. invoking pane, if it is an eligible agent;
3. the sole eligible agent in the same checkout and invoking tab;
4. the sole eligible agent in the same checkout;
5. the sole eligible agent in the workspace;
6. among the narrowest non-empty group above, the pane with the greatest
   `state_change_seq` from `herdr agent list` (listing order breaks missing/tied
   sequence data).

For the final ambiguous case, the plugin still stages the notes, prints the
choice, and best-effort shows a no-sound Herdr notification naming the selected
and passed-over panes. It considers visible, unsubmitted staging safer than
discarding comments because authorship is uncertain.

The notes file is overwritten on every send for the same checkout. Comments are
not cleared from Hunk, so invoking the action again stages the full current set
again.

## Important invariants and limitations

- Pane reuse is keyed by checkout, not by workspace, tab, agent, or review type.
  This conflicts with the intended agent–Hunk pairing. One checkout cannot retain
  separate copilot panes for agents in different tabs through this plugin.
- A review pane may move to another workspace; reuse follows and focuses it there.
- State is plugin-global, while comment agent discovery is restricted to the
  invoking workspace. A remembered origin in another workspace is not eligible.
- Any live Hunk session for the checkout is assumed to correspond to the
  recorded pane. Hunk session lookup is repo-based, not pane-based.
- A review pane taken over by another command is no longer excluded once its map
  entry is overwritten by a replacement. The plugin has no pane ownership marker
  beyond the current state map.
- `send-comments` accepts every object in Hunk's returned `comments` array; it
  trusts `--type user` to perform source filtering.
- Failure to focus after a successful reload or comment staging does not undo or
  report the completed operation as failed.
- Hunk is checked before review layout mutation. Git and Hunk launch/reload
  failures are surfaced; linked-worktree target lookup failures are not.
- There is no concurrency control around repeated keypresses or state writes.
  The Hunk registration poll reduces duplicate splits after launch but does not
  serialize truly simultaneous plugin processes.

## Tests and verification seams

The test suite runs the real script as a subprocess against fake `herdr`, `hunk`,
and `git` executables. Rules match ordered argv subsequences, return scripted
JSON/errors, and record every call. This verifies the plugin's process boundary
without opening an interactive terminal UI.

Coverage is organized as follows:

- `tests/test_context.py`: invocation context and executable selection.
- `tests/test_target.py`: checkout and changeset target resolution.
- `tests/test_review.py`: open, reuse, reload, relaunch, origin updates, startup
  races, and pane-count stability.
- `tests/test_send_comments.py`: note rendering, agent routing, staging, focus,
  ambiguity, and empty/dead review behavior.
- `tests/test_failures.py`: missing tools, malformed responses, CLI failures, and
  layout cleanup.

Run the repository checks with:

```bash
python3 -m unittest discover -s tests -t .
ruff check .
ruff format --check .
```

At the time of this research, all 81 tests pass. The orb has Hunk 0.18.1; its
live `--help` output confirms the `session get`, `session reload`, and
`session comment list` forms used here. Herdr is not installed in the orb, so
the Herdr behavior is contract-tested through fakes rather than validated in a
live Herdr server.

When changing a CLI boundary, inspect the installed command's help and update
the exact-argv tests. When changing pane semantics, add a lifecycle combination
to `test_review.py`. When changing agent selection, add the smallest pane-list
combination that demonstrates the precedence in `test_send_comments.py`.
