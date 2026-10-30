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
- Corpus parameters are pinned in the receipt: overlap `32`, long-document
  token count `64`, and inactive-token history `64`. The validator regenerates
  every declared corpus within a bounded unit limit, checks the generator
  source digest, and compares the resulting manifest and corpus SHA-256.
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
  raw `620ab431274790293dabee48d3ab390171f176f4399ec4358b9a366590f05071`,
  receipt `242373dade90aff2aa0a5abace0a543e54cf40092a5c61d7e498ef28a0c6cd5c`.
  These artifacts use schema version 3 and record explicit configuration,
  provenance, terminal status, and stop evidence. Empty raw output is valid
  only for an explicitly `PARTIAL`/`FAILED` diagnostic receipt whose cases
  all stopped before producing measurements. Exact-head CI replay is required
  for this corrective pass and is recorded in the PR after push.

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
| shared-hot / 512 | flat-v2 | 1,071.53 | 2.042 | 2.111 | 3.651 | 16,777,216 | 0 | 0 |
| shared-hot / 512 | seg-v2 | 1,816.76 | 5.327 | 5.642 | 6.275 | 16,777,216 | 16 | 1,024 |
| isolated-sparse / 512 | flat-v2 | 1,657.75 | 3.564 | 3.654 | 5.578 | 16,777,216 | 0 | 0 |
| isolated-sparse / 512 | seg-v2 | 3,170.18 | 16.136 | 16.981 | 10.726 | 16,777,216 | 4 | 2 |
| long-document / 512 | flat-v2 | 10,183.80 | 15.741 | 14.759 | 37.266 | 16,777,216 | 0 | 0 |
| long-document / 512 | seg-v2 | 2,144.38 | 19.210 | 19.253 | 7.811 | 16,777,216 | 16 | 1,024 |
| inactive-history / 512 | flat-v2 | 1,384.86 | 2.854 | 3.033 | 4.072 | 16,777,216 | 0 | 0 |
| inactive-history / 512 | seg-v2 | 3,088.22 | 13.974 | 13.026 | 5.157 | 16,777,216 | 12 | 514 |
| no-op / 512 (per operation) | flat-v2 | 1,370.66 | 2.698 | 2.783 | 2.974 | 16,777,216 | 0 | 0 |
| no-op / 512 (per operation) | seg-v2 | 2,374.06 | 9.213 | 8.423 | 0.149 | 16,777,216 | 0 | 0 |

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

The update timer starts only after all deterministic replacement, insertion,
and expected-state inputs have been prepared. Oracle mutation, restoration of
the final logical corpus, and validation remain outside the backend mutation
interval.

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
