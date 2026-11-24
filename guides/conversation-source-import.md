# Conversation Core Phase 3B: source import / normalization

- Status: active contract for the bounded reference slice; full Conversation
  Core remains Planned. Date: 2026-11-24. Owner: conversation ingestion.
- Dependency: [Phase 3A](conversation-reference-store.md) and the
  [Conversation/Event contract](conversation-event-contract.md), especially
  sections 3 and 4.2. No replacement event grammar or source identity scheme.
- Review trigger: new source family, identity equivalence, target reconciliation,
  durable backend, or broader authority/retention guarantee.

## Boundary and public API

`<agent_memory/ingestion.hpp>` exposes `IConversationImportSink` and the compiled
`InMemoryConversationImportSink`. Its source DTOs represent created messages,
snapshot observations, original tool calls and source-reported tool results.
They are not envelopes with caller-selected Entry IDs. The deterministic
reference adapter checks/normalizes those DTOs into existing typed AMCE values.
It does not parse a provider archive, execute tools, authenticate a source or
prove artifact retention. The trusted host must admit exact immutable evidence
under the [artifact owner](artifact-provenance-roadmap.md) before import.

| Operation | Responsibility |
| --- | --- |
| `create_conversation(access, ref)` | Host selects the existing namespace-qualified Conversation identity; create sink/store state together |
| `import_observation(access, source_record)` | One bounded atomic admission: validate source, compare replay witness, resolve declared target, allocate next Entry and append canonical AMCE |
| `correct(access, envelope, expected_revision, reason)` | Explicit Phase 3A CAS representation correction; never another source occurrence |
| `history()` | Borrowed const `IConversationStore&`, valid only during sink lifetime; owning bounded read results and opaque frontier continuations retain Phase 3A semantics |

The [C++17 example](../examples/conversation_import.cpp) is also compiled/run as
an installed and relocated-package consumer. Binary call tokens use ByteBuffer;
text and existing opaque legacy bindings retain their owner contracts. Calls
borrow input only for the synchronous operation; accepted state owns its bytes.
Multiple producer/adapter callers may share one sink. No mutable-store escape
permits append or allocation outside its coordination boundary.

## Identity and replay

An instance-local structured map is qualified by the full ConversationRef and
its admitted scope. Its occurrence key is:

| Declared source identity | Exact key within that conversation/scope |
| --- | --- |
| ExternalEvent | Full canonical AMXR event reference + selected retained SourceRevisionId |
| ObjectRevision | Full canonical AMXR object reference + explicitly reliable provider revision marker + selected retained SourceRevisionId |
| LocalObservation | Selected retained SourceRevisionId + evidence ArtifactBindingId + exact record locator |

This map uses complete values, not delimiter concatenation, content hashes,
timestamps, call tokens or an unqualified message ID. Full AMXR retains provider,
instance, identity domain, object kind, parent and key type. Standalone AMXR
bytes may cross its existing string API through an explicit binary adapter;
its grammar and public API are unchanged.

Every source binding requires revision, evidence, locator and adapter recipe.
The reference recipe is exactly the table above: callers may not reinterpret
its fields as another identity scheme. The host/source adapter still owns
proof that the declared event key or provider marker is reliable. Missing
marker rejects ObjectRevision; explicitly declare a LocalObservation snapshot
when only observed state is known. LocalObservation cannot claim MessageCreated.
Snapshots never infer unobserved creation/edits or completeness of source history.

Recipe is representation identity, **not part of the occurrence key**. Mapping
stores the original source-shaped replay witness, original exact `EntryRef@1`
and original admission generation. Identical witness returns NoChange without
new Entry/revision/generation. A different recipe, payload, retained evidence,
attribution, classification input, original/effective request or declared target
under the same key returns Conflict, without replacing either canonical history
or its mapping. Even classifications that both normalize to Unknown are not
interchangeable. Refusals expose no existing ref or hidden generation.

New source evidence/observer receipts are not automatically coalesced; separate
observation receipt support is deferred. Dedupe is limited to this live sink
instance and selected retained source revision. Different archives, polls or
revisions do not acquire universal equivalence. The same external object with
distinct source event keys remains distinct occurrences. Independent sinks do
not coordinate IDs, memory or mappings, even with identical namespace strings.

An explicit `correct` preserves Phase 3A immutable source/target constraints and
CAS-before-no-op. It does not rewrite the original replay witness or admission
reference. Replaying the original input after correction names original@1 and
cannot undo newer representations. Reimporting a changed recipe conflicts;
use explicit CAS correction, not a second Entry. No automatic renormalization,
target reconciliation or new wire schema is introduced.

## Target and tool semantics

A tool result accepts an exact local EntryRef, an evidence-qualified source
target, or an explicitly unresolved target. Source targets must declare the
same full ConversationRef and scope. A present mapping resolves to its original
exact revision, never latest. Store admission checks typed call/token matching.
A missing source mapping retains its exact external reference/evidence as
Unresolved. It does not search by call_id, text, nearest time, observer or count.
Explicit missing/inaccessible local exact targets refuse; they are not silently
converted to unresolved. Wrong target scope is Denied; cross-conversation call
targets refuse. Neither identity nor source lookup grants access.

The mapping freezes the first successful resolution decision. An out-of-order
result admitted before its call stays unresolved on identical replay after the
call arrives. Changing its declared target conflicts. Later reconciliation is
a separate future operation; it must not silently rewrite historical evidence.
Partial archives and late arrivals retain admission order, not invented causal
or provider chronology.

Original requested arguments remain the ToolCall request binding. Optional
effective arguments are separately retained in the source evidence and replay
witness, not substituted into that binding. ToolResult origin and reported
outcome are separate inputs. Only explicitly Reported origin uses its declared
reported outcome; denial, recovery, timeout, repaired and unknown origins become
Unknown regardless of exit-shaped data or is_error. A Reported Succeeded is
still a source report, not proof that a tool body ran or that external effects
were independently verified. No outcome, role or deserialization authorizes
execution or retry.

Actor, observer and producer are separate attribution axes. AMCE contains actor
and observer; producer/runtime origin, raw flags and effective-argument facts
remain recoverable from the exact source artifact and recipe. The producer
descriptor is a nonempty strict UTF-8 input (at most 1024 bytes), not an actor
substitute, credential or complete machine-readable lifecycle. A lifecycle
consumer needs separately reviewed typed schemas, not interpretation-sensitive
data hidden in advisory extensions. The eight source-shaped tests qualify this
reference adapter on authored fixtures, **not execution/conformance of an
upstream SDK**.

## Atomicity, ordering and limits

The backend-neutral seam requires allocation, mapping and canonical append to
share one admission unit. The reference sink privately owns the Phase 3A store,
per-conversation next ID and map under one mutex. All potentially throwing
mapping/result preparation precedes append. A tentative map entry is removed
on any refusal/exception; the store's strong-guarantee append is the commit
point. After Applied, only nonthrowing moves/counters publish the result and
advance next ID. Create prepares its bookkeeping before store creation with
the same rollback discipline. CAS correction shares the write lock and does
not change the import map. No host callback runs under the lock.

IDs commit consecutively in lock acquisition/admission order. There is no
unprotected maximum-ID scan or ID reservation exposed as committed provenance.
Reads use the store's own lock and cannot mutate sink bookkeeping. This is
**instance-local exception atomicity**, not a persistent transaction, recovery
log, cross-process allocator or exactly-once execution guarantee.

One observation is the transaction unit. There is deliberately no batch/archive
API: callers importing a sequence receive explicit results in call order, and
must retain the successful prefix/retry position. A later refusal does not
claim rollback of earlier successful calls or a complete archive import.

| Budget | Default | Supported ceiling |
| --- | ---: | ---: |
| Observation logical bytes | 2 MiB | 32 MiB |
| Mapping rows | 4096 | 65536 |
| Aggregate mapping logical bytes | 64 MiB | 256 MiB |
| Canonical storage / field / codec budgets | Phase 3A options | Existing Phase 3A / AMCE caps |

Input preflight bounds variable bytes/counts before owning copies, and aggregate
mapping charge includes key, witness, references and scalar overhead. Budgets
are conjunctive; overflow-safe checks reject rather than wrap. These are
logical charges, **not heap/RSS bounds**: containers, allocator metadata,
temporary canonical encoding and result/read copies cost additional memory.
Zero import budgets prohibit that resource, never mean unlimited. Invalid
options throw invalid_argument. Typed format/value/limit refusals return
InvalidRequest/ResourceLimit, conflicting witnesses Conflict, missing/hidden
conversation NotFound, malformed/mismatched access Denied. Allocation failures
propagate without successful partial admission. The host authorizes every call;
scope/policy-shaped values do not themselves implement authentication.

Potentially throwing copies of nested external identities use the shared
[explicit copy operations](identity-policy.md#61-explicit-exception-safe-value-copies),
including validation probes, normalized metadata, retained source witnesses
and source/unresolved targets. The adapter reconstructs its source-shaped DTO
around those operations; it does not introduce another AMXR copying scheme.
Input lvalue copies made by a caller before this API remain ordinary STL
operations, outside the sink's exception-atomicity qualification.

## Reuse and deferred gates

| Disposition | Surface |
| --- | --- |
| REUSE | Conversation/Entry/Actor identities, source bindings, AMXR, artifact references, AMCE typed pair, Phase 3A immutable history/current/CAS/read budgets |
| EXTEND | Ingestion umbrella, compiled reference adapter, installed example, source-shaped normalization/conflict/target/concurrency tests |
| NEW | Narrow IConversationImportSink atomic admission seam and instance-owned source map/ID coordinator; no second canonical store |
| DEFER | Real provider parsing/conformance, observation receipt coalescing, explicit target reconciliation, additional runtime lifecycle schemas |
| DEFER | Phase 4 durable mapping/allocator/history/index transaction, persistence DBIs, reopen/crash recovery, scalable projection/checkpoint alternatives |
| DEFER | Phase 5 full Core recovery/retention/erase/authority and performance qualification; Phase 6 derived-publication bridge |

The independent Memory Foundations audit does not authorize ontology SPI,
graph/embedding work, tool/runtime execution or Managed Memory Blocks in this
slice. No DBI/schema, migration, release or merge is part of Phase 3B.

Executed checks and remaining evidence gaps belong in the
[qualification report](audits/2026-11-24-conversation-source-import-qualification.md),
not in a second specification.
