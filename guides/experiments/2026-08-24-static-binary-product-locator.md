# Static binary-product locator diagnostic

Date: 2026-08-24. Context: calibration-only follow-up to the static ITQ
locator and external BinaryIVF frontiers.

## Question

Can a deterministic Cartesian partition of frozen ITQ-256 binary codes provide
useful coarse routing without training a global centroid codebook?  The tested
assignment is local nearest-prototype assignment, followed by best-first
multi-probe enumeration of global product cells and the existing full
ITQ-256 Hamming, binary-ADC, and E5 cascade.

## Frozen scope and controls

Only the Spanish 25k calibration materialization and its frozen Flat query
order are used. French and every confirmation split are forbidden. The ranking
code remains frozen ITQ-256; the product code is a locator only.

Each row stops after accumulating at least the requested 5%, 10%, or 25% of
the corpus, while retaining at least the required Hamming@768 candidates.
Empty cells count as probes. This is deliberate: an implicit codebook that
requires many empty lookups is not a cheap routing structure merely because
its assignment is cheap.

The static codebooks are deliberately simple mathematical controls, not
learned prototypes:

| ID | local partition | implicit cells |
| --- | --- | ---: |
| `repetition-b12-c2` | 12 blocks, all-zero/all-one local centers | 4,096 |
| `repetition-b16-c2` | 16 blocks, all-zero/all-one local centers | 65,536 |
| `walsh-b6-c4` | 6 blocks, fixed Walsh/complement local centers | 4,096 |
| `walsh-b4-c8` | 4 blocks, fixed Walsh/complement local centers | 4,096 |

`binary-product-locator.example.json` pins that matrix, the frozen input and
Flat-reference SHA-256 values, cascade limits, and the actual product-cell
order: sum of local Hamming costs, then lexicographic local-cost-rank tuple.
The Python runner is an
external calibration harness, so it reports routing work and quality but makes
no native C++ latency claim.

## Result

The product partition is extremely selective in a syntactic sense, but its
nearest-cell order is not semantically local for these frozen ITQ codes. At a
matched 5% actual candidate fraction, none of the fixed product treatments is
close to either the random static locator or BinaryIVF quality:

| locator | actual candidates | E5 survival after ADC | reranked nDCG@10 | global-cell probes p50 |
| --- | ---: | ---: | ---: | ---: |
| repetition 12 x 2 | 5.014% | 24.48% | 0.3063 | 192 |
| repetition 16 x 2 | 5.002% | 31.14% | 0.3784 | 2,974 |
| Walsh 6 x 4 | 5.014% | 24.20% | 0.3128 | 200 |
| Walsh 4 x 8 | 5.017% | 26.13% | 0.3308 | 197 |
| random static subset, 64 bits / r3 | 4.784% | 58.18% | 0.6087 | native MIH measurement |
| BinaryIVF, 4096 lists / nprobe 205 | 5.663% | 92.38% | 0.7866 | external Faiss measurement |

At 25%, the best product survival is still only 69.09% (`repetition-b16-c2`)
and it requires a median 15,145 global-cell probes. Its 65,536-cell codebook
also illustrates the sparse-cell problem: only 18,230 cells are occupied and
the median occupied posting length is one document. The 4,096-cell variants
are dense enough to avoid that extreme, but remain semantically weak.

## Interpretation

This closes the tested *fixed mathematical local-prototype* product family as
a practical challenger to BinaryIVF on this calibration material. It does not
close learned local prototypes: training could align product cells with the
retrieval task, but that is a different learned-locator treatment and requires
the already predeclared disjoint calibration partitions. It also does not make
a claim about a native implementation; the quality deficit is already large
enough that one is not justified for these static controls.

The result supports a narrower research rule: product construction provides
cheap assignment and an implicit large codebook, but neither property implies
semantic routing. A future learned product locator must first beat the static
product result under the same candidate budgets and be compared directly with
scale-aware BinaryIVF.

## Evidence and limitations

`run-binary-product-locator.py` validates every input payload against its
manifest SHA-256 before use. Its companion evidence packager includes those
payloads in the archive and recomputes every local assignment, best-first cell
order, candidate union, full Hamming ordering, ADC order, and every per-query
E5/nDCG contribution from the replayed ADC shortlist and frozen evaluation
data. Raw shortlists, contribution arrays, and archives remain untracked under
`tmp/`. The previous local archive digest is historical; a new archive should
be generated after this provenance hardening.

The measurements use only one frozen Spanish calibration corpus. They are not
a selection result, a confirmation result, or a latency comparison with native
MIH/HNSW. The next two scoped controls are mathematical rather than learned:
the feasibility boundary for syndrome/perfect-code partitions, then a
full-length RM(1,8)/Hadamard locator diagnostic.
