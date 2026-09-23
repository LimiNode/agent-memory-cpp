# Research workspace index and lifecycle

All active research checkouts and large payloads belong under:

```text
E:\_repoz\agent-memory-workspaces\<experiment>\
```

Each experiment uses one checkout/worktree and one remote branch. The minimum
index row is:

| experiment | branch | checkout | payload | state |
| --- | --- | --- | --- | --- |
| `<name>` | `<remote branch>` | `<absolute repo path>` | `<absolute payload path>` | `active` / `blocked` / `archived` |

Before work starts, capture the remote URLs, branch, worktree path, and clean
status. Before handoff, push the branch, record the remote head SHA in the
receipt, and leave the checkout clean. Payloads stay outside Git and are bound
by SHA-256 in receipts.

The current residual-correction wave uses the existing checkout
`E:\_repoz\agent-memory-rslm-faithful` as a transitional location. New work
should use a child checkout under `agent-memory-workspaces` rather than adding
another top-level clone under `E:\_repoz`; migration of this checkout must not
delete unreviewed commits or payloads.
