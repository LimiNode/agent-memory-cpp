# 2026-08-11 Native CSR MIH hot-path baseline

## Question

How much native candidate-generation work do the two already observed ITQ-256
MIH operating points require before any query-adaptive probing heuristic is
introduced?

This establishes a C++ baseline for later weighted probing and data-aware band
experiments. It is not a production latency comparison with Python-reference
or external ANN implementations.

## Setup

The benchmark loads the frozen 22,607-document / 1,252-query ITQ-256 packed
code input used by the storage-layout experiment (seed 42, 50 ITQ iterations).
It deterministically selects 128 queries with seed `20260811`, uses five warm
repeats, a direct-address CSR posting directory, `uint32_t` generation-array
deduplication, hardware POPCNT, and a stable Hamming top-512.

The predeclared controls are:

| Variant | MIH schedule | Contractual inclusion guarantee |
| --- | --- | --- |
| `16x16-r64` | global radius 64 | every code within full Hamming distance 64 |
| `32x8-r1` | local radius one in every band | every code within full Hamming distance 63 |

The benchmark reports probe enumeration, posting traversal, deduplication,
Hamming scoring, and top-k selection separately. Counters are distinct from
timings: bucket probes, posting visits and bytes, unique candidates and their
corpus fraction, Hamming evaluations, and code bytes touched.

The external ANN harness was also extended to record Faiss's one-thread
`faiss.cvar.hnsw_stats.ndis` counter. It is a graph distance-work diagnostic,
not a claim that HNSW reads only its returned 512 candidates.

## Result

| Variant | Bucket probes | Posting visits | Unique candidates | Candidate fraction | Hamming@512 recall | Native total ms/query |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `16x16-r64` | 12,972 | 5,825.7 | 5,014.8 | 22.18% | 0.73857 | 0.8326 |
| `32x8-r1` | 288 | 29,170.4 | 16,117.4 | 71.29% | 0.98637 | 0.6415 |

The timing components explain why probe count is not an adequate work metric.
For `16x16-r64`, probe enumeration is `0.4513` ms/query and postings are
`0.1687` ms/query. For `32x8-r1`, enumeration is only `0.0075` ms/query, but
generation-array deduplication, Hamming scoring, and stable selection dominate
after nearly three quarters of the corpus reaches the union.

As a one-seed Faiss Binary HNSW reference check (`M=16`, `efSearch=512`), HNSW
returned 512 candidates but performed a mean `7,192.8` Hamming distance
evaluations/query according to `hnsw_stats.ndis`. Thus returned candidates and
internal search work must remain separate in later comparisons.

## Interpretation and next checks

The experiment supports two conclusions without selecting a new algorithm:

1. `32x8-r1` confirms that the frozen ITQ-256 representation can preserve
   near-HNSW-level binary neighbourhood quality, but its current high-quality
   operating point is close to a proportional scan.
2. `16x16-r64` has a materially smaller union but loses too much Hamming top-k
   recall to be a direct replacement.

The next experiment therefore keeps the code and roots frozen and tests
budgeted, calibrated weighted-Hamming best-first probing against these controls.
It must report the same counters and the complete
`E5 oracle -> raw union -> Hamming-512 -> ADC-256` survival funnel. A later
separate experiment may change band layouts; code learning is not part of this
baseline.

## Limitations

These are one-machine, warm-cache, 128-query native diagnostics. They exclude
query encoding, ADC, exact E5 reranking, cold-cache I/O, memory high-water, and
multi-threaded throughput. The raw reports remain in `tmp/`; no evidence
archive is published for this implementation baseline alone.
