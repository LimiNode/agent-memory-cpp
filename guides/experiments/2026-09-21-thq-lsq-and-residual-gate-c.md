# THQ4 LSQ32/48 and residual Gate C

Status: `PLANNED` / `PENDING_SOURCE_REPLAY`

This wave closes the two algorithmic checks left after the Faiss RQ and
faithful RSLM studies.  It is deliberately separate from the native serving
benchmark.

## LSQ32/48

`run-thq-faiss-lsq-replay.py` fits two independent
`faiss.LocalSearchQuantizer(D=384, M=32/48, nbits=8)` models on document-only
THQ residuals.  The two widths are not prefix arms.  The serving protocol is

```text
frozen R4 candidate shell -> canonical THQ4 interval² top128
-> LSQ32 or LSQ48 decode -> FP32 cosine top-10
```

The model archive stores sub-codebooks and offsets.  The paired audit decodes
those persisted sub-codebooks without calling `faiss.decode`, then replays
the full 152-query matrix.  The CI self-test uses a small 16D/2-subquantizer
fixture because a full 384D LSQ fit is a research-duration operation.

## Residual Gate C

`run-thq-residual-binary-gate.py` defines two explicitly local references:
`rabitq_like` and `bbq_like`.  They are not faithful TurboQuant, RaBitQ, or
NEQ implementations.  Both are alternatives after the same THQ4 top-128
filter and are evaluated with the same cosine rerank.  The required ablation
matrix is:

```text
arm ∈ {rabitq_like, bbq_like}
correction ∈ {code_only, scale, scale_norm}
```

The Gate C audit is intentionally marked `source_binding: true` and
`independent_decode_replay: false`; it must not be read as a quality claim
until the canonical 1M FP32 sources are available and an independent decoder
is added.

## Evidence boundary

The canonical 1M document/train/query/qrels/teacher bundle is not present in
the current checkout.  Therefore no LSQ or Gate C quality number is reported
by this wave.  A source-bound run must bind every input SHA, candidate receipt,
model archive, code archive, and runner SHA before any result can be promoted
to `EXECUTED` evidence.
