# Lexical adapter and publication coordinator compatibility

Date: 2026-10-26

Status: interface note; no coordinator dependency

The current publication-coordinator stack is owned by the parallel
publication workstream (reference PR #554 head was `7fc8f7ab` at audit time).
This note records the boundary for a future incremental lexical participant;
it does not modify or import those unmerged types.

## Two independent guarantees

**Lexical-local atomicity** is the incremental adapter's own MDBX guarantee:
one writable transaction publishes the unit row, touched partitions, token
metadata, counters, and lexical publication marker, with rollback and reopen
recovery.

**Cross-store publication consistency** is a coordinator guarantee: canonical
content, knowledge unit, projection, lexical, and manifest participants are
validated and committed under the coordinator's transaction boundary.

The former must never be described as the latter. A lexical-only snapshot can
be internally coherent while canonical/projection/manifest evidence is stale
or absent.

## Requirements for a future participant

The coordinator stack currently requires the following properties, which a
seg-v2 adapter must satisfy before integration:

| Coordinator concern | Adapter requirement | Current status |
| --- | --- | --- |
| transaction ownership | participant mutates through the coordinator-owned transaction and never commits/aborts it | design only |
| participant connection identity | participant context is exactly the coordinator context | compatible in principle; no dependency now |
| durable fencing | writer admission is checked before and inside the same writable transaction | lexical-local contract to implement |
| publication receipt | lexical publication exposes descriptor/snapshot/sequence evidence that can be recorded in a receipt | design only |
| scope frontier | lexical descriptor/snapshot reports scope, projection, epoch, generation and source revision | existing lexical fields plus proposed seg-v2 marker |
| pinned read lifetime | read participant consumes one owner-bound read transaction | existing coordinator read-scope pattern; not wired |
| writer registration boundary | all writers for a scope must be admitted or legacy writers quiesced before activation | coordinator policy, not lexical adapter |
| completion semantics | `TargetExact` is evidence for a selected target, not scope-wide token-content equivalence | must remain explicit |

The coordinator's bounded `TargetExact` result proves identity-level evidence
for the selected resource/unit/projection target. It does not prove that all
lexical tokens in a scope equal a newly built index, nor that an external
legacy writer was globally excluded. A future lexical participant must supply
its own descriptor, epoch, generation, and row-integrity evidence; the
coordinator may combine that evidence but must not infer it from a generic
layout number.

## Integration boundary

The first seg-v2 implementation remains a lexical-only adapter and may use
its own local transaction. It must not include coordinator headers, change
PRs #544--#555, or make those unmerged types a compile-time requirement. A
later integration slice can add a thin participant wrapper after both sides
are merged and after the coordinator owner accepts the receipt/frontier
contract.

Minimum future participant contract:

1. prepare the bounded lexical delta before opening the coordinator scope;
2. verify context identity and descriptor/epoch compatibility;
3. apply only bounded touched-partition work to the supplied transaction;
4. return a lexical evidence record containing index identity, source frontier,
   touched-token/row counters, and physical integrity result;
5. let the coordinator commit or abort; reset transient prepare state after an
   abort so an identical prepared operation can be retried.

No cross-store implementation is authorized by this note.
