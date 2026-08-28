# NeuRoute final semantic representation frontier

Date: 2026-08-28. Frozen protocol and completed measurement.

## Question

Can a compact document representation preserve the ranking contribution of
resident FP32 E5 over the already frozen ADC256 top-64 pool, without changing
the router, candidate set, Hamming shortlist, or ADC pool selection?

This is a final-rerank experiment, not an ANN or candidate-generation study.
Every treatment receives the same ordered 64 document positions per query.

## Frozen matrix

The multilingual DE/FR/JA 25k pools from the exact-E5 ablation and the nested
DE 1M primary-policy pools from the corrected scale-transfer study are bound by
SHA-256. Three frozen router seeds are replayed. The treatments are FP32, FP16,
per-document symmetric INT8 and INT4, ternary and five-level scalar controls,
the existing ADC256 order, and a document-only coordinate-binary ADC384
control. No qrels select quantizer parameters.

The coordinate ADC384 row is deliberately not called ITQ384: it uses the 384
original E5 coordinates, document medians, and conditional centroids. Widths
above 384 require a separately licensed overcomplete encoder.

## Measurements

Quality reports nDCG@10, paired loss against FP32, top-10 overlap, top-1 match,
and Kendall tau-b over the fixed 64-document pool. Storage reports encoded
payload, per-document metadata, exact bytes/document, projected 1M bytes, and
the actual materialized payload size.

Native timing separates decode/score, deterministic top-10 selection, and
total. A 64-repeat microbatch amortizes timer noise; the report divides back to
per-query time. All timing is warm resident. Cold storage fetch, MDBX routing,
Hamming, and ADC pool generation remain out of scope.

## Decision

A representation is quality-eligible only if cross-dataset mean nDCG loss is
at most .003 and no dataset loses more than .0075 against FP32. Selection is
then lowest total bytes/document followed by the maximum native row p95. A selected representation of
at most eight bits/dimension licenses a codec/layout follow-up. ADC384 licenses
an overcomplete 512+ bit follow-up only if it improves ADC256 by at least .005
nDCG while at least .005 gap to FP32 remains.

## Results

The fail-closed evidence replay passed for all 96 rows. Mean nDCG@10 across
the three frozen seeds was:

| Representation | Bytes/document | DE 25k | FR 25k | JA 25k | DE 1M | Cross-dataset loss vs FP32 | Gate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| FP32 | 1536 | .632164 | .617412 | .687926 | .578523 | .000000 | pass |
| FP16 | 768 | .632164 | .617486 | .687926 | .578523 | -.000018 | pass |
| symmetric INT8 | 388 | .633874 | .622542 | .687349 | .580170 | -.001978 | pass |
| symmetric INT4 | 196 | .619798 | .620614 | .683497 | .561105 | .007753 | fail |
| ternary 2-bit | 100 | .293290 | .311369 | .319724 | .207669 | .345993 | fail |
| five-level 3-bit | 148 | .569042 | .531350 | .624185 | .446260 | .086297 | fail |
| existing ADC256 order | 32 | .587374 | .560296 | .650135 | .492878 | .056336 | fail |
| coordinate ADC384 | 48 | .610036 | .602380 | .662478 | .515312 | .031455 | fail |

INT8 is the compact winner: it reduces resident document payload from 1536 to
388 bytes/document while passing both quality limits. Its mean top-10 overlap
with FP32 is .9918, top-1 agreement is .9711, and mean Kendall tau on the
fixed top-64 pool is .9911. The negative mean nDCG loss is not evidence that
INT8 is generally better than FP32; its paired 95% bootstrap interval
[-.003420, .000844] includes zero.

The native INT8 total timing was stable across all twelve dataset/seed rows:
median row p50 .027576 ms, median row p95 .030305 ms, and frozen maximum row
p95 .030891 ms/query. Several non-selected treatments suffered host scheduling
outliers during the long sequential run, so their maximum-row p95 values are
retained in the raw evidence but are not used for comparative latency claims.
The quality result and byte replay are unaffected.

Coordinate ADC384 improves mean nDCG by .024881 over existing ADC256, but still
loses .031455 to FP32. It therefore passes the predeclared mechanism gate for a
separate overcomplete learned-encoder study, not the production quality gate.
The selected eight-bit scalar representation separately licenses the packed
codec/layout follow-up.

## Evidence

```text
quality result SHA-256:       7fb285624271c1930ddf5c36117498702539f179bf392b8734635a8593268b50
materialization SHA-256:      5124d018990ca79565a38cb0b754f96fb664fa8ad83c892234f12a63f41eb59c
native report SHA-256:        045f37b42c79c74be02a8e8bc54bbecf6220fe848780f72f2e3fe2648aa1c8a7
fail-closed evidence SHA-256: e48b1c546a0edf817009f823891d7fdfcd75e1f68fb0426a50be2acef238a448
```

The evidence writer regenerated quality and materialization byte-for-byte,
then replayed every per-query ranking from persisted representation bytes in
the independent C++ evaluator. The final decision is `int8_symmetric`; both
`codec_layout_followup_licensed` and `overcomplete_adc_followup_licensed` are
true.
