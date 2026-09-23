# Native compressed complete-cascade gate

Date: 2026-09-23
Status: `PLANNED` / direct LSQ scorer implemented, replay pending

This gate is the follow-up to the bounded AAQ/LSQ/QINCo2 wave. Its purpose is
to measure the serving path over compressed payloads rather than persisted
FP32 reconstructions:

```text
frozen R4 candidate stream
  -> THQ4 byte-LUT top128
  -> direct compressed-code cosine scorer
  -> top10
```

The first implemented arm is Faiss LSQ32/LSQ48. The binary payload contains
sorted candidate IDs, one-byte stage codes, the fitted codebooks, shared THQ
centroids (the per-document base is reconstructed from the already-scanned THQ
row), and an FP32 reconstructed-vector norm sidecar. The native
benchmark therefore charges `stages + 4` bytes/document and reports separate
THQ, codec, and total timings. It does not materialize a decoded FP32 vector in
the timed region.

Page accounting distinguishes the candidate-local packed layout from a
hypothetical production full-corpus layout. `codec_pages` uses the packed row
position in the persisted `selected_unique` table, not the document ID;
`model_pages` counts only shared codebooks and THQ centroids; and
`full_corpus_codec_pages` is reported separately for a row-aligned 1M-document
layout. This prevents candidate-local IDs from being mistaken for production
row offsets.

The command is `--lsq-candidate-gate` in
`native-full-corpus-codec-benchmark.cpp`; payloads are produced by
`materialize-native-lsq-payload.py`. The scorer is candidate-local to the
frozen 152-query/128-document shell, matching the source-bound quality gates.

Execution remains pending until the native build is configured and the
canonical source paths are supplied. The final gate must additionally record
Python-to-native top-10 parity, p50/p95/p99, cold/warm behavior, THQ/codec/model
pages, checksums, and an independent audit. Existing predecoded timings must
not be substituted for this result.

No production codec selection is made by this planned gate.
