# 2026-10-27 `flat-v2` / `seg-v2` performance qualification

Status: `INCONCLUSIVE` for production selection. This is a separate research
PR after the experimental `seg-v2` adapter was integrated; it does not change
the production default or coordinator activation.

## Question

Does the segmented adapter retain its bounded point-update work while its
snapshot/reopen and storage costs remain acceptable on larger, more varied
corpora than the isolated sparse fixture used by PR #559?

## Setup

- Target: `main` after PR #559, branch `feat/seg-v2-performance-qualification`.
- Runner: `tools/run-segmented-qualification.py`.
- Corpus generator: `tools/generate-segmented-qualification-corpus.py`.
- Corpus kind: synthetic deterministic UTF-8; it is not a production knowledge
  corpus and must not be presented as one.
- Seed: `12648430`; workloads: `unique+anchor`, `shared-hot`,
  `isolated-sparse-replacement`, `zipf-like`, `mixed-utf8`, and `no-op`.
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
| shared-hot / 128 | flat-v2 | 116.013 | 0.682 | 1.258 | 1.351 | 65,536 | 0 | 0 |
| shared-hot / 128 | seg-v2 | 221.318 | 1.480 | 1.773 | 2.201 | 16,777,216 | 4 | 256 |
| shared-hot / 512 | flat-v2 | 941.249 | 2.471 | 4.529 | 3.032 | 16,777,216 | 0 | 0 |
| shared-hot / 512 | seg-v2 | 1,746.08 | 5.542 | 5.771 | 6.117 | 16,777,216 | 16 | 1,024 |
| isolated-sparse / 128 | flat-v2 | 148.796 | 1.266 | 2.250 | 1.767 | 65,536 | 0 | 0 |
| isolated-sparse / 128 | seg-v2 | 725.168 | 4.235 | 4.760 | 6.127 | 16,777,216 | 4 | 2 |
| isolated-sparse / 512 | flat-v2 | 1,516.72 | 5.593 | 8.896 | 5.434 | 16,777,216 | 0 | 0 |
| isolated-sparse / 512 | seg-v2 | 3,125.34 | 16.869 | 17.627 | 11.169 | 16,777,216 | 4 | 2 |
| long-document / 512 | flat-v2 | 9,335.35 | 18.146 | 31.218 | 40.075 | 16,777,216 | 0 | 0 |
| long-document / 512 | seg-v2 | 2,036.56 | 18.702 | 15.063 | 6.779 | 16,777,216 | 16 | 1,024 |
| inactive-history / 512 | flat-v2 | 1,240.1 | 3.754 | 7.141 | 4.218 | 16,777,216 | 0 | 0 |
| inactive-history / 512 | seg-v2 | 2,237.2 | 11.223 | 11.352 | 4.814 | 16,777,216 | 12 | 514 |

The segmented point update remains bounded for the isolated workload: decoded
postings stay at 2 and range rows at 4 at both executed scales. The expanded
long-document case shows a useful point-update advantage at 512 units, while
shared-hot and inactive-history remain slower than flat-v2. The same run shows
a snapshot/reopen and fixed-environment footprint trade-off, so there is no
uniform backend winner.

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
- Three repeats are useful for smoke qualification but do not establish stable
  machine-independent timing. A future pilot must record hardware, MDBX
  configuration, cache order, warm-up policy, and more repeats.
- Add explicit long-document and shared-hot fan-out cases, process RSS sampling,
  and physical I/O counters if the storage platform exposes them.
- Keep `seg-v2` experimental and compare it with `flat-v2` before any
  coordinator or production-default decision.
