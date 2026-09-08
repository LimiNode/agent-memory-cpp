# NeuRoute final semantic representation frontier

Date: 2026-08-28. Protocol PR; measurements are intentionally absent.

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
