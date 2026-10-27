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
- Runner: `tools/run-segmented-qualification.py`; each
  `(scale, workload, backend, repeat)` case runs in an isolated subprocess
  with a 120-second runtime budget. A timeout disposition applies only to the
  case whose process actually exceeded the budget.
- Corpus generator: `tools/generate-segmented-qualification-corpus.py`.
- Corpus kind: synthetic deterministic UTF-8; it is not a production knowledge
  corpus and must not be presented as one.
- Seed: `12648430`; workloads: `unique+anchor`, `shared-hot`,
  `isolated-sparse-replacement`, `zipf-like`, `mixed-utf8`, `long-document`,
  `small-insert`, `small-delete`, `inactive-history`, and `no-op`.
- Requested scales: 128, 512, 2048, 8192, 32768 units. Scales above the
  configured `--max-units` are represented as explicit
  `SKIPPED_BY_CONFIG_LIMIT` case outcomes; they are not described as runtime
  timeouts. Runtime stops, when present, identify the single started case and
  its process termination evidence.
- Backends: `flat-reference`, `flat-v2`, `segmented-v1` where applicable, and
  `segmented-v2`.
- Raw artifact: `2026-10-27-seg-v2-qualification.raw.tsv`; its SHA-256 and
  receipt are recorded in the adjacent JSON file.
- Current canonical-LF artifact digests (recompute after any receipt edit):
  raw `c5a181ac1c102e7ceebb7d14ac10c54a840a5ce513cb36328dad043fb275b2be`,
  receipt `74de1fa5d91ab0490882c953f9d5de661cd58a12b3c6d53978d0a9e0f23fde2c`.
  CI replay `40559183012` verified these bytes from its clean checkout on
  commit `e86a93f5`.

The benchmark checks final active-unit count, non-empty token statistics,
reopen stability (including direct token-to-ID dictionary equality within a
backend), and equality of logical final state across backends. The validator
rejects duplicate/incomplete case outcomes, non-finite values, fractional
physical counters, altered raw hashes, falsified p50 aggregates, and missing
repeats. No-op timings report the complete 16-operation backend batch and its
per-operation mean separately.

## Selected p50 observations

Values are milliseconds except database bytes and logical counters. These are
directional local measurements, not cross-machine performance claims.

| Workload / units | Backend | write | snapshot | reopen snapshot | point update | DB bytes | update range rows | decoded postings |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| shared-hot / 512 | flat-v2 | 1,074.72 | 1.998 | 2.189 | 3.557 | 16,777,216 | 0 | 0 |
| shared-hot / 512 | seg-v2 | 2,225.68 | 5.374 | 5.979 | 6.981 | 16,777,216 | 16 | 1,024 |
| isolated-sparse / 512 | flat-v2 | 2,220.51 | 3.789 | 3.680 | 7.623 | 16,777,216 | 0 | 0 |
| isolated-sparse / 512 | seg-v2 | 6,794.90 | 15.883 | 17.100 | 16.710 | 16,777,216 | 4 | 2 |
| long-document / 512 | flat-v2 | 11,985.30 | 15.926 | 14.928 | 77.449 | 16,777,216 | 0 | 0 |
| long-document / 512 | seg-v2 | 5,482.49 | 19.062 | 20.034 | 7.333 | 16,777,216 | 16 | 1,024 |
| inactive-history / 512 | flat-v2 | 3,401.00 | 2.864 | 3.071 | 4.124 | 16,777,216 | 0 | 0 |
| inactive-history / 512 | seg-v2 | 8,685.44 | 11.230 | 11.863 | 6.995 | 16,777,216 | 12 | 514 |
| no-op / 512 (per operation) | flat-v2 | 2,509.37 | 2.601 | 2.771 | 2.900 | 16,777,216 | 0 | 0 |
| no-op / 512 (per operation) | seg-v2 | 5,691.23 | 7.753 | 7.579 | 0.154 | 16,777,216 | 0 | 0 |

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
- Scales above `--max-units` are configuration stops, not successful
  measurements and not evidence of runtime timeout.
- Three repeats are useful for smoke qualification but do not establish stable
  machine-independent timing. A future pilot must record hardware, MDBX
  configuration, cache order, warm-up policy, and more repeats.
- Add explicit long-document and shared-hot fan-out cases, process RSS sampling,
  and physical I/O counters if the storage platform exposes them.
- Keep `seg-v2` experimental and compare it with `flat-v2` before any
  coordinator or production-default decision.
