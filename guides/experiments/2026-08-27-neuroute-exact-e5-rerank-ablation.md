# Frozen A@256 exact-E5 rerank necessity

Date: 2026-08-27. Protocol and completed measurement PR.

## Question

The native MDBX study identified the frozen raw-Euclidean learned route at 256
probes as the current serving knee, but its timing stopped after binary ADC.
This experiment asks how much ranking quality and latency exact E5 adds after
the fixed routing, Hamming, and ADC stages.

## Frozen setup

The German, French, and Japanese configuration partitions, three A-model byte
artifacts per language, 12-bit document medians, 256 logit-guided probes, 10%
candidate ceiling, Hamming top-768, qrels, ITQ codes, and ADC tables are reused
without training or selection. The experiment reports:

1. Hamming-order top-10;
2. ADC-order top-10 over Hamming top-768;
3. exact-E5 top-10 over ADC top-64/128/256/512.

ADC-only top-10 is invariant for every ADC limit at least ten, so the limit
sweep applies to the exact rerank pool rather than pretending to create four
different ADC rankings. All stages retain document-ID tie breaking and
per-query sequence digests.

The native timing is explicitly a lower bound: resident contiguous FP32
document/query matrices, three warmups, fifteen measured passes, and no query
encoder, routing, MDBX, Hamming, ADC, or cold-storage fetch. The report also
publishes hot FP32 bytes so 1M storage implications are not hidden by the
resident-memory timing.

## Decision rule

Exact E5 is removable from the default hot path only when its cross-language
mean nDCG gain over ADC-only at K=256 is at most .005 and no language gains more
than .01. The smallest exact rerank pool within .01 nDCG of exact-E5@256 for
every language is the preferred exact mode. These gates are diagnostic and do
not license width or scale selection.

## Evidence requirements

The contract binds the completed v4 quality, evidence, native result, and
materialization bytes. The quality report must be replayed from frozen model
and dataset bytes. The native evaluator validates every exact top-10 sequence
before timing, records its build/source manifest, and supports timing-free
report replay. The final evidence receipt is fail-closed over contract,
quality, materialization, native report, models, frozen roots, and source
hashes.

## Follow-up

After review of this ablation, frozen 12-bit A@256 transfers unchanged across
a nested 25k/100k/1M corpus. Width tuning remains a separate later question.

## Result

The frozen nine-route matrix and native resident-FP32 timing completed. Exact
E5 is not removable under the preregistered gate: reranking ADC top-256 adds
`.0456` mean nDCG across the three languages, with a substantial gain in every
language.

| Dataset | Hamming only | ADC only | exact E5 @64 | exact E5 @256 | exact gain over ADC |
| --- | ---: | ---: | ---: | ---: | ---: |
| DE | .5844 | .5874 | .6322 | .6286 | +.0412 |
| FR | .5222 | .5603 | .6174 | .6185 | +.0582 |
| JA | .6150 | .6501 | .6879 | .6875 | +.0374 |

ADC materially improves Hamming ordering, but it does not recover the final
semantic ranking. The failure is therefore not merely a weak Hamming shortlist:
exact dot-product ordering remains valuable after ADC has selected a strong
pool.

The smallest eligible exact pool is 64. Its maximum per-language nDCG loss
against exact-E5@256 is only `.0011`; DE and JA are directionally slightly
higher at 64, while FR is within `.0011`. This is a frozen result, not a reason
to tune below 64 on these same configuration partitions. E5-oracle overlap
still increases with larger pools, so 64 is the serving candidate under the
qrels contract, not a claim that the larger pool contains no additional
semantic neighbours.

| Resident FP32 exact stage | p50 ms/query | p95 ms/query |
| --- | ---: | ---: |
| 64 vectors, DE/FR/JA range | .0288-.0302 | .0365-.0420 |
| 128 vectors, DE/FR/JA range | .0610-.0679 | .0792-.0887 |
| 256 vectors, DE/FR/JA range | .1265-.1385 | .1580-.1681 |
| 512 vectors, DE/FR/JA range | .2671-.2764 | .3148-.3256 |

Each 25k document matrix occupies 38,400,000 FP32 bytes; the same representation
would occupy 1,536,000,000 bytes at 1M before container or alignment overhead.
The timing above excludes fetching those bytes, so it is a compute lower bound,
not an end-to-end storage claim. A production design should therefore retain
exact E5 as an optional/high-quality stage and prefer exact@64 by default while
allowing a binary-only lower-quality mode.

## Evidence

The replay-only evidence writer reproduced byte-identical quality and
materialization manifests and invoked the native timing-free sequence replay.

```text
quality result:        fe2632b245a2a02f7068c8a2a45c1244a3d9459a8245d54938f0983ad80a0921
materialization:       8f035c76ce042cbb99c05c7434b229ef3895e1c6a91522cef63bcc69057d9f86
native report:         87c3d9b8621b33e5f14e6e20ecc3a4c95e2e9c360502b79463b90da87261e304
evidence receipt:      20e68b60672e4b00454bb5cedf17b2f1dd17e2ae53475e503a118d8ad0e77ee1
```

The next scale protocol must carry both modes explicitly: the quality-default
`ADC -> exact E5@64` path and a compact binary-only mode. It must not silently
time ADC-only and publish exact-E5 quality as though they were the same path.
