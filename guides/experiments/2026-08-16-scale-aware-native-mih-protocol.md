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

Each candidate is measured with the legacy sorted/lower-bound plus two-pass
generation-array pipeline and the flat/open-address plus streaming-generation
dedup challenger. The challenger must preserve candidate order and Hamming
top-K identity; the native self-test establishes that invariant before the
matrix is allowed to run.

Binary HNSW receives the same calibration-only per-scale selection privilege:
`M ∈ {16, 24, 32}` and `efSearch ∈ {256, 512, 768, 1024}`, with fixed
`efConstruction=200` and seed 20260815. Flat is evaluated as its exact binary
candidate baseline. No quality or latency result on French data may alter any
of these choices.

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
the same quality and memory gates, by candidate-generator p50 then cascade p50,
resident bytes, and a stable identifier. Freeze the resulting configurations,
then execute one French confirmation per family without retuning.

## Limits And Follow-up

- The protocol does not claim that `m≈256/log2(N)` is a production optimum; it
  is only the reason the scale-specific grids are centered below frozen `m19`.
- Stage timers are diagnostic components. Candidate-generator total and cascade
  total are the latency measures; overlapping streaming traversal/dedup timers
  must not be added.
- Learned MIH, coarse locator codes, and radius-policy learning remain out of
  scope until this full-code native baseline is resolved.
