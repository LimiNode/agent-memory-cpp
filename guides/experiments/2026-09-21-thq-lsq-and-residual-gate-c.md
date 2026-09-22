# THQ4 LSQ32/48 and residual Gate C

Status: `EXECUTED` / `AUDITED` (2026-09-22)

This wave records the first source-bound LSQ and residual controls after the
Faiss RQ and faithful RSLM studies. It does not close the whole algorithmic
frontier: LSQ remains a research additive control, and the residual arms below
are local controls rather than vendor-compatible
TurboQuant, BBQ, or a paper-faithful RaBitQ implementation.
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
| LSQ32 strong | 32 | 128 | 0.657264 | 0.000000 | 0.000000 | 0.890132 | 0.886842 |
| LSQ48 strong | 48 | 144 | 0.661515 | 0.000000 | 0.000000 | 0.896711 | 0.894079 |

The independent audit reports `status: PASS`, `source_replay: true`, and
`persisted_code_decode_replay: true`. It reconstructs the THQ shell and sums
the persisted LSQ codewords without calling Faiss. The result is therefore a
source-bound candidate-local quality comparison, not a full-corpus serving
latency claim. LSQ48 does not improve mean qrels quality over LSQ32 in this
protocol despite its larger side payload.

The earlier replay (`0.650175` / `0.641877`) is retained only as a weak-budget
control. The completed strong replay uses the following pinned configuration:

```text
M=32, nbits=8
train_iters=25
train_ils_iters=8
encode_ils_iters=16
icm_iters=4
nperts=4
```

The strong replay is still candidate-local and does not establish held-out or
native serving quality. It is not AVQ/AAQ/QINCo.

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

The official RaBitQ estimator is a separate direct-IP/distance protocol. Its
one-bit IP form stores document-side factors (`F_add` and `F_rescale`) and
scores the packed sign dot against a transformed query with the `c_B S_q`
correction. The existing `rabitq_rr1` matched gate is an algebraically reduced
centered, full-dimensional one-bit form only when the `metric=ip` contract is
explicit; the residual reconstruction lane above must not be renamed RaBitQ.
A source-grounded direct-IP replay is now available in
`audit-thq-rabitq-official-ip.py`. On the canonical 152-query IP lane it
reports `0.558582` mean nDCG for both the explicit public-factor estimator and
the packed scorer; the common FP32 rerank diagnostic is `0.653278`. The
explicit public-factor form and packed scorer differ by at most `0.001658`;
25 of the 760 `(query, K)` selections differ, while direct top-10 sets match
on all 152 queries. This is numerical/protocol diagnostic evidence, not a
cosine reconstruction claim.

The Qdrant-pinned TurboQuant normal-mode control is now implemented in
`run-thq-turboquant-reference.py`. It reproduces the upstream seeded WHT
rotation, Lloyd-Max centroids, length rescaling, and 1/2-bit payloads. The
candidate-local cosine replay reports mean nDCG `0.659176` for 1-bit (52 B)
and `0.656254` for 2-bit (100 B). A separate bounded `TQMode::Plus` algebraic
control is now also executed by `run-thq-turboquant-plus-reference.py`: the
56 B lane reaches `0.653643` with direct decode and `0.633163` with the
query-side correction formula. It is source-pinned algebra, not QJL, wire
compatibility, or native SIMD evidence.

The corrected 1-bit Elastic/Lucene document payload is now reproduced by
`run-thq-elastic-bbq-reference.py`: 384 packed bits plus the public inline
correction trailer (62 logical bytes, 64-byte aligned), with centered
unit-cosine fitting and an independently audited optional block-PCA
preconditioner. The old `0.635520` residual-only row is superseded. The new
source-bound rows are `0.573386/0.585536` (direct/asymmetric, no
preconditioner) and `0.563219/0.589484` (direct/asymmetric, block-PCA).
Both artifacts reproduce all 304 top-10 rows exactly. Native SIMD, query-side
transpose, oversampling, and `bbq_disk` serving gates remain open; the older
`bbq_like` arm remains a separate local control.

## Evidence and next gate

Compact receipts bind result, audit, runner, source, and (external) model/code
artifact hashes. The full result rows and NPZ artifacts remain in the external
research workspace named by those receipts; large model and code archives are
not committed to Git.

The bounded protocol is audited, but the algorithmic gap is not closed. The
remaining systems work is a true native compressed-code scorer gate (plus
optional QJL and a full Lucene query-side implementation):

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
equal-score top-128 boundary) and the final ordered top-10 exactly. The
completed native THQ + predecoded-rerank gate covers orchestration and ordered
parity, but not compressed-code cost. A codec-specific native scorer gate with
cold/warm page accounting remains the next implementation step.
