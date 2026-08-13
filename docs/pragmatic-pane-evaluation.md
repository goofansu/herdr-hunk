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

## Product behavior specification

The maintainer clarified the goal after the initial evaluation:

> A Hunk pane is the agent's copilot in the current tab.

### Required stories

1. Invoking review from an agent opens or reuses that agent's Hunk in the same
   tab.
2. Repeated invocation does not multiply panes unnecessarily.
3. Comments from a plugin-managed Hunk return deterministically to its paired
   agent.
4. One agent never silently takes over another agent's Hunk or comments.

The conceptual unit is therefore an **agent–Hunk pair**, not a checkout-global
review. Findings below that describe checkout-global takeover are implementation
gaps.

### Permissive recovery rules

- Review from an ordinary shell is allowed when exactly one agent is in the
  current tab. Ambiguous shell invocation should ask or fail clearly rather than
  guess by lifecycle recency.
- A Hunk deliberately moved by the user is not moved back automatically. Reuse
  should focus it and explain where it is; restoring adjacency should be an
  explicit action.
- Pairing follows the current agent pane. If that agent exits or is replaced,
  stale state may be discarded and rebuilt; process replacement need not retain
  the old relationship.
- Switching between `diff` and `show` is not blocked merely because notes exist.
  Feedback should retain or report enough target context to remain intelligible.
- Manually opened Hunk sessions are allowed. The plugin should stop only when an
  operation is genuinely ambiguous, and should explain how to recover.
- Hunk copilots are created on demand. The plugin does not proactively add one
  for every agent in a crowded tab.

Session IDs, state keys, and managed plugin panes are possible implementation
mechanisms, not product requirements. The implementation should use the smallest
mechanism that satisfies these stories.

## Combination results

| Arrangement/action | Observed result | Practical verdict |
| --- | --- | --- |
| One agent opens one plugin review beside itself | Hunk is split right, focused, watched, remembered, and comments route to that agent. | Clear happy path. |
| Same agent invokes either review action repeatedly | The pane count stays fixed and the session reloads. | Good; safe for a keybinding. |
| Two agents share a checkout and the second opens/reuses the review | One shared Hunk pane is retargeted and `origin_pane` changes to the second agent. Comments route to the latest caller. | Violates the copilot model: the second agent takes over the first agent's Hunk instead of getting/reusing its own paired pane. |
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

The first invocation creates the intended association: agent on the left, its
Hunk copilot on the right. The state model does not preserve that association.
Another agent, tab, or workspace using the same checkout takes over the review
and becomes its origin. This conflicts directly with the required stories.

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

### P0 — preserve pairing without making recovery rigid

1. **Persist enough pair identity to prevent takeover.** A review record must
   distinguish agents using the same checkout. The exact key and whether a Hunk
   session ID is stored are implementation decisions. When a known pair exists,
   comment routing must not fall back to workspace-wide lifecycle recency.
2. **Reuse the pair in its current location.** Normally it is in the agent's tab
   because that is where the plugin creates it. If the user moved it, focus that
   pane and report its location rather than mutating the layout automatically.
3. **Handle multiple Hunk sessions deliberately.** Real Hunk permits multiple
   sessions for one checkout but makes `--repo` selectors ambiguous. Use session
   IDs when multiple plugin pairs require them; tolerate a manual session until
   an operation is actually ambiguous, then fail with an actionable message.
4. **Keep target switching fluid.** Do not block `diff`/`show` switching by
   default. Preserve or include changeset context with notes so feedback cannot
   silently appear to describe the wrong target.

### P1 — make common actions predictable

5. **Support the useful shell case.** If review is invoked from a shell and the
   current tab contains exactly one agent, create or reuse that agent's pair. If
   several agents are plausible, fail clearly instead of choosing by recency.
6. **Focus the actual review pane.** For an ordinary pane, at minimum focus its
   tab using the `tab_id` returned by `pane get`. A manifest `[[panes]]` entry may
   provide stronger ownership and absolute `plugin pane focus`, but adopting it
   is an implementation choice rather than a requirement.
7. **Deduplicate identical staging.** A repeated keypress with unchanged notes
   should focus the already targeted agent instead of appending the same
   instruction again.
8. **Recover stale pairs naturally.** If the paired agent or Hunk pane is gone or
   replaced, discard that stale relationship and create a new pair on demand.

### P2 — improve review ergonomics without imposing preferences

9. **Keep Hunk's `auto` mode and user rendering settings.** The live width tests
   confirm that forcing split mode would be worse in common half-width panes.
10. **Document zoom/wrap as the intended narrow-pane escape hatch.** A short usage
   note is cheaper and less surprising than forcing `--wrap` (which can consume
   substantial vertical space) or silently opening a different placement.
11. **Consider a separate “open full review” action only after real user demand.**
   A managed tab/zoomed pane would improve long-line review, but adding layout
   choices now increases concepts and keybindings. Fix routing, visibility, and
   note-target integrity first.
12. **Treat the clean-review message as upstream Hunk UX.** The plugin launches
    watched empty reviews intentionally, including while an agent is about to
    write changes. Hunk could say “No changes yet; watching…” when no filter is
    active. The plugin should not preflight and skip the pane, because that would
    remove useful watch behavior.

## Suggested validation matrix for future changes

Any implementation work following these recommendations should retain the
existing 81 tests and add end-to-end boundary cases for:

- origin agent and Hunk in different workspaces;
- two agents in different tabs on the same checkout, each retaining an
  independent pairing without takeover;
- same checkout open in two workspaces, with comments from each Hunk returning to
  its paired agent;
- review pane in a background tab of the active workspace;
- shell invocation with zero, one, and multiple agents in the current tab;
- a deliberately moved paired Hunk, verifying focus without automatic movement;
- an unmanaged live Hunk session before first plugin invocation, both before and
  after its presence causes genuine Hunk selector ambiguity;
- user notes present while switching `diff`/`show`;
- identical `send-comments` invoked twice before submission.

At least one live Herdr session should then verify that tab/pane focus lands where
the API response says it does. Hunk integration should use real PTYs for duplicate
repo sessions and note-preserving reloads; the subprocess fakes cannot establish
those daemon behaviors on their own.
