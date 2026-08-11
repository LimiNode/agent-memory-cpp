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
42--46 are independent repetitions of the binary code.  The float-HNSW rows
are rerun against each corresponding seed as matched controls, but are not
independent float-algorithm repetitions because they do not consume an ITQ
code.  Retrieval quality is never used to choose an ITQ rotation, an MIH
radius, or an HNSW parameter; the table records the predeclared frontier.  The
machine-readable expansion contract is
[`2026-08-10-ann-cascade-comparison-matrix-v1.json`](2026-08-10-ann-cascade-comparison-matrix-v1.json).

## Metrics and timing scope

Every row records exact-E5 top-10 candidate coverage, exact-E5 top-10 coverage
after the downstream shortlist, qrels nDCG@10, mean candidate count, index
build time, and candidate-generator/cascade p50 and p95.  Binary candidate
generator time is followed by the common Hamming/ADC/exact stages; query ITQ
projection and full-corpus oracle construction are excluded because they are
shared or diagnostic work rather than index-specific latency.

Timing is a Python reference-harness comparison: MIH uses the deliberately
transparent Python/NumPy reference while Faiss and USearch use their native
bindings.  It is useful for regression detection and for locating the next
implementation question, but is not a production MIH-versus-native-HNSW
latency verdict.  A production comparison requires the planned C++ MIH/block-
CSR challenger under the same timing protocol.
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

Before the full matrix run, the evaluator exercised all four paths once on the
full fixture: MIH-256, Faiss binary HNSW, USearch binary HNSW and Faiss float
HNSW.  Those one-pass values were smoke validation only and were intentionally
not interpreted as the experiment result.

## Result

The corrected complete 130-row matrix completed with all five ITQ seeds.  On
the fixed 256-candidate ADC-256 profile, current MIH at global radius 64
reached mean nDCG@10 `0.79536` and exact-E5 top-10 coverage `0.95361`.  Faiss
binary HNSW (`M=16`, `efSearch=512`) reached `0.80085` / `0.98835`; USearch
binary HNSW on the same profile reached `0.79827` / `0.98224`.  Faiss float
HNSW approached the E5 oracle (`0.80118` / `0.99968`).

The same reference harness measured median/p95 cascade times per query of
`21.88` / `25.42` ms for MIH r64, `3.64` / `4.05` ms for Faiss binary,
`4.11` / `4.76` ms for USearch binary, and `3.23` / `3.74` ms for Faiss float.
These numbers intentionally exclude shared ITQ query projection and the
full-corpus E5 oracle, but remain harness diagnostics rather than a production
latency frontier because the MIH implementation is Python reference code.

The paired bootstrap reports are descriptive fixed-profile comparisons, not a
post-hoc choice of one matrix row as the universal winner.  They are
fail-closed bound to their declared per-seed contribution endpoints, exact
comparison identifiers, 10,000 replicates, bootstrap seed, contribution
identity, and bootstrap source hashes.  They show a stable candidate-coverage
advantage for Faiss binary HNSW over MIH, while final nDCG differences are
materially smaller.  The corrected evidence archive is
`ann-cascade-evidence-v5.zip`, SHA-256
`5b44f322bbc6dd8f98756d497ffa81adf22390896c91019ead43c96f54e21b64`, with
bundle-root SHA-256
`cd2b6eceb3c3eacc94103d0f89b833b8212b3899dcfab8e72838d085fe54aa17`.

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
