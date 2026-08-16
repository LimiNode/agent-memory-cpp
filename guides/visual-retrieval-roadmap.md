# Visual Retrieval Roadmap

> **Status (2026-08): deferred M2+ research and architecture map.** This is not
> an implementation commitment, does not change the frozen MIH confirmation,
> and is not automatically scheduled after it. Visual work starts only behind a
> separately approved artifact-profile, dataset and evidence protocol.

## Purpose And Boundary

The artifact-provenance roadmap already defines immutable image artifacts,
versioned OCR and vision representations, typed image-region anchors and
`FigureContext`. This document adds the missing retrieval-side plan: independent
visual candidate generators, their fusion with text signals, and the evidence
needed to promote one.

`KnowledgeUnitKind` continues to describe knowledge semantics, not media file
format. An image remains an `Artifact`; a retrieval-eligible image region,
caption or OCR span materializes through the existing segment-to-`Chunk`
workflow. OCR, vision-language models, visual encoders and media connectors are
optional adapters. The C++ core owns identity, descriptors, candidate planning,
hydration and provenance validation; it does not become an OCR or VLM runtime.

## Independent Image Projections

One image may create several independent, versioned projections. They answer
different questions and must not be silently substituted for one another.

| Projection | Primary use | Retrieval path | Limitation |
|---|---|---|---|
| OCR text | Exact visible terms, identifiers, error messages | BM25/BM25F, learned sparse, text dense route | Cannot express visual content with no text. |
| Caption or visual description | Textual semantic access to visual content | Text lexical/dense route | Model-generated, potentially wrong; always derived and labelled. |
| Visual semantic embedding | Image-to-image and compatible text-to-image retrieval | Separate visual vector index/ANN | Requires a matched image/text encoder space. |
| Visual semantic binary code | Compact visual semantic candidate generation | Descriptor-scoped MIH/Hamming research path | Requires its own image training/evaluation evidence. |
| Perceptual duplicate hash | Near-copy/crop/resize/compression detection | Small Hamming radius or duplicate lookup | Not a semantic image-similarity signal. |
| EXIF and artifact metadata | Date, camera, source and policy filters | Exact metadata indexes | Not a content-relevance signal. |

The descriptor for each derived projection binds the input artifact digest and
source revision, adapter/model/checkpoint revision, preprocessing and crop
policy, normalization, output-space identity, bit length when applicable, and
the segment/locator relation. A binary descriptor additionally records whether
the code was trained directly for Hamming retrieval or quantized from a float
embedding, together with its training or quantization and bit-selection policy.
A text E5 space and a CLIP-like visual space are different embedding spaces
even when their dimensions match; they cannot share an index or be directly
score-fused without an explicit calibrated contract.

The artifact digest remains the byte-identical duplicate test. A perceptual
hash instead answers whether two separately encoded artifacts are near copies;
it is not a weaker semantic embedding and cannot act as the coarse locator for
a semantic-image route. pHash, dHash, aHash, Haar-wavelet hashes and
crop-resistant hashes have different perturbation contracts. Their descriptor
therefore records the algorithm and revision, image conversion, resize and
crop policy, and hash length.

## Candidate Architecture

Strict access, scope, lifecycle and metadata constraints construct the common
eligible set before expensive work. Within that set, eligible visual routes are
independent candidate generators:

```text
image artifact / region
    -> OCR text ------------------------> lexical or text-vector candidates
    -> caption/vision description ------> lexical or text-vector candidates
    -> visual embedding ----------------> image/image or text/image candidates
    -> visual binary code --------------> MIH -> full-code Hamming candidates
    -> perceptual hash -----------------> near-duplicate candidates only
                                            |
                                      provenance-aware fusion
                                            |
                           hydrate artifact/region and build cited context
```

The query determines which routes run. An exact screenshot error normally
favours OCR lexical search; a natural-language description of an unlabelled
object requires a compatible text-to-image embedding; an image query uses the
visual route; and copy detection uses the perceptual-hash route. A returned
candidate must retain its original artifact and `ImageRegionLocator` or
`WholeArtifactLocator`; OCR and generated captions never become the cited image
truth.

`FigureContext` remains the structural bridge to author captions and adjacent
document text. It is useful for fusion and context expansion but does not turn
an image file into a text-only unit or assert that a generated description is
authoritative.

## Perceptual Duplicate Route

Perceptual matching is useful for deduplicating re-encoded screenshots,
resized pictures and similar ingest artifacts. It needs its own labelled
near-duplicate protocol: byte-identical copies, resize, JPEG recompression,
brightness or colour changes, overlays and crop variants must be reported as
separate perturbation classes. A poor crop result is not evidence against a
hash designed only for resize or recompression; it calls for a crop-resistant
hash or local-feature method.

Do not assume that MIH is faster for a short perceptual code. For example, a
64-bit `BinaryFlat` scan is a sequential XOR/popcount pass and may beat index
construction and sparse probing at the project scale. Each accepted code and
corpus size therefore compares the exact flat baseline with native MIH; Binary
HNSW is a later challenger only when its backend and build/memory contract are
available. A classical multi-stage image matcher, such as `Simd::ImageMatcher`,
is also a useful non-neural baseline: it has a different descriptor and cascade
contract, so it must be measured as a separate route rather than described as
an MIH implementation.

## Visual Binary And MIH Research

Visual binary codes are a separate family from the current text E5 -> ITQ
experiments. A directly trained image hash can make bit balance, Hamming
neighbourhoods and MIH routing behavior part of its learning objective; a float
embedding quantized after the fact has a different contract. Neither inherits
text-selected band counts, radii or cost models without a new calibration.

The ElasticHash pattern is a useful deferred challenger, not an adopted design:

```text
coarse visual code (for example, 64 bits) -> MIH -> candidate positions
full visual semantic code (for example, 256 bits) -> full Hamming rerank
```

If visual binary work is admitted, compare at least these predeclared arms on
one frozen image corpus and split:

1. directly trained full visual code -> native arbitrary-m MIH -> full
   Hamming;
2. coarse learned/selected visual code -> MIH -> full-code Hamming;
3. visual float embedding -> binary quantization -> MIH -> optional float
   rerank.

Report Recall@K, mAP, candidate/posting work, p50/p95/p99 latency, index and
resident bytes per image, build/rebuild time, image-query and text-query
coverage, plus the separate near-duplicate accuracy of the perceptual hash.
The binary challenger also needs an exact full-Hamming reference and, only when
the project has an evidence-qualified binary graph backend, a Binary HNSW
comparison. Do not interpret results from image-retrieval literature as
evidence for the current text MIH configuration.

## Evidence And Promotion Sequence

1. **Artifact and dataset gate.** Publish original-image identities, permitted
   licensing, region/ground-truth policy, query modalities and typed evidence
   anchors. Retain OCR/caption model descriptors separately from original bytes.
2. **Text-derived baselines.** Measure OCR and caption routes independently;
   distinguish exact-text, generated-description and visual-semantic query
   classes instead of reporting one blended score.
3. **Perceptual baseline.** Establish byte-identical and near-duplicate
   controls; compare the selected perceptual hash against BinaryFlat before
   introducing an index.
4. **Visual embedding reference.** Establish a compatible visual
   embedding/index baseline for image-to-image and text-to-image queries before
   binary compression or MIH. Measure cross-modal quality and artifact-region
   citation correctness.
5. **Binary/MIH challenger.** Only after the reference, test the frozen visual
   binary arms above against its quality, latency, memory and write/rebuild
   gates.
6. **Multimodal fusion and reranking.** Compare independent-route fusion with
   each single route. A vision or cross-modal reranker is host-owned and can
   only reorder a supplied pool; it cannot recover an image lost by all
   candidate generators.

All stages preserve the artifact profile's generation and stale-projection
rules. A failed OCR or caption representation simply removes that route; it
does not invalidate the original image, visual embedding or a separately
published projection.

Cross-modal semantic codes and the shared descriptor-scoped binary-engine
direction are described in
[`multimodal-binary-retrieval-roadmap.md`](multimodal-binary-retrieval-roadmap.md).
They are not evidence that an image code is compatible with the current text
binary space.

## References

- [ElasticHash approach](https://nik-ko.github.io/elastichash/approach.html)
  and [paper](https://arxiv.org/abs/2305.04710): short visual routing code plus
  full-code Hamming reranking; an image-retrieval reference only.
- [CLIP](https://arxiv.org/abs/2103.00020): canonical contrastive shared
  image/text embedding-space reference.
- [Multi-Index Hashing](https://www.cs.toronto.edu/~norouzi/research/papers/multi_index_hashing.pdf):
  compact binary-descriptor candidate-generation reference.
- [Image perceptual hashing](https://github.com/JohannesBuchner/imagehash):
  pHash, dHash, aHash, Haar-wavelet hash and crop-resistant hash reference;
  useful for the distinct near-duplicate route.
- [ErmIg's ImageMatcher article](https://habr.com/ru/articles/122372/):
  classical coarse-to-fine image-matching reference, not a semantic or MIH
  result.
- [`artifact-provenance-roadmap.md`](artifact-provenance-roadmap.md): normative
  artifact, representation, segment and evidence-anchor contracts.
