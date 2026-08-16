# Multimodal Binary Retrieval Roadmap

> **Status (2026-08): deferred M2+ research and architecture map.** It does not
> change the frozen text ANN confirmation or schedule a successor experiment.
> The reusable subject is a descriptor-scoped Hamming candidate engine and its
> evaluation method, not an assumption that one binary representation or MIH
> configuration works for text, images and audio.

## Purpose And Boundary

[`artifact-provenance-roadmap.md`](artifact-provenance-roadmap.md) already
defines immutable artifacts, versioned derived representations, segments and
typed locators. [`visual-retrieval-roadmap.md`](visual-retrieval-roadmap.md)
defines image-specific retrieval routes. This document records the broader
research opportunity: foundation-model representations can be projected or
learned directly in a Hamming space for semantic image and audio retrieval,
including compatible text-to-image or text-to-audio retrieval.

The C++ core remains an artifact, descriptor, index and retrieval layer. ASR,
audio encoders, image encoders, binary projection training and neural hash
heads remain optional adapters. A new model output is a versioned projection;
it does not alter the original artifact or turn model-generated text into
source truth.

## Binary Spaces Are Not Modalities

The engine may reuse Hamming storage, flat scan, MIH, full-code scoring and
candidate diagnostics across modalities. It must never group codes merely
because they are all 64 or 256 bits. A binary projection is admitted to a
search only under one exact binary-space descriptor, including:

- stable binary-space and descriptor identity, encoder/checkpoint and
  preprocessing revision;
- database and query projection definitions, code width and bit order;
- the meaning of Hamming distance, allowed query modalities and the labelled
  relevance task;
- direct hash training objective or post-hoc PCA/projection/rotation policy;
- optional soft-query/asymmetric scoring contract and final reranking policy;
- index layout, band schedule and calibration provenance; and
- artifact/segment generation plus the stable `ArtifactId` and typed locator
  to hydrate after a candidate position is found.

This is an internal descriptor-scoped implementation direction, not a public
`TextMihIndex`, `ImageMihIndex` or `AudioMihIndex` API commitment. Existing
binary-index work already keeps candidate positions segment-qualified and
resolves them to stable identities before final ranking. A modality adapter
publishes a compatible descriptor; the candidate engine is deliberately
unaware of its media type.

Two code spaces of equal width cannot share postings, a Hamming threshold or a
score without that descriptor declaring them compatible. In particular, an
E5-derived text code, a perceptual image hash, a semantic visual hash, an audio
fingerprint and a semantic audio hash are different spaces. A common text,
image and audio Hamming space is a future trained-and-evaluated hypothesis, not
an architectural default.

## Retrieval Families

| Family | Projection and query | Retrieval semantics | Required later stage |
|---|---|---|---|
| Text semantic | Text encoder -> binary code | Semantically relevant text | Full Hamming, asymmetric score and/or float rerank as measured. |
| Image semantic | Visual encoder or matched text/image encoders -> binary code | Image-to-image or text-to-image relevance | Full Hamming and optional compatible float rerank. |
| Audio semantic | Audio encoder or matched text/audio encoders -> binary code | Audio-event similarity or text-to-audio relevance | Full Hamming and optional compatible float rerank. |
| Image identity | Perceptual hash | Near-copy under a declared perturbation set | Exact Hamming/threshold decision; no semantic rerank claim. |
| Audio identity | Windowed audio fingerprints | The same recording or fragment at a time offset | Hamming candidate lookup plus temporal sequence/offset verification. |
| Text derived from media | OCR, ASR transcript or caption | Exact and semantic text access | The ordinary lexical/text-dense stack. |

Identity and semantic tasks must have separate data, ground truth, metrics and
production gates. A perceptual image hash cannot serve as a semantic-image
locator; an audio fingerprint cannot establish semantic sound similarity.

## Semantic Binary Representation Portfolio

The first research baseline is deliberately simple: freeze a capable modality
encoder, transform its output with a recorded PCA/projection and orthogonal
rotation, then threshold to a binary code. This tests whether a compact code is
already useful before training a hash head. It is a challenger to ITQ-style
quantization, not evidence that any fixed transform is universally best.

The second family uses a directly learned binary head. Its objective may combine
semantic or cross-view alignment with code-usage, bit-balance or decorrelation
regularization. The descriptor must record whether the backbone was frozen,
adapted or jointly trained. Improved semantic labels do not by themselves prove
useful MIH geometry; retrieval quality, candidate work and bit/posting geometry
all need measurement.

For either family, a query may retain more information than the database code:

```text
query embedding -> soft bit scores -> thresholded code -> candidate generator
database embedding -> binary code ---------------------> candidate store
candidate pool -> asymmetric Hamming or binary ADC -> optional float rerank
```

This asymmetric path is a hypothesis that must be specified by the descriptor
and benchmarked against symmetric full-Hamming. It must not reuse a text ADC or
probability calibration for visual or audio codes.

A separate representation challenger is a short routing code plus a full
ranking code:

```text
coarse semantic code -> MIH -> candidate positions
full semantic code   -> full Hamming -> optional float rerank
```

The short code can be learned or selected only on calibration data. It is not a
perceptual hash and cannot be substituted for the full semantic code. This is
the modality-neutral version of the deferred ElasticHash-style experiment.

## Audio-Specific Contracts

An audio artifact can publish independent, versioned projections: timestamped
ASR transcript, semantic audio embedding, semantic binary code, windowed audio
fingerprints, and metadata such as duration or channel count. A semantic result
hydrates its original artifact with a `TimeRangeLocator`; a transcript is named
as a derived representation rather than presented as an original quote.

Audio fingerprinting has a different shape from one-code-per-artifact semantic
retrieval. It maps an audio clip to many short window fingerprints and must
preserve `(artifact, time offset)` evidence. After Hamming candidate generation,
the verifier tests a consistent sequence and offset. It therefore needs
fragment-localisation precision and robustness to declared transformations
(for example compression, pitch or time change) in addition to ordinary
retrieval metrics.

## Cross-Modal Hamming Hypothesis

Matched encoder families such as CLIP-like image/text or CLAP-like audio/text
spaces motivate a stronger experiment: train or project query and document
modalities into one declared Hamming space, then use Hamming distance directly
for text-to-image or text-to-audio retrieval. Existing literature makes this a
credible hypothesis, not a production conclusion.

Run it only as a separately predeclared experiment with paired cross-modal
ground truth. Its controls are the compatible continuous-space baseline and
each single-modality binary baseline. The experiment must separately report
text-to-image, image-to-image, text-to-audio and audio-to-audio results; a gain
on one task does not license another. A unified text+image+audio code space is
an additional hypothesis after those pairwise controls, with no implicit score
fusion across incompatible spaces.

## Evidence Sequence

1. **Artifact and dataset gate.** Freeze licensing, artifact identities,
   modality/segment policy, query forms, relevance labels and original
   provenance anchors.
2. **Continuous reference.** Establish exact or controlled float retrieval for
   the specific modality and task before binary compression; include a matched
   cross-modal reference where applicable.
3. **Binary representation.** Compare a recorded simple projection baseline,
   direct learned binary code and, where justified, coarse-to-full-code routing.
   Tune representation, width and all bit selections on calibration only.
4. **Binary backend.** On frozen codes, compare `BinaryFlat`, native sparse MIH
   and a Binary HNSW challenger only when its build/memory contract exists. Do
   not transplant text band counts, radii or hardware cost coefficients.
5. **Cascade and confirmation.** Freeze the admissible configuration, then use
   a new untouched evaluation set for quality, p50/p95/p99 latency, bytes,
   build/rebuild cost and per-stage candidate-work reporting.

For image and audio identity work, the corresponding perturbation and temporal
verification protocols replace semantic mAP as the primary proof. Fusion with
OCR, ASR or captions is a final independent-route experiment, never evidence
that a derived text representation is the original media truth.

## Research References

- [Hashing-Baseline](https://arxiv.org/abs/2509.14427): simple foundation-model
  projection and rotation baseline for image and audio binary retrieval.
- [CroVCA / HashCoder](https://arxiv.org/abs/2510.27584): learned image hashes
  with cross-view alignment and code-usage regularisation.
- [Compact Hypercube Embeddings](https://arxiv.org/abs/2601.22783): text-image
  and text-audio Hamming-space retrieval research; not evidence for a unified
  production multimodal index.
- [CLAP](https://arxiv.org/abs/2206.04769) and
  [ImageBind](https://arxiv.org/abs/2305.05665): continuous audio/text and
  multimodal shared-space references.
- [AudioNet](https://arxiv.org/abs/2511.01372): learned semantic audio hashing
  reference.
- [Audio fingerprinting reproduction](https://transactions.ismir.net/articles/10.5334/tismir.4):
  Hamming candidate retrieval with temporal sequence verification.
- [`visual-retrieval-roadmap.md`](visual-retrieval-roadmap.md) and
  [`optimization-roadmap.md`](optimization-roadmap.md): image-specific and
  current binary-candidate-generation contracts.
