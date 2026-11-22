# Search integration

Status: staged implementation; no release/default activation.
Baseline: `acd5ad5545386b3feeaa79f8a587855b60801b4d`.

This work reuses existing algorithms in the product path. It does not promote
research conclusions to production readiness or replace the normative
[milestones](milestones.md). The only active functional goal is persistent
lexical retrieval. Complete its independent correctness/qualification work
before switching to exact semantic. If only approved metadata/access/publication
contracts remain external, that documented dependency may remain open during
the exact-semantic slice; it does not close lexical Phase 1. PRR/PR-IVF + THQ
follows exact semantic, then explicit search composition.
PRR means Prototype–Representative Routing; PR-IVF means
Prototype–Representative Inverted File. Historical R4 is evidence, not a backend.

## R6 disposition (2026-11-21)

All four PRs remain Draft; no merge or restack was performed. The exact-head
checks below predate a future base synchronization. Review threads were empty,
but GitHub had no formal review decision; this is not independent approval.

| PR / documentation HEAD | Measured source | Verdict | CI / packaging run |
| --- | --- | --- | --- |
| #676 `eab50143` | inventory baseline `9005a010` | documentation only | `44768464020` / `44768464167` |
| #677 `34a24d2c` | `a587fa326d25b40a92dacf09ea93c772b9fcaa9c` | ADC `KEEP_CURRENT` | `44684602226` / `44684602137` |
| #678 `d24d8b7f` | combined `04b466d41193df3925bc21068d6432c2f5342840` | ADC `KEEP_CURRENT` | `44783777513` / `44783777754` |
| #679 `4cb3fd76` | `fdc21558678e2985d260824f7929d89ceb3bd276` | compact-set `CONDITIONAL` only synthetic profiles 18/19; production `KEEP_CURRENT` | `44859701859` attempt 2 / `44859701547` |

All listed workflows succeeded. #676 ran 3 CI jobs with 21 skipped; packaging
was 6/6. Each other PR ran 30 CI jobs with 2 skipped (`Full research CTest`,
`R5 combined integration evidence`); packaging was 6/6. #679's initial macOS
dependency-clone failure was retried without a source change.

The read-only disposition audit checked seven small retained package manifests
and all 196 listed records for availability/size, not full payload rehash/replay.
ADC document codes (96,000,000 bytes), thresholds (4,608 bytes) and queries
(233,472 bytes) remain available with recorded SHA-256 respectively:

```text
0a0c825720bccef97a0fd1af5c7727671b5e0a79be09b643e0fcb558236e2b70
ceaa7ed312525bfa81a96b9f53aa79fb7f210387eb190a752051165dbdbd37a4
abe14a8790bd488fc91b01b4b1d6ab664db1d2f2d69e67147ed8439f54c73191
```

MultiProbe final GNU/MSVC package manifest hashes are respectively
`81ccf97f57f5e9ea8cc61d0696999bbbe211cf33c74c7714fff90c94bab2e7c1` and
`52081910df6d0ac4835187c6090b7fbadc14b3ca3888cfa1465887b6e3287e36`.
Input qualification matrix SHA-256:
`5b28c851770d38703ea9abbdd3cbb88490ac09f284d74687741da4475713bf3f`.
These are retained local evidence identities, not a public replay package.

K8 prototypes/memberships are missing. Original IVF256 bindings are incomplete;
alternate IVF4096 was trained on base/evaluation documents and is invalid for
the required disjoint-quality protocol. Both experiments stay `NOT_EXECUTED`.
Fresh sealed applied quality and public full replay are not established.
The original #677 measured executable was not retained separately; the later
corrected-source confirmation in #678 is new evidence, not its recovery.

With separate permission, future integration order is #676, then #677, then
#678, then #679. Preserve the #677 → #678 stack, keep its base branch until the
child is retargeted, and inspect the child's diff after squash integration.
Reconcile append-only registry and CI/CMake edits with #679 semantically.
Each changed base/HEAD needs independent diff review and new exact-head checks.
Historical measured SHA/raw/receipts must never be rebound to a restacked HEAD.
R6 integration does not block lexical development; no further untargeted R6
experiments are scheduled.

## Lexical implementation map

| Existing component | Actual role / scorer | Reuse / remaining boundary |
| --- | --- | --- |
| `ExactLexicalIndex` | in-memory chunk BM25 | shared scoring primitive and independent oracle control |
| `FlatLexicalIndex` | unit postings + TF/DF/length statistics, no ranking | persistent logical snapshot baseline |
| `PersistentLexicalIndex` | dependency-free identity/frontier values | no storage/query engine |
| `MdbxPersistentLexicalIndex` | flat-v2 durable payload, full reconstruction | bounded persisted baseline; not serving-scale traversal |
| `MdbxSegmentedLexicalIndex` | segmented durable rows | separate layout, not silently substituted for flat-v2 |
| `MdbxPersistentLexicalCandidateAdapter` | legacy TF; explicit opt-in BM25 window | candidate source, not canonical authority |
| `ProjectionAwareLexicalRetriever` | bounded per-query in-memory BM25 rebuild | existing producer/admission checks, not persistent postings |
| `RevisionBoundLexicalRetriever` | canonical admission, no scoring | retain typed IDs and freshness/materialization states |
| `MdbxKb0RetrievalFacade` | trace over snapshot admission + rebuild producer | not yet wired to the persistent candidate window |

The first increment adds opt-in BM25 over persisted statistics and an explicit
full bounded window so admission can precede final top-K. Legacy TF/default
and `generate()` limiting semantics remain unchanged. Its tests must cover
numeric goldens, exact-index parity, repeated terms, ties, no-match, budgets,
invalid statistics, update/delete and reopen. It does not close Phase 1.

For an existing index and tokenizer (MDBX enabled):

```cpp
agent_memory::PersistentLexicalCandidateOptions ranking;
ranking.scoring = agent_memory::PersistentLexicalScoring::Bm25;
agent_memory::MdbxPersistentLexicalCandidateAdapter candidates{index, tokenizer, ranking};
agent_memory::RetrievalQuery query;
query.text = "publication frontier";
query.limit = 10;
const auto window = candidates.generate_window(query);
// Check completeness, admit every candidate against canonical authorities,
// and only then select final top-K. Do not expose this window as product hits.
```

The current admission response preserves one observation per candidate and does
not refill/sort it. A usable limited M0 hit requires both `Current` freshness
and `SummaryOnly` materialization; `Current` alone may accompany `Unavailable`.
`admitted_count` counts freshness, not materialized returned hits. Truncated
candidate windows propagate as `BudgetExceeded`, not exhaustive current top-K.

The existing MDBX installed-consumer fixture creates a flat index, closes its
adapter/environment, reopens it and exercises this opt-in window through only
installed headers and static-library symbols. That is same-process reopen,
not process/crash recovery or proof of canonical admission.

## Interface requests to the canonical/domain owner

These are requests for the first agent's decision, not new competing APIs.
They remain deferred dependencies, not a request to interrupt the first agent's
Identity Architecture / AMXR work. Do not implement dependent authority paths
until their owner approves the contracts.

1. **Metadata authority.** `KnowledgeUnitEnvelope` has no metadata map and the
   Original projection has body-only lexical statistics. Confirm which existing
   revision-qualified canonical record supplies `RetrievalQuery.metadata_filters`,
   its comparison/AND semantics and how to read it in the final admission
   transaction. Missing/unsupported filters must fail closed. A mutable external
   map or metadata copied from postings must not become authority.
2. **Access admission.** Scope isolation already exists, but there is no
   principal/policy decision in this candidate seam. Specify the approved
   application-owned authorization hook and its consistency/budget contract,
   including deny/missing/changed-policy behavior. Ranking must not truncate
   before mandatory scope/access admission. Do not call scope checks ACL proof.
3. **One read frontier.** Approve the composition seam that joins the persistent
   lexical snapshot with canonical units, projections, manifests and any
   filter/access evidence in one caller-owned MDBX read transaction. The current
   generic admission performs independent reads. The flat snapshot's opaque
   transaction owner cannot establish this join by itself. Do not claim Exact,
   pinned hydration or linearizability from this increment.

BM25F remains deferred: `FlatLexicalUnitStats` and postings lack per-field
lengths/TF. Implementing title/heading/body today would require a reviewed
persisted-layout delta, not merely a new scorer flag. No BM25+/BM25L/WAND,
new schema, ID change, migration or production-default change is included.

## Phase 1 closure gates

Persistent create/reopen, insert/update/delete and scorer correctness are only
part of closure. Remaining gates are approved metadata/access admission before
final top-K, exact source/projection binding through a shared frontier, bounded
whole-query work, no stale/corrupt results, source hydration, an installed C++17
consumer, GNU/MSVC and Linux MDBX checks, independent review and full-query
latency/memory receipts. Flat reconstruction must be counted, not excluded as
setup. No quality/latency qualification or release eligibility is claimed here.

| Mode | IMPLEMENTED | INTEGRATED | QUALITY_QUALIFIED | RELEASE_ELIGIBLE |
| --- | --- | --- | --- | --- |
| Persistent lexical product mode | candidate/scoring subset | no | no | no |
| Exact semantic product mode | existing algorithm/storage subset | no | no | no |
| PRR/PR-IVF + THQ product mode | research components only | no | no | no |
