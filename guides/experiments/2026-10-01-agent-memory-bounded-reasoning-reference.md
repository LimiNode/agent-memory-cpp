# Bounded reasoning and evaluation patterns (2026-10-01)

This note extracts implementation-independent patterns from the reviewed
`AG_Memory` material for `agent-memory-cpp`. It is a roadmap/reference note,
not a dependency proposal and not a claim that the source project is licensed
for code or prompt reuse. The project should reimplement any adopted contract
in its own C++17 vocabulary.

## Applicable patterns

### Bounded decisions at an adapter boundary

When an optional provider is used to resolve a small ambiguity, the memory
layer should construct a bounded menu and an explicit evidence projection. The
provider returns a short typed answer (numeric or enum-like); host code maps it
to canonical ids and rejects malformed, ambiguous, unsupported, or out-of-menu
answers. Retrieved text is evidence, never provider instructions, and provider
conversation history is not an implicit input to a replayable decision.

This is an adapter/evaluation contract only. Core retrieval remains useful
without an LLM or a decision provider.

### Narrowing, clarification, and dispositions

Each deterministic stage should narrow a candidate domain. A later stage may
create a new candidate lineage when it needs a different interpretation, but
must not silently widen or rewrite already established structural facts. A
clarification response selects from host-presented options; it is not an
unconstrained new formalization.

The wire result should distinguish a selected option from `Abstained`,
`Malformed`, and `Unsupported`. A semantic `Unknown` option, when meaningful
in the domain, is separate from a provider that could not decide.

### Derived computation is not canonical mutation

Counterfactual or branch-local assumptions stay inside a scoped assessment
environment and never become factual memory by propagation alone. A derived
conclusion records premise references, profile/rule identity, branch scope and
provenance. It is not itself a mutation proposal; a separate host policy must
decide whether any canonical successor may be admitted atomically.

Child work consumes a shared parent computation budget. It must not mint a new
unbounded budget for recursive or compound operations.

### Diagnostics and semantic evaluation

Proof, provider, retrieval and activation traces are immutable diagnostic
projections, not canonical semantic memory. Evaluation scenarios should run
from a baseline checkpoint or isolated clone, collect structured observations,
and restore state. Acceptance separates transport/runtime success, contract
validity, semantic correctness, policy correctness and outcome correctness;
`HTTP 200` or valid JSON is not a semantic pass.

Permutation/reordering stability is a useful optional evaluation profile for
bounded choices: evaluate a bounded sample of option orders, reverse-map the
answers, and abstain or escalate when the semantic result changes.

### Activation scheduling (optional)

If activation is implemented, propagate a typed seed reason and its provenance
through a sparse active frontier rather than scanning the full corpus each
tick. Time-based expiry can use a min-heap, while activation-triggered changes
touch only affected records. An optional synchronous step trace is useful for
diagnostics and benchmarks; it is not a mandatory background loop or a new
truth/retention mechanism.

## Planned contracts and gates

| Milestone | Planned contract | Dependencies | Minimum benchmark / acceptance | Risk |
|---|---|---|---|---|
| M1b/M2 adapter research | bounded typed decision profile with evidence/instruction separation and fail-closed dispositions | provider adapter, `Context` projection, cancellation/budget contracts | fake provider replay over valid, malformed, ambiguous, out-of-menu and timeout answers; deterministic host mapping; no canonical write on failure | provider leakage or prompt-injection through retrieved text |
| M1b/M2 retrieval research | monotonic candidate lineage and clarification-selection result | candidate-set/retrieval trace, provenance and revision contracts | replay a three-stage narrowing fixture; every successor is a subset or explicit reformalization; order permutation instability is surfaced | hidden widening and irreproducible routing |
| M1b/M2 knowledge-base research | branch-scoped derived result versus mutation proposal | source/evidence anchors, atomic publication, shared computation budget | counterfactual branch fixture; branch assumptions cannot appear in canonical search; budget is conserved across children | hypothetical state contaminates factual memory |
| M1b/M2 evaluation | scenario-isolated semantic oracle and typed outcome matrix | checkpoint/clone support, structured expected observations, trace projection | same scenario replay is byte-stable; runtime/contract/semantic/policy/outcome statuses are reported separately | tests pass while meaning or policy is wrong |
| M2 activation research | sparse frontier, seed provenance, expiry scheduling and optional step trace | activation metadata, lifecycle timestamps, bounded graph expansion | compare full-scan and frontier/heap implementations on synthetic corpus; same active set and expiry order under equal input; report work and memory | premature autonomous activation or scheduler complexity |

## Explicit non-goals

Do not import the source project's ontology, morphology/parser stack, prompt
library, proof rules, pacemaker, Hebbian/plasticity algorithm, garbage
collection semantics, or a general logical solver into the core library. These
patterns do not change the embedded-first boundary: storage, indexing,
retrieval, context assembly and provenance remain the project's scope; LLMs,
providers, solvers and live agent orchestration remain optional external
adapters.

## Evidence boundary

The source material is used as a design input only. No external performance or
quality number is promoted. Any implementation or activation claim requires a
fresh fixture, exact-oracle comparison where applicable, fixed budgets, and a
machine-readable trace/manifest under the evaluation roadmap.

See also [`associative-heterarchical-memory-reference.md`](2026-10-01-associative-heterarchical-memory-reference.md),
[`knowledge-activation-roadmap.md`](../knowledge-activation-roadmap.md), and
[`knowledge-base-roadmap.md`](../knowledge-base-roadmap.md).
