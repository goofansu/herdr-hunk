# Pragmatic pane evaluation

This is an empirical UX review of the implementation documented in
`docs/current-implementation.md`, performed on 2026-08-13. It focuses on what a
user experiences when agent and Hunk panes are arranged in different ways, not
only whether each CLI call is valid.

## Evidence and limits

Three evidence sources were used:

1. The plugin was run as a subprocess against its Herdr/Git/Hunk boundary with
   pane lists varied by agent count, tab, workspace, checkout, remembered origin,
   and plugin ownership. These are the same fakes as the production test suite,
   so the actual routing code and argv are exercised.
2. Hunk 0.18.1 was run in real PTYs over temporary Git repositories at 180×42,
   82×30, and 100×28 cells. Its daemon API was used to add/list comments and
   reload sessions between `diff` and `show`.
3. Herdr 0.8.0's current official CLI and plugin documentation was checked for
   focus, pane movement, send-text, and managed plugin-pane semantics.

Herdr itself was not installed in the orb. Layout/focus conclusions therefore
come from Herdr's documented contract rather than a live Herdr screen. Hunk UI
and session conclusions are live observations.

## Combination results

| Arrangement/action | Observed result | Practical verdict |
| --- | --- | --- |
| One agent opens one plugin review beside itself | Hunk is split right, focused, watched, remembered, and comments route to that agent. | Clear happy path. |
| Same agent invokes either review action repeatedly | The pane count stays fixed and the session reloads. | Good; safe for a keybinding. |
| Two agents share a checkout and the second opens/reuses the review | One shared Hunk pane is retargeted and `origin_pane` changes to the second agent. Comments route to the latest caller. | Coherent if users understand that the review is checkout-global, not agent-owned. |
| Two equally plausible agents, no remembered origin | The most recently state-changing agent receives staged text; a toast names the other candidate. | Visible recovery, but lifecycle recency is not authorship. |
| Hunk is in another tab in the same workspace | Reuse calls only `workspace focus`. Herdr documents separate `tab focus`; workspace focus does not identify the review tab or pane. | Review can reload successfully while remaining hidden. |
| Hunk is moved into a review-only workspace | Review reuse can still address the old pane ID because Herdr retains moved-pane aliases, but `send-comments` from Hunk searches only its current workspace and finds no agent. | Sending from the natural place—the completed review—fails. |
| Two workspaces use the same checkout | The checkout-global record can name an origin in workspace B while Hunk remains in workspace A. Sending from Hunk searches A and can silently choose A's sole agent instead. | High-risk misrouting. |
| User starts Hunk manually, then invokes `review-changes` | With no recorded plugin pane, the plugin does not inspect the existing live session and opens another Hunk. Real Hunk allows both sessions, after which every `--repo` lookup fails as ambiguous. | High-risk self-created broken state. |
| A manually opened Hunk is reported as an agent but is absent from plugin state | The synthetic pane matrix selected Hunk itself when it was the most recently active candidate. | Conditional edge case; current Herdr agent manifests may not classify Hunk as an agent. |
| `review-changes`, add a user note, then `review-commit` | Real Hunk retained the user note unchanged after reload to `show HEAD`, and retained it again when switched back to `diff`. | A note can be sent while the visible changeset is not the one it was written against. |
| Separate `diff` and `show` Hunk processes for one checkout | Real Hunk registered both, but `session get/comment/reload --repo` became ambiguous and required a session ID. | Separate panes require session-ID state throughout; they are not a small layout-only change. |
| Hunk at 180×42 | Auto mode rendered a side-by-side old/new diff, but long lines still truncated at each half's width. | Strong overview, limited long-line inspection without horizontal scrolling or wrap. |
| Hunk at 82×30 | Auto mode switched to a stacked unified layout; long lines truncated near the pane edge. | Usable, but dense. A half-terminal Herdr split commonly lands here. |
| Clean watched diff at 100×28 | Hunk stayed open with `0 files` and the center message “No files match the current filter.” | Ambiguous: it can mean “waiting for changes” rather than an active filter. |

## Main confusions and anti-intuitions

### 1. “Beside this agent” becomes “one movable review for this checkout”

The first invocation creates a strong spatial association: agent on the left,
its review on the right. The state model does not preserve that association.
Another agent, tab, or workspace using the same checkout takes over the review
and becomes its origin. That is a valid resource model, but it conflicts with the
action description and the user's spatial mental model.

The mismatch is most harmful across workspaces: the review remains physically in
one workspace, attribution moves to another, and comment discovery is scoped back
to the review's workspace.

### 2. Successful reload does not imply visible review

`focus_review` is named and described as surfacing the review pane, but it calls
`workspace focus` only. When the review is in another tab of the already active
workspace, that is insufficient by Herdr's API model. The command reports success
even though the user may see no change.

### 3. Review type changes but note identity does not

`review-changes` and `review-commit` look like independent actions but mutate one
Hunk session. Hunk preserves user notes across reloads. The pane title changes,
but neither the plugin nor `send-comments` records which target a note was written
against. File-and-line metadata can still look plausible on the wrong changeset,
making this worse than an obvious failure.

### 4. Manual and plugin Hunk sessions do not coexist safely

The plugin tracks panes, while Hunk commands target a repository. If a user
already has Hunk open outside plugin state, the plugin creates a second session.
Hunk supports that, but repository selectors then reject both as ambiguous. The
next keypress appears to encounter an unreachable daemon even though the daemon
is healthy and reporting two matches.

### 5. Staging is safe from auto-submit but not from prompt collision

Herdr documents `pane send-text` as literal, non-submitting input. That satisfies
the deliberate approval step, but it appends at the current terminal cursor. If
the agent already has a draft, or the key is pressed twice, the generated
instruction can concatenate with existing text. Focusing afterward makes the
problem visible but does not prevent it.

### 6. The default split favors context over review width

Opening right beside the agent is excellent for maintaining context. In a
typical terminal, however, a 50/50 split leaves Hunk around 60–90 columns wide;
the live 82-column run used stacked mode and truncated long lines. This is not a
correctness bug—Hunk provides wrap, horizontal scrolling, stack/split toggles,
and Herdr provides zoom—but the initial layout optimizes “beside” more than
“readable code review.”

## Recommendations, in order

### P0 — prevent wrong or stranded handoffs

1. **Resolve the remembered origin globally before workspace-local fallback.**
   Ask Herdr whether `origin_pane` still hosts an agent even when it is outside
   the invoking workspace, and stage there if valid. Only use the existing
   same-tab/checkout/workspace heuristic when no valid origin exists. This is the
   smallest fix for moved review panes and same-checkout multi-workspace routing.
2. **Refuse to create an unmanaged duplicate Hunk session.** Before opening a
   pane with no usable record, inspect Hunk sessions for the checkout. If one
   already exists and cannot be unambiguously correlated to a Herdr pane, fail
   with an actionable “close or adopt the existing Hunk review” message. Do not
   split first. Correlating Hunk's PID to a pane process and adopting it would be
   nicer, but refusal is smaller and safer.
3. **Guard target changes when user notes exist.** Read the current Hunk input
   kind and user-note count before changing `diff` to `show` or vice versa. If
   notes exist, keep the current target and ask the user to send/remove them
   first. Separate sessions are not the pragmatic first fix because every Hunk
   operation would need a stored session ID and a way to choose which session's
   comments to send.

### P1 — make spatial behavior match the action

4. **Focus the review's tab, not only its workspace.** The minimal change is
   `tab focus` using the `tab_id` returned by `pane get`; this at least makes the
   review layout visible. The stronger long-term fit is a manifest `[[panes]]`
   entry opened as a managed split, because Herdr then provides absolute
   `plugin pane focus` and preserves plugin ownership as the pane moves.
5. **Name the resource model in user-facing text.** If one review per checkout is
   retained, action output/toasts should say “checkout review” and identify when
   it moved focus away from another agent/workspace. Avoid implying durable
   one-agent/one-Hunk pairing.
6. **Deduplicate identical staging.** Store a digest of the note set and target
   pane after a successful send. A repeated keypress with unchanged notes should
   focus the already targeted agent and notify instead of appending the same
   instruction again. This does not solve collision with an unrelated draft, but
   it removes the common self-inflicted collision.

### P2 — improve review ergonomics without imposing preferences

7. **Keep Hunk's `auto` mode and user rendering settings.** The live width tests
   confirm that forcing split mode would be worse in common half-width panes.
8. **Document zoom/wrap as the intended narrow-pane escape hatch.** A short usage
   note is cheaper and less surprising than forcing `--wrap` (which can consume
   substantial vertical space) or silently opening a different placement.
9. **Consider a separate “open full review” action only after real user demand.**
   A managed tab/zoomed pane would improve long-line review, but adding layout
   choices now increases concepts and keybindings. Fix routing, visibility, and
   note-target integrity first.
10. **Treat the clean-review message as upstream Hunk UX.** The plugin launches
    watched empty reviews intentionally, including while an agent is about to
    write changes. Hunk could say “No changes yet; watching…” when no filter is
    active. The plugin should not preflight and skip the pane, because that would
    remove useful watch behavior.

## Suggested validation matrix for future changes

Any implementation work following these recommendations should retain the
existing 81 tests and add end-to-end boundary cases for:

- origin agent and Hunk in different workspaces;
- same checkout open in two workspaces, with comments fired from Hunk;
- review pane in a background tab of the active workspace;
- an unmanaged live Hunk session before first plugin invocation;
- user notes present while switching `diff`/`show`;
- identical `send-comments` invoked twice before submission.

At least one live Herdr session should then verify that tab/pane focus lands where
the API response says it does. Hunk integration should use real PTYs for duplicate
repo sessions and note-preserving reloads; the subprocess fakes cannot establish
those daemon behaviors on their own.
