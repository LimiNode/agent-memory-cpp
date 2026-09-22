# Faithful binary follow-ups after Gate C

Status: `EXECUTED` / `AUDITED` (2026-09-22)

This note owns the remaining algorithm checks. It is deliberately separate
from the reconstructed-cosine Gate C and from the native complete-cascade
benchmark.

## Corrective replay update (2026-09-22)

The first pass of this note contained a protocol error in the BBQ control: it
quantized a THQ residual and described the result as a Lucene-style document
quantizer. That bounded number is retained only as historical context. The
source-bound corrective replay now follows Lucene's centered unit-cosine OSQ
contract, persists the corpus centroid and an optional orthogonal block-PCA
preconditioner, and has an independent decode audit. The old `0.635520` row is
therefore **SUPERSEDED**, not a comparable production claim.

Corrective artifacts (outside the repository) are under
`E:/_repoz/research-wave-2026-09-22-elastic-bbq-v2/`:

| arm | preconditioner | direct decoded cosine | 4-bit-query asymmetric |
|---|---|---:|---:|
| BBQ-Lucene | none | 0.573386 | 0.585536 |
| BBQ-Lucene | deterministic block-PCA 8x8 | 0.563219 | 0.589484 |

Both rows are `EXECUTED`; both v4 artifacts pass the independent persisted
decode audit with `304/304` top-10 matches. These are candidate-local scalar
controls (62 logical / 64 aligned side bytes), not Elastic SIMD or `bbq_disk`
serving measurements. The block-PCA result is a research preconditioner, not a
free production improvement: it changes the global metadata and must be paid
for in any native/layout gate.

## RaBitQ

The public RaBitQ estimator is an asymmetric IP/distance estimator, not a
decoded-vector cosine scorer. The source-grounded one-bit protocol is:

```text
full-dimensional centered residual
-> sign code x_u
-> document F_add/F_rescale factors
-> query-side rotated vector
-> ip = <x_u, q'> + c_B * sum(q')
-> distance = F_add + G_add + F_rescale * ip
```

The implementation reference is:

<https://vectordb-ntu.github.io/RaBitQ-Library/rabitq/estimator/>

`tools/agent-memory-bench/audit-thq-rabitq-official-ip.py` evaluates the
explicit public `B=1` factors and checks their equality with the reduced
centered packed scorer. Its metric is `ip`; its result must not be merged with
cosine quality rows. The full source-bound replay reports the lanes separately:

| lane | mean qrels nDCG@10 | side bytes/document |
|---|---:|---:|
| explicit public estimator | 0.558582 | 52 B |
| packed serving scorer | 0.558582 | 52 B |
| same FP32 cosine rerank after K | 0.653278 | 52 B |

Explicit and packed top-10 sets are equal on all 152 queries; the broader
`K={32,64,128,256,512}` sweep has 25/760 top-K mismatches and a maximum score
difference of `1.66e-3`. Storage is 48 B sign bits plus one 4 B per-document
factor, hence 52 B side payload and 148 B with canonical THQ4. The global
mean/rotation model is 591,360 B and is not charged per document. The direct IP
rows are not reconstructed-cosine evidence: the common FP32 rerank is retained
only as a matched cascade diagnostic. Extended `B>1` codes are a separate
follow-up.

## Strong LSQ control

The independent Faiss `LocalSearchQuantizer` replay completed with the pinned
strong configuration (`train_iters=25`, `train_ils_iters=8`,
`encode_ils_iters=16`, `icm_iters=4`, `nperts=4`, seed `20260922`). The
source-derived THQ centroid check, persisted-code decode, exact row cardinality
(`304 = 152 × 2`), and summary replay all pass in the hardened audit.

| arm | side bytes | mean qrels nDCG@10 | mean FP32 candidate overlap |
|---|---:|---:|---:|
| LSQ32 strong | 32 | 0.657264 | 0.890132 |
| LSQ48 strong | 48 | 0.661515 | 0.896711 |

These are research additive controls, not AVQ/AAQ/QINCo and not a production
selection. They improve on the previous weak controls (`0.650175` and
`0.641877`) but remain candidate-local and require held-out/native confirmation.

`AVQ`, `AAQ`, and `QINCo2` remain `NOT_CHECKED`: no source-pinned local
implementation or reproducible configuration was available in this workspace,
so this wave intentionally creates no synthetic positive result for them.

## TurboQuant / QJL

TurboQuant is not equivalent to `rotation -> sign -> reconstruction`. The
faithful path requires the paper/source-defined scalar quantizer, residual QJL
sketch, query-side asymmetric estimator, and correction terms. The public
Qdrant implementation is a useful implementation reference, but its exact
version and distance mode must be pinned before importing any result:

<https://github.com/qdrant/qdrant/tree/master/lib/quantization/src/turboquant>

The normal-mode reference control is now implemented in
`tools/agent-memory-bench/run-thq-turboquant-reference.py`. It pins Qdrant
revision `6ab21cac18ebb6f4ae29102c7f8f5cc11affd5de`, the three upstream
permutation seeds, normalized chunked WHT, Lloyd-Max 1/2-bit centroids,
per-vector length rescaling, and the 4-byte Dot-compatible scale metadata. The
source-bound 152-query candidate-shell replay produced:

| arm | side bytes | mean qrels nDCG@10 | p05 | worst |
|---|---:|---:|---:|---:|
| TurboQuant normal 1-bit | 52 | 0.659176 | 0.000000 | 0.000000 |
| TurboQuant normal 2-bit | 100 | 0.656254 | 0.000000 | 0.000000 |

These are candidate-local reconstructed-cosine controls, not native latency
or a production selection. They do not include Qdrant's QJL residual
estimator. A separate bounded `TurboQuant+` algebraic control is now
`EXECUTED` and independently audited. It fits a persisted global shift/scale
table on the 25k training rows, stores a 48-bit sign code plus residual length
and `ec_correction` (56 B/document), and evaluates both direct decode and the
query-side correction formula:

| arm | side bytes | mean qrels nDCG@10 | p05 | worst |
|---|---:|---:|---:|---:|
| TurboQuant+ direct decode | 56 | 0.653643 | 0.000000 | 0.000000 |
| TurboQuant+ asymmetric correction | 56 | 0.633163 | 0.000000 | 0.000000 |

This is a source-pinned Qdrant `TQMode::Plus` **algebraic control**, not a
claim of Qdrant wire compatibility, QJL fidelity, or native SIMD performance.
The fit contract is explicit (`shift=-mean`, `scale=1/std` with a `1e-4`
floor), and the independent audit reproduces all `304/304` top-10 lists.

The selected IDs, THQ base and decoded 1/2-bit residual payloads are persisted;
the independent TurboQuant audit replays all 304 top-10 lists with zero
mismatch. This validates the local decode path, not QJL or the native serving
estimator.

## BBQ

The exact 1-bit Elastic/Lucene document payload is now implemented in
`run-thq-elastic-bbq-reference.py`, pinned to the retrieved Lucene
`OptimizedScalarQuantizer` source at Lucene revision
`0b346c5141e5ffeca8129a0280fa55768a19c643`. Each document stores 384 one-bit values and
the inline trailer `(float lowerInterval, float upperInterval,
float additionalCorrection, uint16 quantizedComponentSum)`: 62 logical bytes,
64 bytes after alignment. The historical pre-correction THQ top-128 replay
reported mean qrels nDCG@10 `0.635520` (p05 and worst query `0.0`). That row is
**SUPERSEDED** by the v4 centered-cosine replay in the corrective update above
and must not be compared as if it used the same document semantics.

The runner now persists the selected document IDs, THQ base, OSQ code values and
intervals (the NPZ is a byte-per-dimension working artifact; logical storage is
the separately reported packed 48-bit payload). `audit-thq-elastic-bbq-reference.py` independently decodes that
payload and reproduces all 304 top-10 lists (local and 4-bit-query arms) with zero mismatch. This closes
the decode/provenance gap, but not the serving-kernel gap below.

As a bounded serving-formula check, the same payload was also scored with a
4-bit query and the published asymmetric correction
`base_dot + ax*ay*D + ay*lx*doc_sum + ax*ly*query_sum + lx*ly*quantized_dot`.
That portable scalar control reached only `0.461179` mean nDCG@10 (192 B
ephemeral query code; 62 B/document side payload). It is a useful negative
result: the formula alone does not turn this THQ-residual payload into a
competitive BBQ arm, and it must not be presented as complete Lucene serving.

This is a faithful document quantizer/decode control, not a complete
Elastic/Lucene BBQ serving reproduction. The current score is local
reconstructed-cosine over the decoded document payload; it does not yet
implement production query-side 4-bit transpose, native asymmetric bitwise dot
kernel, oversampling/rescore policy, native SIMD, or `bbq_disk` topology. The
portable 4-bit correction-formula control above is explicitly not that serving
implementation.
The reference contract is:

<https://www.elastic.co/docs/reference/elasticsearch/mapping-reference/bbq>

The older `bbq_like` block-sign arm remains a separate local control and must
not be combined with this result. No production conclusion follows from the
candidate-local replay alone.

## Acceptance order

1. Keep the strong LSQ32/48 source replay and independent summary/centroid audit
   as the completed research control.
2. Keep the explicit one-bit RaBitQ IP gate separate from cosine evidence; its
   floating-point top-K mismatches require no production claim.
3. Keep the independent vector decode audits for persisted TurboQuant normal
   and the bounded `TQMode::Plus` control as completed; QJL and native serving
   remain optional follow-ups.
4. Keep the independent BBQ payload/decode audit and portable 4-bit control as
   bounded evidence; native transpose/oversampling remains open.
5. Run the native complete cascade with persisted finalist payloads, quantiles,
   page accounting, and exact top-10 parity. This bounded predecoded native
   control is now recorded in `2026-09-22-native-complete-cascade.md`; a
   separate compressed-decode/native-SIMD gate remains optional.

The current controls are useful bounded evidence. They are not a license to
select a production codec before these acceptance conditions are met.
