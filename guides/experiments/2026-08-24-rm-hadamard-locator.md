# RM(1,8) / Hadamard locator diagnostic

Date: 2026-08-24. Context: structured full-length control following the
syndrome/perfect-code feasibility diagnostic.

## Question

Can RM(1,8), a 256-bit code with 512 affine Boolean-function centers and fast
Walsh-Hadamard nearest-codeword decoding, provide useful deterministic coarse
routing for frozen ITQ-256 codes?

## Method

The runner converts a binary code to signs and obtains all 256 affine
correlations with an in-place fast Walsh-Hadamard transform. For each affine
function, the two complement choices give 512 RM(1,8) centers. Assignment uses
the nearest center with the lowest center ID on ties. The machine-readable
contract separately pins query probing as exact `(distance-to-center, center-ID)`
order until cells contain at least
the 5%, 10%, or 25% candidate budget, then receive the unchanged ITQ-256
Hamming, ADC, and E5 cascade.

Only frozen Spanish 25k calibration material is used. The Python harness is an
external quality diagnostic and makes no native latency claim.

## Result

All 512 centers are occupied, with a median posting length of 46. The routing
is computationally compact—only 24 median centroid probes reach 5%—but is not
semantically local enough:

| candidates | E5 survival after ADC | reranked nDCG@10 | centroid probes p50 |
| ---: | ---: | ---: | ---: |
| 5.110% | 27.85% | 0.3378 | 24 |
| 10.117% | 40.66% | 0.4548 | 48 |
| 25.115% | 65.03% | 0.6324 | 123 |

At comparable 5% work, this is far below the 92.38% E5 survival of the
external 4096-list BinaryIVF control and even below the 58.18% random-static
64-bit/r3 locator. The broad RM cells therefore do not preserve the relevant
semantic neighborhood despite full-length structured decoding.

## Interpretation and limitations

This closes RM(1,8)/Hadamard as a static locator challenger for this frozen
calibration protocol. It does not generalize to every Reed-Muller order,
concatenated code, or learned structured codebook, and it does not claim an
implementation latency comparison. Together with the static product result,
it strengthens the evidence that cheap mathematical partition structure alone
does not substitute for the data-dependent routing learned by BinaryIVF.

The runner validates every input payload against its manifest SHA-256. The
fail-closed packager archives those payloads and recomputes FWHT correlations,
assignments, cell order, candidate unions, Hamming and ADC shortlists, and all
per-query E5/nDCG contributions from replayed ADC shortlists and frozen
evaluation data. Raw results and archives remain untracked under `tmp/`. The
post-hardening full replay completed successfully and produced
`tmp/rm-hadamard-locator-es25k/evidence-c.zip`, SHA-256
`2c54bed52f8a7e49a18f960a1d109b4194d7fd5bb53ea8cff3b97bf8d68848ef`.
It remains local pending review and any separately approved evidence-release
decision.
