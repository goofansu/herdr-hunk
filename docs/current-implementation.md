# Current implementation reference

This document records the implementation after applying
`docs/pragmatic-pane-evaluation.md` on 2026-08-13. Source remains authoritative
if it diverges from this reference.

## Product model and boundaries

`herdr-hunk` connects three command-line tools:

- **Herdr** owns invocation context, agent identity, terminal panes, focus, and
  plugin state directories.
- **Hunk** renders reviews and exposes live sessions and user comments.
- **Git** resolves the checkout and linked-worktree comparison base.

The durable unit is an **agent–Hunk copilot pair**. One checkout may have many
pairs. A second agent never retargets another agent's Hunk, and comments from a
known Hunk never use workspace-wide lifecycle recency to choose an agent.

The repository remains one standard-library Python script with no build step.
`herdr-plugin.toml` declares three pane-context actions and one managed pane
entrypoint:

| Manifest entry | Python entry | Result |
| --- | --- | --- |
| `review-changes` action | `action_review("review-changes")` | Open or reload this agent's watched Hunk diff. |
| `review-commit` action | `action_review("review-commit")` | Reload the same pair to Hunk's default `show` target. |
| `send-comments` action | `action_send_comments()` | Render notes and stage an instruction in the paired agent. |
| `review` pane | `$HERDR_PLUGIN_ROOT/herdr_hunk.py run-hunk` | Validate the passed target and replace itself with watched Hunk. |

Operational failures become `PluginError`, print one `herdr-hunk: ...`
diagnostic to stderr, and exit 1. Invalid invocation syntax exits 2.

## Inputs and state

The action entrypoints read:

- `HERDR_PLUGIN_CONTEXT_JSON`: focused pane, pane/agent/cwd, workspace, tab, and
  optional worktree metadata;
- `HERDR_PLUGIN_STATE_DIR`: the pair map and generated review-note files;
- `HERDR_BIN_PATH`: the running Herdr binary, falling back to `herdr`.

The internal pane entrypoint additionally reads `HERDR_HUNK_TARGET_JSON`, set by
the action that opens it. Only non-empty `diff` and `show` argv arrays are
accepted before `execvp` starts `hunk ... --watch`.

State has this shape:

```text
review-panes.json
notes/
  <checkout-basename>-<pair-digest>.md
```

```json
{
  "/path/to/checkout": [
    {
      "origin_pane": "workspace:agent-pane",
      "origin_terminal_id": "agent-terminal",
      "origin_agent_session": {
        "source": "herdr:agent",
        "agent": "agent",
        "kind": "id",
        "value": "optional native session identity"
      },
      "review_pane": "workspace:hunk-pane",
      "review_terminal_id": "hunk-terminal",
      "plugin_pane": true,
      "session_id": "exact Hunk session UUID",
      "target": ["diff", "merge-base"],
      "note_targets": {"note-id": ["diff", "merge-base"]},
      "last_stage": {
        "digest": "notes-and-instruction digest",
        "agent_terminal_id": "agent-terminal"
      }
    }
  ]
}
```

Terminal and native agent-session identity distinguish a live pair from a new
occupant that happens to reuse the same public pane ID. Pane IDs are refreshed
from `pane get` after moves. Old IDs still work because Herdr retains moved-pane
aliases. State writes use a flushed temporary file and atomic `os.replace`.

The old checkout-global formats—a pane-ID string or one record object—remain
readable and are normalized to a one-item list. A legacy record with a known
origin can be reused, with tab-level focus as its fallback. An origin-less
legacy Hunk is retained but not silently assigned to whichever agent invokes
next.

## Agent selection and pair identity

Review invoked from an agent uses that exact pane. Herdr `pane get` supplies the
terminal and optional native agent-session identity stored with the pair.

Review invoked from an ordinary shell is allowed only when `pane list` reports
exactly one non-Hunk agent in the current tab. Zero or multiple agents produce
an actionable error. No `agent list` recency tie-break exists.

Review or comment actions invoked from a recorded Hunk resolve the pair by its
review terminal. This works when the user moved the Hunk across tabs or
workspaces and when the process still reports its launch-time pane alias. The
paired agent is then resolved globally through `pane get`; it need not share the
Hunk's current workspace.

If the paired agent exits, loses agent identity, changes terminal, or reports a
different native agent session, the relationship is stale. It is never handed
to another agent. Invoking review from a current replacement agent discards the
old occupant's record and creates a new pair on demand.

## Review targets

`review-commit` chooses `show`.

`review-changes` chooses:

1. `diff <merge-base>` for a linked worktree whose parent repo root differs from
   the checkout; the base is the merge base of the parent checkout's current
   `HEAD` and the agent checkout's `HEAD`;
2. plain `diff` outside that case or when either Git lookup fails.

The silent fallback preserves the existing behavior but can review less than
expected when worktree metadata or Git lookup is incomplete.

## Managed pane and session lifecycle

New Hunk copilots are opened with:

```text
herdr plugin pane open
  --plugin herdr-hunk
  --entrypoint review
  --placement split
  --target-pane <agent-pane>
  --direction right
  --cwd <checkout>
  --env HERDR_HUNK_TARGET_JSON=<target-json>
  --focus
```

Because `--cwd` places the managed process in the reviewed checkout, the manifest
uses Herdr's protected `HERDR_PLUGIN_ROOT` variable to locate the Python script.
The action lists Hunk sessions before opening, then polls for at most 1.5 seconds.
It identifies the new session by the Hunk PID in the pane's foreground process
tree when available, otherwise by the one session ID that appeared after launch.
A pre-existing manual session therefore does not get adopted and does not
prevent the plugin session from being recorded.

Reuse follows this state machine:

```text
same agent + live pane + exact session
  -> capture old target for existing notes when target changes
  -> hunk session reload <session-id> -- <target> --watch
  -> focus exact managed pane

same agent + moved managed pane
  -> retain its current location
  -> focus exact pane through plugin pane ownership
  -> report workspace/tab/pane location when it is no longer beside the agent

legacy pane + live session
  -> reload exact/adopted session
  -> focus the pane's current tab

legacy pane + idle shell
  -> relaunch Hunk in place and record the resulting session ID

Hunk process still starting + no registered session
  -> poll without opening another pane

missing/replaced Hunk pane or unrelated foreground command
  -> open a replacement for the same agent
```

Every reload explicitly restores `--watch`, which Hunk reload otherwise drops.
Every session command for a known pair uses its session ID, never `--repo`, so
multiple pairs and manual sessions may share one checkout safely.

Managed focus is `herdr plugin pane focus <pane-id>`. If ownership cannot be
confirmed (principally legacy or migrated state), the plugin falls back to
`herdr tab focus <tab-id>`. Focus failures after a successful reload are
reported but do not reverse the reload.

## Review target integrity

Hunk retains comments when a session reloads between `diff` and `show`. Before a
target change, the plugin lists current user notes and maps their IDs to the old
target. Notes first seen later map to the current target. For Hunk responses
without a note ID, a digest of stable note content acts as the key.

If comments cannot be read before switching, the pair records uncertainty. Notes
visible at the next successful handoff are labeled `unknown (review target
changed before note context was captured)` instead of being silently attributed
to the new target; notes created afterward use the current target normally.
Target switching itself remains fluid and is not blocked by existing notes.

## Sending comments

For a known pair, `send-comments`:

1. verifies both stored terminal identities and the exact Hunk session;
2. runs `hunk session comment list <session-id> --type user --json`;
3. renders checkout, current target, per-note target, file/range, title, and body
   to a pair-specific Markdown file;
4. stages one newline-free instruction with `herdr pane send-text` in the paired
   agent—without Enter or `agent prompt`;
5. best-effort focuses the paired agent for user approval.

It does not list agents or fall back to another candidate when a known pair is
stale. This is the core non-misrouting invariant.

The digest of the complete Markdown body and instruction is stored after a
successful stage. An identical second invocation rewrites the notes file and
focuses the agent, but does not append the same instruction again. Changed notes
produce a new digest and are staged normally.

An unmanaged Hunk remains usable. With one repo session, the plugin may select
it directly. With several, invocation from the Hunk can identify it through its
pane PID. If neither produces one session, the action stops and names the
matching session IDs rather than using ambiguous repo selection. Shell routing
for an unmanaged Hunk still requires exactly one agent in the current tab.

## Narrow-pane behavior

The plugin does not override Hunk's `auto` layout, wrapping, theme, or sidebar.
The intended escape hatches are Hunk's `w` wrap toggle; `0`/`1`/`2`
auto/split/stack modes; `s` sidebar toggle; Left/Right horizontal scrolling; and
Herdr's default `Ctrl+B`, then `z` pane zoom. The plugin does not move a Hunk
back after a deliberate user move.

## Limitations

- The pair file uses atomic replacement but no cross-process lock. Truly
  simultaneous actions can still race and lose one state update.
- An origin-less legacy review cannot be attributed safely and is not adopted.
- `send-comments` trusts Hunk's `--type user` filtering and accepts every object
  in the returned `comments` array.
- Git target fallback is successful and silent.
- Focus and notification are best effort after the requested reload/staging has
  already succeeded.
- Herdr-managed panes close when their command exits; idle-shell relaunch exists
  for legacy action-created ordinary panes.

## Tests and verification seams

The subprocess suite runs the real Python script against fake Herdr, Hunk, and
Git executables. It covers context and Git target resolution, managed pane open
and exact focus, moved aliases, one pair per agent, stale/replaced identities,
manual and duplicate Hunk sessions, session-ID reload/comment commands, note
target preservation, deterministic cross-workspace routing, and duplicate
staging suppression.

Run:

```bash
python3 -m unittest discover -s tests -t .
ruff check .
ruff format --check .
```

Hunk 0.18.1 is installed in the development orb and its live CLI confirms the
session forms used here. Herdr is not installed in the orb, so Herdr 0.8.0 pane
ownership, movement, and focus are contract-tested against its documented CLI
and response shapes. A live Herdr smoke test remains appropriate before a
release that changes those boundaries.
