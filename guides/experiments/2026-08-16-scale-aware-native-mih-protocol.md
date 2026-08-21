# Scale-Aware Native MIH Protocol

Date: 2026-08-16

Status: predeclared; no calibration or confirmation result is included here.

Contract: `tools/agent-memory-bench/scale-aware-native-mih-protocol.example.json`

## Question

Does the calibration-selected MIH substring count move downward as corpus size
grows, once native lookup and candidate handling are measured, and does the
best per-scale MIH configuration remain competitive with equally calibrated
binary HNSW and Flat baselines?

The frozen `m19` result on German data is not reused for selection. It remains
evidence about transfer of that frozen representative only.

## Predeclared Setup

Spanish MIRACL development data is reserved for calibration only. French MIRACL
development data is unavailable to selection and will be materialized only
after all Spanish choices are frozen. Both materializations will pin the E5
model and MIRACL source revisions and add their manifest SHA-256 values before
execution; no result is eligible without those frozen manifests.

The Spanish scale corpora are nested (`25k ⊂ 100k ⊂ 1M`): a deterministic
ascending SHA-256 ordering of the seeded document identifier supplies the first
25,000, 100,000 and 1,000,000 documents. ITQ-256 is trained once on the
declared 25,000 Spanish calibration-train documents and that exact artifact is
reused unchanged at every retrieval scale. Thus the scale sweep changes corpus
size rather than document sampling or representation training.

Every MIH treatment keeps ITQ-256 seed 52, 50 iterations, Hamming top-768,
binary ADC top-256, exact rerank top-256, and the exact fixed-r56 inclusion
condition:

```text
sum(local_radius[b] + 1) = 57
```

The only MIH configuration dimension is near-equal-band `m`, with the
deterministic minimum-enumeration radius allocation. This is deliberately not
the obsolete rule "all bands have local radius two".

| Calibration scale | Predeclared `m` candidates |
| --- | --- |
| 25k | 15–21 |
| 100k | 13–19 |
| 1M | 10–16, subject to exact-probe preflight |

Each candidate is measured with the complete 2×2 directory × deduplication
matrix: sorted/lower-bound or flat/open-address directory, crossed with
two-pass or streaming generation-array deduplication. This separates the
directory and traversal effects rather than treating one diagonal comparison
as their individual causal attribution. Every combination must preserve
candidate order and Hamming top-K identity; the native self-test establishes
that invariant before the matrix is allowed to run.

Binary HNSW receives the same calibration-only per-scale selection privilege:
`M ∈ {16, 24, 32}` and `efSearch ∈ {768, 1024}`, with fixed
`efConstruction=200` and seed 20260815. Flat is evaluated as its exact binary
candidate baseline. `efSearch` is never below the frozen Hamming candidate
depth of 768, so every predeclared HNSW row is executable. No quality or
latency result on French data may alter any of these choices.

## Exact-Probe Feasibility Gate

`preflight-scale-aware-native-mih.py` calculates the combinatorial local-key
count before any external materialization or native execution. It does not cap
or truncate probing: a treatment is either exact-r56 or explicitly excluded
before execution. The initial protocol therefore excludes `m10` and `m11` at
1M because their 578,376 and 193,375 local keys exceed the predeclared 100,000
per-query ceiling; `m12` remains admissible at 74,867 keys and `m13` at 37,148.

The ceiling is a feasibility decision, not a latency result or a quality-based
selection. The preflight report is retained with the future calibration
evidence so excluded treatments cannot silently disappear.

## Measurements And Decision

For every admissible row retain p50/p95/p99 for key enumeration, bucket lookup,
posting traversal, deduplication, Hamming scoring, top-K, ADC,
candidate-generator total, cascade total, and logical index bytes. Also retain
non-empty/empty probes, mean/p95 touched posting lengths, posting visits,
unique candidates, candidate fraction, and unique candidates per posting visit.

Within each scale, choose MIH and HNSW only from calibration rows satisfying
all of these predeclared gates: ADC-oracle bootstrap LB95 ≥ 0.90, nDCG
retention bootstrap LB95 ≥ 0.98, and auxiliary logical index bytes/document
≤ 256.  The frozen machine-contract field is named
`auxiliary_resident_bytes_per_document` for compatibility, but its measured
value is `backend_index_logical_bytes / document_count`; allocator and process
RSS footprint were not a gate.
The bootstrap uses 10,000 replicates, confidence 0.95 and deterministic metric
seeds derived from base seed 20260827.
Auxiliary logical index bytes include backend-specific immutable index structures
(directory keys/slots, offsets, postings or HNSW graph) but exclude the shared
32-byte binary-code store and transient query scratch. This normalized memory
gate is intentionally scale-independent; it replaces the small-corpus absolute
byte gate from #149. Choose by candidate-generator p50, then cascade p50,
logical index bytes and a stable identifier. Freeze the resulting configurations,
then execute one French confirmation per family without retuning.

## Limits And Follow-up

- The protocol does not claim that `m≈256/log2(N)` is a production optimum; it
  is only the reason the scale-specific grids are centered below frozen `m19`.
- Stage timers are diagnostic components. Candidate-generator total and cascade
  total are the latency measures; overlapping streaming traversal/dedup timers
  must not be added.
- Learned MIH, coarse locator codes, and radius-policy learning remain out of
  scope until this full-code native baseline is resolved.

## Calibration Result (2026-08-19)

Status: completed calibration-only sweep; French confirmation was not run.

Measured source: PR #155, commit `f9aa210` (with the HNSW-enabled native
benchmark build). Each scale's `results/result.json` binds the protocol, input
manifest, frozen ITQ artifact, native reports, shortlists, quality reports, and
bootstrap gates.

Evidence is retained for review in the superseding draft release
[`[Evidence] Scale-aware native MIH calibration v2`](https://github.com/LimiNode/agent-memory-cpp/releases/tag/untagged-3eb83b047d77048f9e8a).
Its single ZIP has SHA-256
`b0651dda1f9d8f632162c1ee811047f7e0ca76df8d3aeab8b392929b58993c60` and
bundle-root SHA-256
`f7841d210bd5a194bc584c2cae65a647822c02e82e27c3e71458b9770ae4cebd`.
The archive contains the protocol/preflight, ITQ artifact, per-scale result,
input and E5 manifests, configs, native reports, quality reports, oracle
caches, contributions, and measured-source snapshots. The approximately 1 GB
shortlist JSON payload is omitted by design; every included quality report
binds its shortlist by SHA-256.  The v2 packager independently derives logical
index bytes and latency from each bound native report, replays every bootstrap
and memory gate from the included artifacts, and reproduces deterministic
per-backend selection.  It supersedes v1 without replaying or changing the
measured matrix.

The shared ITQ-256 artifact was trained once from the same 25,000 Spanish
training documents at every scale.  Its v2 identity binds ordered training
IDs, training-vector SHA-256, embedding identity, and dimension.  It does
not bind the scale-specific evaluation manifest.  This correction is required
for a single training-side artifact to be validly reused across nested
evaluation corpora.

### Completion and Gate Outcomes

| Scale | Completed rows | Admissible MIH rows | Selected MIH | Selected Flat | Selected HNSW |
| --- | ---: | ---: | --- | --- | --- |
| 25k | 35 / 35 | 7 | `m21`, flat directory, two-pass dedup | `binary-flat-256` | `m16`, `efSearch=768` |
| 100k | 35 / 35 | 0 | none | `binary-flat-256` | `m16`, `efSearch=768` |
| 1M | 27 / 27 | 0 | none | `binary-flat-256` | `m24`, `efSearch=1024` |

The 1M protocol has 27 rows because exact-probe preflight excluded `m10` and
`m11`; its five admissible-to-run MIH values (`m12` through `m16`) produce
20 MIH rows, plus one Flat and six HNSW rows.  No row was silently omitted.

`candidate-generator p50` is the selection objective.  The selected rows and
their gate statistics are:

| Scale | Backend | Candidate-generator p50 | Cascade p50 | ADC-oracle LB95 | nDCG retention LB95 | Auxiliary logical index bytes/doc |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 25k | MIH `m21` flat/two-pass | 0.3105 ms | 0.4952 ms | 0.9551 | 0.9833 | 165.40 |
| 25k | Flat | 0.4488 ms | 0.6411 ms | 0.9918 | 1.0000 | 0.00 |
| 25k | HNSW `M16/ef768` | 0.7688 ms | 0.9684 ms | 0.9909 | 1.0000 | 180.65 |
| 100k | Flat | 1.6479 ms | 1.8853 ms | 0.9778 | 0.9944 | 0.00 |
| 100k | HNSW `M16/ef768` | 1.0318 ms | 1.2572 ms | 0.9759 | 0.9936 | 180.60 |
| 1M | Flat | 15.5583 ms | 15.8819 ms | 0.9403 | 0.9842 | 0.00 |
| 1M | HNSW `M24/ef1024` | 3.3344 ms | 3.5719 ms | 0.9353 | 0.9819 | 244.35 |

### MIH Interpretation

This sweep supports the scale-mismatch diagnosis rather than identifying a
stable full-code MIH winner.  At 25k, the selected `m21` row is faster than
Flat under the frozen quality and logical-index-memory gates.  At 100k, the
fastest MIH row is `m19` with the flat
directory and two-pass deduplication (0.6961 ms candidate-generator p50), but
its nDCG-retention LB95 is 0.9710, below the predeclared 0.98 gate.  No 100k
MIH row passes all gates.

At 1M, the most quality-preserving measured MIH region is around `m16`, but
the flat/two-pass `m16` row reaches only ADC-oracle LB95 0.8818 and nDCG
retention LB95 0.9451.  Its candidate-generator p50 is 6.4279 ms.  Moving to
smaller `m` does not restore the gate: `m12` reaches 0.7847 / 0.8997 for the
same two quality bounds.  Thus this is not a selection tie that French data
could resolve; there is no eligible MIH configuration to freeze at either
100k or 1M.

The retained native counters are consistent with the loss of selectivity:

| Scale and representative | Bucket probes/query | Posting visits/query | Unique candidates/query | Candidate fraction |
| --- | ---: | ---: | ---: | ---: |
| 25k, MIH `m21` flat/two-pass | 1,267 | 8,331 | 6,988 | 27.95% |
| 100k, MIH `m19` flat/two-pass | 1,874 | 18,455 | 16,649 | 16.65% |
| 1M, MIH `m16` flat/two-pass | 7,232 | 120,880 | 112,911 | 11.29% |

Candidate fraction alone is therefore not a quality proxy: the 1M schedule
still sends over one hundred thousand candidates per query into the fixed
top-768 Hamming stage, while the quality gates remain below threshold.

### Consequence and Follow-up

Do not run the planned French confirmatory comparison for MIH from this
calibration.  It would be a post-hoc choice without an admissible Spanish MIH
configuration.  The valid conclusion is narrower: under the current full
ITQ-256, fixed-r56 exact-inclusion, top-768/top-256 cascade, and 256 bytes/doc
auxiliary-logical-index-memory contract, native arbitrary-`m` MIH is admissible at 25k but
not at 100k or 1M.

Flat and HNSW have admissible per-scale calibration rows, but this experiment
was predeclared as a three-family confirmation pipeline.  Any standalone
French Flat/HNSW confirmation, a relaxed MIH quality contract, a coarse
locator code, or a learned/radius-policy change needs a new protocol and must
not be selected from these results.
