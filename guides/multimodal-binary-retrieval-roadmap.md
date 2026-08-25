# Multimodal Binary Retrieval Roadmap

> **Status (2026-08): deferred M2+ research and architecture map.** It does not
> change the frozen text ANN confirmation or schedule a successor experiment.
> The reusable subject is a descriptor-scoped Hamming candidate engine and its
> evaluation method, not an assumption that one binary representation or MIH
> configuration works for text, images, audio and video.

## Purpose And Boundary

[`artifact-provenance-roadmap.md`](artifact-provenance-roadmap.md) already
defines immutable artifacts, versioned derived representations, segments and
typed locators. [`visual-retrieval-roadmap.md`](visual-retrieval-roadmap.md)
defines image-specific retrieval routes. This document records the broader
research opportunity: foundation-model representations can be projected or
learned directly in a Hamming space for semantic image, audio and video
retrieval, including compatible cross-modal retrieval.

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
`TextMihIndex`, `ImageMihIndex`, `AudioMihIndex` or `VideoMihIndex` API
commitment. Existing
binary-index work already keeps candidate positions segment-qualified and
resolves them to stable identities before final ranking. A modality adapter
publishes a compatible descriptor; the candidate engine is deliberately
unaware of its media type.

Two code spaces of equal width cannot share postings, a Hamming threshold or a
score without that descriptor declaring them compatible. In particular, an
E5-derived text code, a perceptual image hash, a semantic visual hash, an audio
fingerprint and a semantic audio hash are different spaces. A common text,
image, audio and video Hamming space is a future trained-and-evaluated
hypothesis, not an architectural default.

## Retrieval Families

| Family | Projection and query | Retrieval semantics | Required later stage |
|---|---|---|---|
| Text semantic | Text encoder -> binary code | Semantically relevant text | Full Hamming, asymmetric score and/or float rerank as measured. |
| Image semantic | Visual encoder or matched text/image encoders -> binary code | Image-to-image or text-to-image relevance | Full Hamming and optional compatible float rerank. |
| Audio semantic | Audio encoder or matched text/audio encoders -> binary code | Audio-event similarity or text-to-audio relevance | Full Hamming and optional compatible float rerank. |
| Image identity | Perceptual hash | Near-copy under a declared perturbation set | Exact Hamming/threshold decision; no semantic rerank claim. |
| Audio identity | Windowed audio fingerprints | The same recording or fragment at a time offset | Hamming candidate lookup plus temporal sequence/offset verification. |
| Video semantic | Global and clip-level video codes | Video-to-video or text-to-video relevance | Full Hamming and optional compatible float rerank. |
| Video identity | Frame/audio fingerprint sequences | The same or edited clip at a temporal offset | Hamming candidate lookup plus temporal sequence verification. |
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

Speaker identity is a third audio space. Speaker embeddings, including a future
binary variant, answer "is this the same speaker?" and must not be searched
under the relevance contract for audio events such as rain, alarms or speech
content. A speaker route has its own consent, access-control, error-cost and
verification protocol before it can be considered.

## Video-Specific Contracts

Video is not an image artifact with a larger byte payload. It may expose global
video semantics, temporal visual clips, temporal audio clips, ASR intervals,
frame OCR regions, keyframes and identity fingerprints simultaneously. A single
global semantic code is a possible coarse artifact route, but it cannot stand
in for retrieval over a long recording with several unrelated scenes.

The primary retrieval unit for long video is therefore a versioned temporal
segment, fixed-duration only when that is the declared baseline and otherwise a
shot, scene or semantic boundary. Semantic binary and float projections bind to
that segment and hydrate via the existing `TimeRangeLocator`. Frame-level OCR
and keyframe projections additionally retain their frame/image locator. A
global code, clip code, audio code and transcript are independent projections;
none silently replaces another.

```text
video artifact
    -> global video representation               -> artifact-level route
    -> temporal video segments -> visual code    -> semantic clip route
                              -> audio code      -> semantic sound route
                              -> ASR             -> lexical/text route
                              -> frame OCR       -> lexical screenshot route
    -> frame/audio fingerprints                  -> near-duplicate verifier
```

For example, a search for "where was `MDBX_MAP_FULL` shown?" is an ASR or
frame-OCR route; "where is a red graphics card demonstrated?" requires a
compatible text-to-video or clip-visual route; "where is a dog barking?" is a
text-to-audio clip route; and "find the edited copy of this clip" is an
identity-fingerprint sequence route. These examples are route-selection tests,
not a claim that one encoder covers every query.

Semantic video hashing research, including learned frame selection, is a useful
future representation challenger. A training-free projection from a capable
video encoder into a binary code is a separate hypothesis: image/audio results
do not establish its quality for video.

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

A common text+image+audio+video code space is later still. It requires a
declared multimodal training or projection objective and direct controls for
text-to-video, video-to-video and video-to-audio where those tasks are in
scope. Shared continuous spaces, such as LanguageBind, motivate the experiment;
they do not prove that thresholded codes retain all of those relationships.

## Predeclared Experiment Families

Each family starts with a new approved protocol, frozen calibration and
evaluation split, recorded encoder and preprocessing descriptor, reference
implementation, raw evidence package, and a separate confirmation set after
selection. No number from the current text MIH line selects a code width, band
layout or backend for these families.

| Family | Question and controls | Required outcome evidence |
|---|---|---|
| Semantic audio | Does a frozen CLAP-like float reference retain useful audio-to-audio and text-to-audio quality after simple projection, ITQ-style quantization or a learned hash head? Compare float exact/controlled search, symmetric Hamming and declared asymmetric rerank. | Recall@K/mAP by query type, code and index bytes, candidate funnel, p50/p95/p99, and a calibration-only selected binary backend on untouched queries. |
| Audio identity | Can window fingerprints find the same clip after declared compression, noise, pitch/time change and fragment offsets? Compare a classical fingerprint and a later neural fingerprint only under the same localisation protocol. | Pair precision/recall, false-match rate, offset/localisation error, sequence-verifier work and latency. |
| Semantic video | Does global-plus-clip representation beat either global-only or uniform fixed-window clips for video-to-video and text-to-video retrieval? Controls are a compatible float video baseline, fixed windows, then declared shot/scene segmentation. | Quality per query family, temporal localisation accuracy, clip count and bytes per source minute, build time, and through-cascade latency. |
| Video identity | Can frame/audio fingerprint sequences recover edited or re-encoded copies without claiming semantic similarity? Test crops, overlays, transcoding, inserted intros, frame-rate changes and speed changes as labelled perturbations. | Pair precision/recall, temporal overlap/offset accuracy, verifier false positives and candidate cost. |
| Cross-modal binary | Does a paired text-image, text-audio or text-video code space preserve relevance better than single-modality binary projections at the selected memory/latency budget? A unified four-way code is a separate final arm, not a pooled metric. | Task-separated Recall@K/mAP, continuous-space control, calibration/held-out chronology, cross-space compatibility checks and per-route coverage. |

An index comparison is nested inside every semantic family only after the code
has been frozen: `BinaryFlat` versus native sparse MIH versus Binary HNSW when
available. Identity families may use a specialised lookup and temporal verifier
instead; their result must never be reported as a semantic ANN win.

## Evidence Sequence

1. **Artifact and dataset gate.** Freeze licensing, artifact identities,
   modality/segment policy, query forms, relevance labels and original
   provenance anchors.
2. **Continuous reference.** Establish exact or controlled float retrieval for
   the specific modality and task before binary compression; include a matched
   cross-modal reference where applicable. For video, establish the segment
   policy and global-only control first.
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
- [ECAPA-TDNN](https://arxiv.org/abs/2005.07143) and
  [Binary Speaker Embedding](https://arxiv.org/abs/1510.05937): speaker-space
  references, separate from semantic-audio retrieval.
- [Audio fingerprinting reproduction](https://transactions.ismir.net/articles/10.5334/tismir.4):
  Hamming candidate retrieval with temporal sequence verification.
- [AutoSSVH](https://arxiv.org/abs/2504.03587),
  [ConMH](https://arxiv.org/abs/2211.11210) and
  [S5VH](https://arxiv.org/abs/2412.14518): semantic video-hashing and frame or
  temporal-structure research references.
- [LanguageBind](https://arxiv.org/abs/2310.01852) and
  [VideoPrism](https://arxiv.org/abs/2402.13217): continuous multimodal and
  video-retrieval reference spaces.
- [Near-duplicate video detection](https://arxiv.org/abs/2005.07356): temporal
  and perceptual visual identity-retrieval reference.
- [`visual-retrieval-roadmap.md`](visual-retrieval-roadmap.md) and
  [`optimization-roadmap.md`](optimization-roadmap.md): image-specific and
  current binary-candidate-generation contracts.
