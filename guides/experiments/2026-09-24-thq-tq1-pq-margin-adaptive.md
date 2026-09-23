# THQ4 → TQ1 → small residual PQ and margin-adaptive correction

Date: 2026-09-24  
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
The PQ fit is a deterministic, chunked Lloyd pilot on 1,024 train vectors
(four iterations, 256 centroids per subspace); it is intentionally bounded
and is not a converged production fit.

| arm | side touched | mean K | mean correction docs | mean nDCG@10 | teacher top-10 overlap |
| --- | ---: | ---: | ---: | ---: | ---: |
| TQ1 filter only | 52 B | 0 | 0 | 0.654117 | 0.897368 |
| TQ1 + PQ4, K=32 | 56 B | 32 | 32 | 0.659016 | 0.902632 |
| TQ1 + PQ4, K=64 | 56 B | 64 | 64 | 0.659016 | 0.902632 |
| TQ1 + PQ4, K=128 | 56 B | 128 | 128 | 0.659016 | 0.902632 |
| TQ1 + PQ4, adaptive | 56 B | 77.47 | 77.47 | 0.659016 | 0.902632 |
| TQ1 + PQ8, K=32 | 60 B | 32 | 32 | 0.655589 | 0.903947 |
| TQ1 + PQ8, K=64 | 60 B | 64 | 64 | 0.655589 | 0.903947 |
| TQ1 + PQ8, K=128 | 60 B | 128 | 128 | 0.655589 | 0.903947 |
| TQ1 + PQ8, adaptive | 60 B | 77.47 | 77.47 | 0.655589 | 0.903947 |

The fixed-K and adaptive arms have identical top-10 quality within each PQ
family on this bounded replay: the correction changes scores but does not
change the final top-10 beyond the K=32 boundary. Consequently the adaptive
policy does not yet demonstrate a quality/latency advantage. The PQ4 pilot is
the stronger point (`0.659016` at 56 B touched side), but it is not a
production selection because its fit is deliberately underpowered.

## Evidence and limits

The runner is `run-thq-tq1-pq-residual-gate.py`. The independent audit
`audit-thq-tq1-pq-residual-gate.py` replays source hashes, THQ top-128
selection, THQ base reconstruction, TQ1 decoding, PQ code assignment, all
760 rows (152 queries × 5 arms), and final top-10 IDs. Both PQ4 and PQ8
audits report `PASS`, `source_replay: true`, zero TQ1 decode error, exact PQ
code parity, and exact top-10 replay over 18,362 unique THQ documents.

This is a source-bound NumPy reference gate, not native timing, page/TLB
measurement, or a wire-format commitment. It does not test ranking-aware
MA-LSQ training: that is a separate research line requiring an independent
query-training fold and must not be conflated with this unsupervised
margin-policy diagnostic. No production codec choice is made here.

## Conditional MA-LSQ pilot (separate diagnostic)

To avoid silently calling the hybrid a ranking-aware codec, a separate
held-out pilot (`run-thq-ma-lsq-ranking-pilot.py`) learned one global residual
scale for the existing LSQ32/LSQ48 codebooks. The first 76 queries were used
only for qrels pairwise hinge selection; the last 76 were untouched during
selection. The best scale was still at the upper edge of the deliberately
small grid (`alpha = 2.0`): held-out nDCG was `0.667203` for LSQ32 and
`0.637276` for LSQ48. Because the optimum is on the grid boundary and this is
only one scalar per payload, these numbers are a diagnostic signal, not a
validated MA-LSQ result. A real ranking-aware additive codec needs a wider
hyperparameter protocol, multiple query folds/seeds, and codebook/stage
training independent of the 152-query evaluation set.
