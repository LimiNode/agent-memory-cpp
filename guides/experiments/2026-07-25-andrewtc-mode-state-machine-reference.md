# andrewtc/mode State Machine Reference Review

## 2026-07-25

### Context

Reviewed `andrewtc/mode` as a small state-machine design reference for
`agent-memory-cpp` runtime services. The question was whether to borrow code or
only patterns for `TaskQueue`, `JobDispatcher`, compaction jobs and future
ingestion pipelines.

### Source

- Repository: `https://github.com/andrewtc/mode`
- Inspected revision: `f0ce95a77bc797e868bd2ff7c8b1df5ebe88271b`
- Reference surface: `README.md` at that revision
- Public README inspection date: 2026-07-25
- Local git fetch: unavailable during this review due GitHub connectivity
  timeout from the working environment.

### Question

Can `mode` improve our roadmap without adding a Rust dependency or a generic
state-machine framework to the C++17 core?

### Observed Design Points

- The library centers on `Automaton`, `Mode` and `Family`.
- A family groups states and exposes the common public interface.
- More complex state machines delegate transition responsibility to the current
  state implementation.
- Transition code can explicitly transfer selected state from the previous mode
  into the next mode.
- The library is intentionally small, safe Rust, macro-free and zero-allocation
  on its own side.

### Interpretation

Do not adopt `mode` as a dependency: it is Rust, while `agent-memory-cpp` is a
C++17 static library. Do borrow the design pressure:

- model runtime job lifecycle transitions as typed functions rather than a
  scattered `switch(JobStatus)`;
- make owner-sensitive transitions consume a current persisted snapshot plus
  `ClaimToken`;
- return both primary `JobRecord` changes and queue-index deltas from the same
  transition operation;
- allow only explicit state transfer across transitions, such as retry counters,
  `last_error`, `lease_epoch` and compaction handoff checkpoints.

### Roadmap Impact

Added `runtime-services-roadmap.md` §4.6.1 "Typed transition pattern" and a
cross-reference from `memory-stacks-roadmap.md` Step 14. This keeps the idea
local to runtime services and avoids creating a generic state-machine subsystem.
The whitelist of allowed `JobLifecycle` transitions, `ClaimToken` fencing, and
queue-index delta production are local `agent-memory-cpp` policy; upstream
`mode` is intentionally small and does not provide those queue consistency
guarantees by itself.

### Follow-Up Checks

- Add table-driven transition tests when `TaskQueue` implementation starts.
- Keep lifecycle helpers private to runtime services unless a second independent
  subsystem needs the same abstraction.
- Do not add a public state-machine framework to `agent-memory-cpp` without a
  separate ADR.
