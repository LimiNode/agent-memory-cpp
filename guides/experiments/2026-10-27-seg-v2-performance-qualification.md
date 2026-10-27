# 2026-10-27 `flat-v2` / `seg-v2` performance qualification

Status: `HARNESS_ACCEPTED / PERFORMANCE_INCONCLUSIVE / PRODUCTION_ACTIVATION_BLOCKED`.
This is a separate research
PR after the experimental `seg-v2` adapter was integrated; it does not change
the production default or coordinator activation.

## Question

Does the segmented adapter retain its bounded point-update work while its
snapshot/reopen and storage costs remain acceptable on larger, more varied
corpora than the isolated sparse fixture used by PR #559?

## Setup

- Target: `main` after PR #559, branch `feat/seg-v2-performance-qualification`.
- Runner: `tools/run-segmented-qualification.py`; each workload runs in an
  isolated subprocess with a 120-second runtime budget.
- Corpus generator: `tools/generate-segmented-qualification-corpus.py`.
- Corpus kind: synthetic deterministic UTF-8; it is not a production knowledge
  corpus and must not be presented as one.
- Seed: `12648430`; workloads: `unique+anchor`, `shared-hot`,
  `isolated-sparse-replacement`, `zipf-like`, `mixed-utf8`, `long-document`,
  `small-insert`, `small-delete`, `inactive-history`, and `no-op`.
- Requested scales: 128, 512, 2048, 8192, 32768 units. The local run executed
  128/512 with three repeats and recorded 2048/8192/32768 as controlled stops
  because the expanded inactive-history workload exceeded the local time
  budget. The earlier six-workload smoke run reached 2048, but it is retained
  only as directional context and is not mixed into this receipt.
- Backends: `flat-reference`, `flat-v2`, `segmented-v1` where applicable, and
  `segmented-v2`.
- Raw artifact: `2026-10-27-seg-v2-qualification.raw.tsv`; its SHA-256 and
  receipt are recorded in the adjacent JSON file.

The benchmark checks final active-unit count, non-empty token statistics,
reopen stability, and equality of logical final state across backends. The
validator rejects duplicate/incomplete rows, non-finite values, altered raw
hashes, falsified p50 aggregates, and missing repeats.

## Selected p50 observations

Values are milliseconds except database bytes and logical counters. These are
directional local measurements, not cross-machine performance claims.

| Workload / units | Backend | write | snapshot | reopen snapshot | point update | DB bytes | update range rows | decoded postings |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| shared-hot / 512 | flat-v2 | 995.208 | 2.060 | 1.884 | 3.332 | 16,777,216 | 0 | 0 |
| shared-hot / 512 | seg-v2 | 1,808.01 | 5.095 | 5.440 | 5.911 | 16,777,216 | 16 | 1,024 |
| isolated-sparse / 512 | flat-v2 | 1,464.83 | 3.540 | 3.554 | 5.481 | 16,777,216 | 0 | 0 |
| isolated-sparse / 512 | seg-v2 | 3,226.66 | 17.036 | 16.596 | 10.628 | 16,777,216 | 4 | 2 |
| long-document / 512 | flat-v2 | 8,887.03 | 14.648 | 14.016 | 33.188 | 16,777,216 | 0 | 0 |
| long-document / 512 | seg-v2 | 1,997.50 | 17.944 | 14.970 | 7.259 | 16,777,216 | 16 | 1,024 |
| inactive-history / 512 | flat-v2 | 1,386.85 | 2.862 | 2.892 | 4.337 | 16,777,216 | 0 | 0 |
| inactive-history / 512 | seg-v2 | 2,316.29 | 11.226 | 10.804 | 4.908 | 16,777,216 | 12 | 514 |
| no-op / 512 (per operation) | flat-v2 | 1,260.76 | 2.550 | 2.795 | 45.089 | 16,777,216 | 0 | 0 |
| no-op / 512 (per operation) | seg-v2 | 2,201.05 | 7.863 | 7.911 | 2.292 | 16,777,216 | 0 | 0 |

The segmented point update remains bounded for the isolated workload: decoded
postings stay at 2 and range rows at 4 at both executed scales. The expanded
long-document case shows a useful point-update advantage at 512 units, while
shared-hot and inactive-history remain slower than flat-v2. The same run shows
a snapshot/reopen and fixed-environment footprint trade-off, so there is no
uniform backend winner.

Snapshot timings exclude oracle validation, probe reads, and stale-state
checks. The no-op row reports a 16-operation batch separately; `point update`
is the per-operation value. Historical preparation is reported separately from
initial ingestion. Numerical TokenId parity is reported as a diagnostic: IDs
are stable within each backend and across reopen, but allocation order is not a
cross-backend contract.

## Interpretation and decision gate

The result is `INCONCLUSIVE`, not a production `GO`. It supports a controlled
pilot of the segmented point-update path only after representative documents,
hot partitions, long-document replacement, and larger scales are measured.
No production activation, default change, or database migration is authorized
by this note.

Logical amplification is represented by update range rows and decoded postings.
Storage growth is represented by the before/after MDBX file-size fields. MDBX
physical write amplification and process RSS high-water are explicitly
`unavailable` in this harness; equal logical rows or final file size must not be
described as physical I/O equivalence.

## Limitations and follow-up

- The corpus is synthetic and deterministic; real technical documents and
  multilingual knowledge units remain untested.
- 8192 and 32768 were controlled stops, not successful measurements.
- 2048 was also recorded as `SKIPPED_BY_CONFIG_LIMIT` in the receipt; this is a
  configuration stop, not evidence of a runtime timeout.
- Three repeats are useful for smoke qualification but do not establish stable
  machine-independent timing. A future pilot must record hardware, MDBX
  configuration, cache order, warm-up policy, and more repeats.
- Add explicit long-document and shared-hot fan-out cases, process RSS sampling,
  and physical I/O counters if the storage platform exposes them.
- Keep `seg-v2` experimental and compare it with `flat-v2` before any
  coordinator or production-default decision.
