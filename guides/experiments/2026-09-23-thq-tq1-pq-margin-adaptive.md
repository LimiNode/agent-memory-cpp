# THQ4 → TQ1 → small residual PQ and margin-adaptive correction

Date: 2026-09-23
Status: `EXECUTED` / `BOUNDED_PILOT`

This gate tests the hybrid proposed in the review:

```text
frozen R4 shell (~5,000/query)
  → canonical THQ4 interval² top-128
  → TQ1 residual cosine scorer
  → optional 4- or 8-byte PQ residual correction
  → common FP32 cosine top-10
```

The three stages are kept separate. TQ1 is evaluated only for the THQ
top-128, and the residual PQ correction is decoded only for the selected
`K` documents after TQ1 (`K = 32, 64, 128`). The persisted payload is still
available for every document in the THQ shell; `touched_side_bytes` measures
what the arm reads, while `persisted_side_payload_bytes` measures what would
be stored. This is the requested `5000 → 128 → K → 10` protocol, not a
sequential THQ→TQ→PQ cascade over all 5,000 candidates.

The margin-adaptive arm is deliberately unsupervised. For each query it uses
the TQ1 score gaps between ranks 10 and 32/64 and global medians computed
without qrels:

```text
K=32 if gap(10,32) >= median_gap32
else K=64 if gap(10,64) >= median_gap64
else K=128
```

## Results

All rows use the same frozen candidate stream and cosine/qrels evaluator.
The canonical THQ/TQ1 stage is frozen from the source-bound 25k-train-row TQ1
payload. Only the second residual PQ is fit on 1,024 train vectors (four
iterations, 256 centroids per subspace); it is intentionally bounded and is
not a converged production fit.

| arm | side touched | mean K | mean correction docs | mean nDCG@10 | teacher top-10 overlap |
| --- | ---: | ---: | ---: | ---: | ---: |
| TQ1 filter only | 56 B persisted / 7,168 B/query touched | 0 | 0 | 0.659176 | 0.899342 |
| TQ1 + PQ4, K=32 | 64 B persisted / 7,424 B/query touched | 32 | 32 | 0.654856 | 0.898684 |
| TQ1 + PQ4, K=64 | 64 B persisted / 7,680 B/query touched | 64 | 64 | 0.654856 | 0.898684 |
| TQ1 + PQ4, K=128 | 64 B persisted / 8,192 B/query touched | 128 | 128 | 0.654856 | 0.898684 |
| TQ1 + PQ4, adaptive | 64 B persisted / 7,787.8 B/query touched | 77.47 | 77.47 | 0.654856 | 0.898684 |
| TQ1 + PQ8, K=32 | 68 B persisted / 7,552 B/query touched | 32 | 32 | 0.656326 | 0.905263 |
| TQ1 + PQ8, K=64 | 68 B persisted / 7,936 B/query touched | 64 | 64 | 0.656326 | 0.905263 |
| TQ1 + PQ8, K=128 | 68 B persisted / 8,704 B/query touched | 128 | 128 | 0.656326 | 0.905263 |
| TQ1 + PQ8, adaptive | 68 B persisted / 8,097.7 B/query touched | 77.47 | 77.47 | 0.656326 | 0.905263 |

The canonical TQ1 baseline is `0.659176`. Both PQ residual families reduce
quality on this bounded fit (`0.654856` for PQ4 and `0.656326` for PQ8), and
all K values are identical within each family. Thus the earlier apparent PQ4
gain was entirely explained by refitting the first THQ/TQ1 stage on only 1,024
rows; it was not an improvement over canonical TQ1. The margin policy does
not demonstrate a quality/latency advantage.

Serving accounting separates the raw code and norm sidecars. TQ1 alone is
`52 B` of code plus one `4 B` norm (`56 B/doc`). A persisted hybrid stores
the residual PQ code plus both the TQ1 and corrected-vector norms: `64 B/doc`
for PQ4 and `68 B/doc` for PQ8. The per-query touched bytes remain lower for
small K because the residual code and corrected norm are read only for the
selected documents.

## Evidence and limits

The runner is `run-thq-tq1-pq-residual-gate.py`. The independent audit
`audit-thq-tq1-pq-residual-gate.py` binds the canonical TQ1 payload, replays
the 25k centroid fit, deterministically refits the bounded PQ centroids,
replays PQ assignments, norm sidecars, all 760 rows (152 queries × 5 arms),
and final top-10 IDs. Both PQ4 and PQ8 audits report `PASS`,
`source_replay: true`, deterministic PQ-fit parity, artifact hash binding,
and exact top-10 replay over 18,362 unique THQ documents.

This is a source-bound NumPy reference gate, not native timing, page/TLB
measurement, or a wire-format commitment. It includes production-style norm
accounting but not a native direct scorer. It does not test ranking-aware
MA-LSQ training: that is a separate research line requiring an independent
query-training fold and must not be conflated with this unsupervised
margin-policy diagnostic. No production codec choice is made here.

## Conditional MA-LSQ pilot (separate diagnostic)

To avoid silently calling the hybrid a ranking-aware codec, a separate
held-out pilot (`run-thq-ma-lsq-ranking-pilot.py`) learned one global residual
scale for the existing LSQ32/LSQ48 codebooks. The first 76 queries were used
only for qrels pairwise hinge selection; the last 76 were untouched during
selection. The expanded train-fold-only grid was
`[0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 3.5, 4.0]` and selected
`alpha = 2.0` for both payloads. On the held-out half, the paired comparison
against the same-query `alpha = 1` baseline was:

| payload | baseline mean nDCG | selected mean nDCG | paired delta | bootstrap 95% CI | wins / ties / losses |
| --- | ---: | ---: | ---: | --- | ---: |
| LSQ32 | 0.660763 | 0.667203 | +0.006440 | [-0.006621, 0.020574] | 23 / 38 / 15 |
| LSQ48 | 0.661149 | 0.637276 | -0.023873 | [-0.046445, -0.005205] | 20 / 34 / 22 |

The LSQ32 point estimate is positive but its paired interval crosses zero;
the LSQ48 diagnostic is negative on this split. These are not validated
MA-LSQ results: this is only scalar residual calibration over fixed codebooks,
not ranking-aware codebook/stage training. A real MA-LSQ gate needs multiple
query folds/seeds and codebook/stage training independent of the 152-query
evaluation set. Bootstrap provenance is payload-specific: the runner records
seeds `20260955` for LSQ32 and `20260971` for LSQ48.
