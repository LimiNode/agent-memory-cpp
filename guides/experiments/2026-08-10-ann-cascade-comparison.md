# 2026-08-10 MIH versus external HNSW candidate generators

## Question

Does the existing ITQ-256 MIH cascade remain the preferable candidate generator
for the held-out E5 workload, or does a maintained external HNSW implementation
provide a materially better quality/latency/memory frontier?  The experiment is
not an adoption decision for either external library and does not add an ANN
dependency to the `agent_memory` library.

## Predeclared comparison contract

All rows use the frozen normalized `intfloat/multilingual-e5-small` E5 fixture
already used by the MIH study: 22,607 evaluation documents, 1,252 evaluation
queries, 13,100 qrels, and the disjoint 25,000-document calibration set.  The
full-corpus E5 inner-product order with stable document-ID ties is the only
oracle.

Binary rows train the same document-only 256-bit ITQ projection for every
engine.  The same packed, little-bit-order document/query code payload is
passed to both binary HNSW libraries.  All binary rows use the same downstream
stages:

```text
candidate generator -> exact Hamming top-512 -> binary ADC -> exact E5 rerank
```

The float row uses frozen E5 vectors directly:

```text
float HNSW candidates -> exact E5 rerank
```

The initial matrix is deliberately small:

| Family | Candidate generator | Predeclared parameters |
| --- | --- | --- |
| MIH | Existing 16 x 16-bit fixed-radius MIH | global radius 48, 56, 64 |
| Faiss binary | `IndexBinaryHNSW` on shared ITQ-256 codes | `M` 16/32; `efConstruction=200`; `efSearch` 512/1024; build seed 20260810 |
| USearch binary | Hamming/B1 `Index` on shared ITQ-256 codes | same `M`, construction and search breadth; deterministic ordered insertion (Python API exposes no build seed) |
| Faiss float | `IndexHNSWFlat` with inner product on normalized E5 | same `M`, construction and search breadth; build seed 20260810 |

For binary cascades, ADC shortlist limits 128 and 256 are measured.  ITQ seeds
42--46 are independent repetitions.  Retrieval quality is never used to
choose an ITQ rotation, an MIH radius, or an HNSW parameter; the table records
the predeclared frontier.  The machine-readable expansion contract is
[`2026-08-10-ann-cascade-comparison-matrix-v1.json`](2026-08-10-ann-cascade-comparison-matrix-v1.json).

## Metrics and timing scope

Every row records exact-E5 top-10 candidate coverage, exact-E5 top-10 coverage
after the downstream shortlist, qrels nDCG@10, mean candidate count, index
build time, and candidate-generator/cascade p50 and p95.  Binary candidate
generator time is followed by the common Hamming/ADC/exact stages; query ITQ
projection and full-corpus oracle construction are excluded because they are
shared or diagnostic work rather than index-specific latency.
The final matrix fixes one compute thread for NumPy/BLAS and Faiss; the runner
also sets `OMP_NUM_THREADS`, `OPENBLAS_NUM_THREADS`, `MKL_NUM_THREADS` and
`NUMEXPR_NUM_THREADS` to `1` before starting each evaluator subprocess.

External packages are research-only Python bindings pinned in
[`requirements-ann-cascade-comparison.txt`](../../tools/agent-memory-bench/requirements-ann-cascade-comparison.txt).
Their versions, the lock SHA-256, Python/NumPy runtime, evaluator sources and
all input manifests will be part of the evidence bundle.  The bindings are not
a production dependency and this protocol does not claim a C++ ABI or storage
integration.

## Preliminary implementation check

The evaluator has successfully exercised all four paths once on the full
fixture: MIH-256, Faiss binary HNSW, USearch binary HNSW and Faiss float HNSW.
Those one-pass values are smoke validation only and are intentionally not
interpreted as the experiment result.  The full predeclared multi-seed matrix,
paired bootstrap and evidence archive remain pending.

## Result

The complete 130-row matrix completed with all five ITQ seeds.  On the fixed
256-candidate ADC-256 profile, current MIH at global radius 64 reached mean
nDCG@10 `0.79536` and exact-E5 top-10 coverage `0.95361`.  Faiss binary HNSW
(`M=16`, `efSearch=512`) reached `0.80085` / `0.98836`; USearch binary HNSW on
the same profile reached `0.79625` / `0.98136`.  Faiss float HNSW approached
the E5 oracle (`0.80118` / `0.99968`).

The paired bootstrap reports are descriptive fixed-profile comparisons, not a
post-hoc choice of one matrix row as the universal winner.  They show a stable
candidate-coverage advantage for Faiss binary HNSW over MIH, while final nDCG
differences are materially smaller.  The evidence archive is
`ann-cascade-evidence-v3.zip`, SHA-256
`ddfec446db27e877155da5b7f7698b6478b61759fc98e658611d16813c29196c`, with
bundle-root SHA-256
`b4604252e71222d1d0d7fdf33b6e72b7d7ae4b047373678c36080dd725cefa90`.

This rules out neither compact MIH nor a future MIH improvement.  It supports
the narrower conclusion that the tested fixed-radius candidate policy, not
necessarily the shared ITQ-256 representation, is the current bottleneck.

## Follow-up decision rule

Do not implement an in-house HNSW from this study alone.  If an external HNSW
row materially improves the frontier, the next work is an optional backend
adapter feasibility study with explicit lifecycle/provenance contracts.  If
MIH stays comparable at the desired quality while retaining its memory and
latency advantages, continue with the block-CSR/whole-band storage direction
instead.
