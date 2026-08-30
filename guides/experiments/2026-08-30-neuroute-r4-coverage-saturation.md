# NeuRoute R4 actual-document coverage saturation

## Context

- Date: 2026-08-30
- PR: stacked on the teacher-selected R4 representative study
- Status: full DE-1M measurement and independent artifact/model/result replay complete

## Question

How many deterministic actual-document representatives are needed per occupied
16-bit address before the frozen max-interaction routing frontier saturates?

## Frozen protocol

The document partition, exact current-K8 top-1024 address shortlist, 8,141
training queries, three route seeds, optimizer/model seeds, and
Hamming768 -> ADC64 -> exact-E5 cascade are unchanged. Candidate evaluation
uses strict `.003`, `.004`, and `.005` unique-document fractions.

For each address, the teacher-blind centroid-nearest-actual plus deterministic
farthest-first construction is extended to K64. K8, K16, K24, K32, K48, and
K64 are strict prefixes. Every treatment uses the same R0 plus normalized exact
max-cosine scorer family; only the available representative prefix changes.
The frozen K32 model and rows from the parent interaction study replay exactly.

Configuration selects the smallest K whose mean and every-seed actionable-gain
and exact-nDCG gaps remain within preregistered tolerances of the K64 ceiling.
Internal evaluation opens once and only validates that selection. The report
also counts effective representatives, exact representative dot products per
query, and raw FP32/FP16/INT8/INT5 storage.

## Results

At the internal strict `.005` frontier, three-seed means are:

| K | Candidate fraction | Actionable gain | Exact nDCG@10 | Representative dots/query |
|---:|---:|---:|---:|---:|
| 8 | .004984 | .8518 | .6330 | 8,024.5 |
| 16 | .004982 | .8789 | .6442 | 13,837.7 |
| 24 | .004976 | .8959 | .6499 | 16,920.9 |
| 32 | .004979 | .9007 | .6507 | 18,486.8 |
| 48 | .004980 | .8969 | .6500 | 19,802.6 |
| 64 | .004980 | .8967 | .6502 | 20,284.5 |

Configuration selected `K*=32`, and internal evaluation confirmed the
preregistered saturation rule. Increasing the prefix to K48 or K64 does not
improve either headline quality measure, while K64 performs about 9.7% more
representative dot products than K32.

Single assignment caps physical representatives at the document count. The
actual INT5-plus-scale footprint is about 213.8--219.3 MB per seed at K32 and
235.3--238.4 MB at K64, rather than `occupied_addresses * K` dense slots.

Canonical result SHA-256 is
`b4ff2e63b40c8349fb70264221746a71ff22687afb43727bf9c44007047e0c83`.
An independent run regenerated all nine materialization artifacts, all 15 new
model archives, and the canonical result byte for byte. Evidence SHA-256 is
`7b8c9057248f5da43a1cf5723848582a916e34d3c7f02cc8f21dccd59074aca2`.

## Interpretation

The finite representative-coverage question is saturated at K32 for this
frozen scorer and pipeline. K48/K64 add work and storage without recovering a
better held-out frontier. The remaining research question is therefore which
query-independent K32 documents should represent each address, not whether a
larger deterministic farthest-first prefix is needed.

The result also narrows the next selector study: keep K32, the exact max-cosine
scorer, shortlist, budgets, and cascade fixed, then change only the K32 set.
A conditional facility-location objective can test whether training-only
teacher coverage improves on farthest-first diversity without repeating the
independent-win failure.

## Limitations

- This is an offline DE-1M diagnostic, not a native latency result.
- Exact representative vectors are fetched from the authoritative FP32 corpus;
  the byte ladder is accounting only and does not measure decode or random I/O.
- Saturation is conditional on the current partition, top-1024 shortlist,
  scorer family, training set, and cascade.
- The K32 control is frozen from its original width-32 numerical kernel. New
  K values use the matched wider materialization, avoiding a one-ULP kernel
  change that would otherwise make the parent-control replay non-exact.
- Compression selection and production selection remain forbidden.

## Follow-up

At frozen `K*=32`, compare deterministic farthest-first with pure conditional
facility coverage, centroid-anchored coverage, FF8/FF16 anchored coverage, and
the previous independent-win selector under the matched max scorer. Use only
the frozen 8,141 training queries and normalized static exact-E5 address
targets, select the recipe on configuration, and open internal once.
