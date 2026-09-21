# THQ4 LSQ32/48 and residual Gate C

Status: `EXECUTED` / `AUDITED` (2026-09-21)

This wave closes the two algorithmic checks left after the Faiss RQ and
faithful RSLM studies. It remains separate from the native serving benchmark.
The canonical source bundle was found under the existing `tmp/` and workspace
materializations; all source hashes are recorded in the compact receipts
committed next to this note.

## Source-bound protocol

Both gates use the same frozen 152-query R4 candidate shell, canonical THQ4
interval-squared top-128 filter, cosine metric, deterministic document-ID tie
break, and FP32 cosine over reconstructed vectors for the final top-10.

The bound inputs are:

| input | SHA-256 |
| --- | --- |
| documents | `d4f67ebe91faa159eaaeb7884281ad0d0057c27cdb67c4007f260c6442636007` |
| train vectors | `1f581860cff679989f0661fb27623c650bc130e8ed4593b0abe08e5474c00b80` |
| queries | `abe14a8790bd488fc91b01b4b1d6ab664db1d2f2d69e67147ed8439f54c73191` |
| qrel IDs | `45e3b9f01a551aad850c8ad77e41cfced815874a4d51ef6b105562cb042c64af` |
| qrel scores | `8034391872dff154f79b1327501d78c9c6112c204cba94f4a8f0f57f5ba8b7ec` |
| teacher IDs | `bbb2e1b0010cf57eace33251d6844528eac54e9d699f8a9210c1128685e8594d` |
| THQ4 codes | `0a0c825720bccef97a0fd1af5c7727671b5e0a79be09b643e0fcb558236e2b70` |
| THQ4 thresholds | `ceaa7ed312525bfa81a96b9f53aa79fb7f210387eb190a752051165dbdbd37a4` |
| candidate flat | `d76cabd553bbd1453908a9cd28fe3578895cf2cd3876026a5b1fd5813839bc79` |
| candidate raw | `c1d91c940e5f420679f63fb37282889a58fc96dd82f6f8b0aa876d70c98bb60b` |
| candidate receipt | `82f3cebfee6e9249eebaf68e31cf86f7ac93f701e8fe0f5752dbd8eba28d437b` |

## LSQ32/48

`run-thq-faiss-lsq-replay.py` fits two independent
`faiss.LocalSearchQuantizer(D=384, M=32/48, nbits=8)` models on document-only
THQ residuals. LSQ is additive: every sub-codebook stores full-dimensional
vectors and decoding sums one selected vector from every sub-codebook. The
runner batches assignment over the union of selected documents so per-query
Faiss workspace setup does not contaminate the codec comparison.

| arm | side bytes | cascade bytes | mean nDCG@10 | p05 nDCG@10 | worst query | mean candidate FP32 overlap | mean teacher overlap |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| LSQ32 | 32 | 128 | 0.650175 | 0.000000 | 0.000000 | 0.886842 | 0.882895 |
| LSQ48 | 48 | 144 | 0.641877 | 0.000000 | 0.000000 | 0.894079 | 0.889474 |

The independent audit reports `status: PASS`, `source_replay: true`, and
`persisted_code_decode_replay: true`. It reconstructs the THQ shell and sums
the persisted LSQ codewords without calling Faiss. The result is therefore a
source-bound candidate-local quality comparison, not a full-corpus serving
latency claim. LSQ48 does not improve mean qrels quality over LSQ32 in this
protocol despite its larger side payload.

## Residual Gate C

Gate C evaluates two explicitly local references after the same THQ4 filter:
`rabitq_like` (one 384-bit sign vector plus one FP16 scale) and `bbq_like`
(eight 48-bit block sign vectors plus eight FP16 scales). The cosine lane
compares `code_only` and `scale`; positive global scaling is not a cosine
ranking correction, so no `scale_norm` claim is made. Rotation is persisted as
an FP32 384x384 matrix and is included in accounting.

| arm / correction | side bytes | cascade bytes | mean nDCG@10 |
| --- | ---: | ---: | ---: |
| `rabitq_like` / `code_only` | 50 | 146 | 0.150887 |
| `rabitq_like` / `scale` | 50 | 146 | 0.640971 |
| `bbq_like` / `code_only` | 64 | 160 | 0.150887 |
| `bbq_like` / `scale` | 64 | 160 | 0.643593 |

The independent audit reports `status: PASS`, `source_binding: true`, and
`independent_decode_replay: true` over 608 rows (152 queries x 2 arms x 2
corrections). It replays canonical THQ interval² selection and independently
unpacks signs, applies persisted FP16 scales, applies the persisted rotation,
and checks top-10 IDs, nDCG, teacher overlap, and payload accounting.

These are local research references. They are not faithful TurboQuant,
RaBitQ, BBQ, or NEQ implementations, and they do not establish an IP/MIPS
norm-explicit correction. The very low `code_only` score is a diagnostic that
the scale is essential for this local construction, not evidence for a
production codec.

## Evidence and next gate

Compact receipts bind result, audit, runner, source, and (external) model/code
artifact hashes. The full result rows and NPZ artifacts remain in the external
research workspace named by those receipts; large model and code archives are
not committed to Git.

The algorithmic gap is now closed for this bounded protocol. The next,
separate task is the native complete-cascade gate:

```text
R4 candidate stream -> native THQ4 byte-LUT top128 -> selected codec
-> FP32 cosine top-10
```

That gate must report native p50/p95/p99, cold/warm behaviour, touched pages,
exact top-10 parity against the source-bound reference, and its own receipt.
No production architecture is selected by this note alone.

## Native candidate controls

As the first native step, the C++ benchmark was extended with two candidate
shell controls. Both scan the frozen R4 candidate records with the native
THQ4 byte-LUT, retain 128 documents, and then perform a deterministic top-10
rerank. The FP32 arm is an oracle control; the INT8 arm decodes the persisted
linear INT8 code and then applies FP32 cosine to the decoded vector.

| arm | native mean ms/query | THQ top-128 set parity | top-10 parity | mean qrels nDCG@10 |
| --- | ---: | ---: | ---: | ---: |
| FP32 document oracle | 1.51261 | 152/152 | 152/152 | — |
| INT8-linear decode + cosine | 1.44184 | 152/152 | 152/152 | 0.656991 |

These measurements are candidate-local native controls, not full-corpus
serving latency. The parity audit uses the canonical source bundle and checks
the retained set as a set (the native and NumPy tie order can differ inside an
equal-score top-128 boundary) and the final ordered top-10 exactly. A native
complete-cascade gate for LSQ/RSLM payloads, with codec-specific storage and
cold/warm page accounting, remains the next implementation step.
