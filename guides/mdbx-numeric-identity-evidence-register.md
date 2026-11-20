# MDBX numeric-identity evidence register

Status: `PRE-NUMERIC BASELINES RETAINED / POST-MIGRATION REQUALIFICATION REQUIRED`

Date: 2026-11-19

This register is the compatibility ledger for the move from decimal text
physical keys to numeric MDBX keys. It does not change a logical identity,
revision, source digest, or provenance contract. It does change the bytes used
by selected DBIs, their comparator policy, and the evidence needed to call an
old-to-new open an upgrade.

This register is documentation-only. Executable replay classification is owned
by [Index lifecycle](index-lifecycle-roadmap.md) and
`tools/run-exact-generation-upgrade.py`; the register does not introduce a
second classifier or treat an arbitrary commit selection as migration proof.

The current `main` baseline still contains the historical
`MdbxKnowledgeUnitStore` layout. The opt-in `StorageIdentityMapping` pilot is
not, by itself, a migration of that store. Until a reviewed pilot wires a
numeric DBI and records its migration decision, every physical snapshot below
is explicitly **pre-numeric**.

## Decision rule

An artifact is one of the following, and the label must be kept with the
artifact or receipt:

| Label | Meaning |
|---|---|
| `RETAIN_LOGICAL` | The fixture contains logical IDs, text, qrels, or digests and has no MDBX physical-key assertion. Keep it byte-for-byte; do not regenerate a quality metric merely because storage changed. |
| `REGENERATE_POST_NUMERIC` | The fixture hashes or compares MDBX keys/rows, or its reopen result depends on the physical DBI. Re-run it against the exact post-migration commit and record the new source/fixture hashes. |
| `HISTORICAL_ONLY` | The evidence remains useful as a dated experiment, but must not be presented as post-migration compatibility or release evidence. |
| `REJECT_OLD_OR_MIGRATE` | An old database is an input to an upgrade gate. The pilot must explicitly choose `migration`, `rebuild`, or `reject-old`; a reader must not reinterpret old bytes as the new format. |

The absence of a migration tool is not permission to silently reinterpret a
legacy key. In the pre-release repository, a clean rebuild or an explicit
reject-old result is acceptable, but the choice belongs in the pilot's schema
manifest and receipt.

## Inventory

| Artifact | Current evidence | Decision after numeric-key migration |
|---|---|---|
| `tests/eval/fixtures/*.json` | Evaluation corpora, queries, judgments and embedding artifacts; no MDBX keys | `RETAIN_LOGICAL`. Keep content hashes and quality thresholds unchanged. |
| `tools/lexical-research/fixtures/*.json`, `*.tsv` | Logical document/query IDs and tokenizer/retrieval contracts | `RETAIN_LOGICAL`. These IDs are not storage identities. |
| `tests/infrastructure/mdbx/mdbx_knowledge_unit_store_test.cpp` | Legacy rows are inserted under decimal text keys and several assertions inspect the old key order | `REGENERATE_POST_NUMERIC` plus a named legacy negative fixture. Preserve the old reader/reject test; add numeric point/range/order and reopen assertions in the pilot. |
| `tests/infrastructure/mdbx/exact_generation_fixture.hpp` | `snapshot()` hashes logical DBI names and raw key/value bytes, excluding only the exact-generation DBI | `REGENERATE_POST_NUMERIC`. The helper remains useful, but every stored snapshot and receipt hash changes when a key codec changes. Compare logical decoded rows separately from raw-byte hashes. |
| `tools/run-exact-generation-upgrade.py` and packaging `exact-generation-upgrade` | The fail-closed runner owned by #659 pins historical OLD to `52004324bc0d2b07c3657245195db349dd391774` and requires `--allow-pre-numeric-baseline`; other historical OLD commits are rejected | `HISTORICAL_ONLY` regardless of the selected NEW commit. `--post-numeric-upgrade` records `NOT_EXECUTED` with `post_migration_upgrade_proof = false`; selecting an arbitrary OLD/NEW pair cannot establish numeric migration proof. |
| MDBX reopen tests (`tests/infrastructure/mdbx/mdbx_*test.cpp`) | Most tests assert value-level identity and reopen; some helpers inspect physical rows or decimal keys | `RETAIN_LOGICAL` for value/revision assertions; `REGENERATE_POST_NUMERIC` for key-byte/order assertions. Add a fresh-process reopen to the numeric DBI gate. |
| `guides/technical-preview-preflight.md`, `guides/markdown-technical-preview.md`, `guides/markdown-technical-preview-release-candidate.md` | Frozen #607/#611 candidate and installed-consumer evidence | `HISTORICAL_ONLY`. The bounded Markdown semantics remain a useful contract, but the candidate is not release or migration evidence after numeric-key integration. |
| `guides/experiments/2026-11-16-release-decision-report.md` | Release decision and CI receipts for a pre-numeric candidate | `HISTORICAL_ONLY`. Preserve the exact SHAs and verdict; re-run the decision matrix at the post-migration HEAD before publication. |
| `guides/experiments/pre-release-ann-r4-compatibility-audit.md` | Commit-based ANN/R4 compatibility study and exact-generation follow-up | `HISTORICAL_ONLY` for physical reopen/upgrade claims. R4 quality numbers are not invalidated, but no numeric-key compatibility follows from them. |
| `guides/experiments/semcs-memory-substrate-compatibility-audit.md` | Graph-free/semantic-substrate compatibility analysis | `HISTORICAL_ONLY` for its preview baseline. Logical provenance conclusions remain applicable; physical fixtures require the post-numeric replay. |
| `guides/storage-identity-mapping.md` | Normative opt-in mapping policy (stable IDs, no reuse, binary codec) | `NORMATIVE_PILOT_INPUT`. It is not evidence that canonical stores have migrated. |

## Required post-migration gate

The numeric pilot is complete only when the following observations are recorded
on one exact commit (and on every changed DBI, not merely the unit table):

1. A fresh database writes a non-zero numeric key, reopens in a new process,
   and resolves the same logical identity and revision.
2. Numeric ordering and any prefix/range operation are tested with values such
   as `2`, `10`, and the 32/64-bit boundary cases. Native
   `MDBX_INTEGERKEY` and bytewise composite keys are tested separately.
3. A failed write aborts both primary and mapping/index rows; a read-only
   lookup never allocates an ID; mappings are not reused after retirement.
4. An old database is exercised through the declared `migration`, `rebuild`,
   or `reject-old` path. If `reject-old` is selected, the failure is typed and
   occurs before a partial write.
5. The post-migration exact-generation/installed-consumer fixture is rebuilt
   from the selected OLD/NEW commits. Its receipt compares decoded logical
   snapshots and records raw-byte changes as expected, rather than demanding
   old physical key bytes.
6. The bounded technical-preview consumer is rerun with the same source
   revisions, stale controls, CAS controls, and table-prefix isolation. A
   green pre-numeric receipt cannot substitute for this run.

The acceptance result must name the selected DBI schema version, key codec and
comparator, migration decision, fixture source hash, receipt hash, exact HEAD,
CI/packaging run, and any intentionally obsolete evidence. No tag, production
activation, or broad release claim follows from this register.

## What remains valid now

The logical guarantees already demonstrated by the historical fixtures remain
valuable: exact source revisions, digest checks, stale rejection, scope
isolation, bounded reads, and no substitution of a newer body for a historical
one. Numeric physical keys must preserve those observations. A reduction in
`KeyValueTable<std::string, std::string>` usage is not, on its own, evidence
that the architecture or the preview has improved.
