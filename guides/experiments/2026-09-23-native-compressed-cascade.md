# Native compressed complete-cascade gate

Date: 2026-09-23
Status: `EXECUTED` for direct LSQ32/LSQ48; broader codec matrix remains pending

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

The LSQ32/LSQ48 replay is recorded in
`2026-09-23-native-compressed-lsq-result.md` with source-bound checksums,
Python-to-native top-10 parity, p50/p95/p99, two process runs, THQ/codec/model
pages, and an independent audit. Existing predecoded timings were not used as
a substitute. RSLM/QINCo2/AAQ compressed payloads still require separate
materializers before they can enter this native matrix.

No production codec selection is made by this planned gate.
