# Frozen A@256 exact-E5 rerank necessity

Date: 2026-08-27. Protocol PR; measurements are intentionally absent.

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
