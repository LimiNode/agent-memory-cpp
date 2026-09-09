# NeuRoute R4 representative physical-codec frontier

## Context

- Date: 2026-08-31
- PR: stacked on the R4 conditional set-coverage study
- Status: full DE-1M measurement and physical-byte validation complete

## Question

How far can the frozen FF32/K32 representative basis be compressed before the
learned R0 plus normalized max-cosine address scorer loses its held-out routing
frontier?

## Frozen protocol

The 16-bit document partition, exact K8 top-1024 shortlist, FF32 document IDs,
model weights, feature normalizers, strict `.003/.004/.005` candidate budgets,
and Hamming768 -> ADC64 -> exact final rerank cascade are unchanged. No model is
retrained. Configuration selects the smallest physical representation passing
all preregistered actionable and nDCG gates; internal opens once afterward.

The ladder is FP32, IEEE FP16, symmetric per-document INT8, INT6, and INT5.
INT6/INT5 are physically packed as three 128-value blocks with pinned SIMDComp
revision `009c67807670d16f8984c0534aef0e630e5465a4`; every score is computed
from bytes decoded from the saved store. INT8 uses 384 unsigned bytes plus a
float32 scale. The compact gates are mean/every-seed actionable loss
`.003/.006` and mean/every-seed nDCG loss `.002/.004`.

## Results

At the configuration `.005` frontier:

| Representation | Bytes/rep | Mean actionable loss | Max seed actionable loss | Mean nDCG loss | Max seed nDCG loss | Pass |
|---|---:|---:|---:|---:|---:|:---:|
| FP32 | 1536 | .000000 | .000000 | .000000 | .000000 | yes |
| FP16 | 768 | .000000 | .000000 | .000000 | .000000 | yes |
| INT8 | 388 | .000344 | .001032 | .000958 | .002874 | yes |
| INT6 | 292 | .000555 | .001448 | .002303 | .004034 | no |
| INT5 | 244 | -.000465 | .001193 | .002342 | .006677 | no |

Configuration therefore selects INT8, not INT5. Internal independently passes
INT8 with mean nDCG loss `.000025`, maximum seed nDCG loss `.000074`, and no
positive maximum seed actionable loss. INT5 happens to pass the internal gates,
but it is ineligible because it failed the locked configuration decision.

Across internal seeds, INT8 changes the winning representative in about 1.34%
of query/address pairs. Its mean absolute max-score error is `.0002405`, while
top-128 address overlap with FP32 remains `.9911`. Exact position agreement is
much lower because tiny score perturbations reorder many near-tied addresses;
the accepted-address and final cascade metrics are the decision evidence.

The actual single-assignment K32 INT8 payload is 340.0--348.7 MB per seed. The
same stores require 213.8--219.3 MB as INT5, but that extra saving does not pass
the configuration nDCG gates.

## Interpretation

The previous final-rerank INT5 result does not transfer automatically to
representative routing. A final top-64 reranker only needs a stable ordering in
a tiny pool; the address scorer takes a maximum over many representatives and
then ranks 1,024 near-tied addresses, which amplifies codec perturbations.
Under the preregistered causal protocol, INT8 is the smallest safe physical
representative representation.

This licenses a physical-layout benchmark with INT8 as the selected compact
codec. INT5/SIMDComp remains useful as a negative quality control and as the
already selected final-document codec; it is not selected for the K32 routing
basis.

## Limitations

- This is an offline quality study, not a latency measurement.
- The result is conditional on FF32, K32, this frozen scorer, and DE-1M.
- The three route seeds share the same corpus and query partitions.
- Exact address-rank identity is not a useful quality gate under near ties;
  cascade quality and accepted-address overlap remain authoritative.
- Production selection remains forbidden pending the separate physical layout
  and end-to-end studies.

## Evidence

```text
materialization SHA-256: 1566688756f1922c9f3cce83c46c9623d2231f221bbe496b12ae978c0cdad8db
result SHA-256:          6bf8395bad869c9d59bd4cc8f19a8b636d96df2edbba17e6d63a010c3263e928
evidence SHA-256:        5d35a92d392823ded29b2007816a5c5b826f680c627296e16e8c800a98a4fee2
```

The evidence writer rehashes every physical store and mapping. An independent
runner invocation must reproduce the canonical result byte for byte before the
PR is considered complete.
