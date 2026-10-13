# JPEG XL Image Representation Research Note

Date: 2026-10-13
Status: research candidate; no implementation claim
Scope: optional artifact representation for image storage and retrieval

## Question

Could JPEG XL (JXL) be a useful optional representation for large or frequently
decoded image artifacts without changing canonical identity, provenance or the
backend contract?

## Evidence

JPEG XL is standardized as ISO/IEC 18181. The JPEG committee describes lossy,
lossless and lossless recompression of existing JPEG files, progressive coding,
alpha, animation, wide-gamut, HDR and high-bit-depth support.

- [JPEG XL overview](https://jpeg.org/jpegxl/)
- [ISO/IEC 18181-1:2024](https://www.iso.org/standard/85066.html)
- [libjxl reference implementation](https://github.com/libjxl/libjxl)
- [Benchmarking JPEG XL lossy/lossless image compression](https://research.google/pubs/benchmarking-jpeg-xl-lossylossless-image-compression/)

Published benchmark results are evidence for particular corpora, encoders and
quality targets. They are not a universal claim that JXL is always smaller,
faster or better than JPEG, PNG, WebP or AVIF.

## Proposed storage boundary

JXL is a derived representation:

~~~text
canonical image artifact
    -> optional JPEG XL representation
    -> optional decoded thumbnail or visual projection
~~~

The canonical artifact identity remains independent of the codec. For a
lossless JPEG transcode, exact reconstruction of the original JPEG is a useful
property, but the original artifact identity and metadata policy must still be
recorded.

A future representation descriptor would need to bind:

- source artifact and source revision;
- representation digest and codec descriptor;
- encoder and decoder versions;
- lossless/lossy mode;
- quality or distance and encoder effort;
- dimensions, pixel format and colour profile;
- metadata preservation policy;
- creation time and build environment;
- decode and validation receipt.

This note does not create that public type or a DBI.

## Evaluation protocol

Before adopting an adapter, compare JXL with the existing deployment candidates
on a fixed image corpus containing photographs, screenshots, diagrams,
transparency, HDR/wide-gamut samples and re-encoded near-duplicates.

Measure separately:

- bytes per artifact and total storage;
- exact pixel equality in lossless mode;
- perceptual quality for lossy mode with a declared metric and qrels where
  applicable;
- metadata, ICC profile and orientation preservation;
- encode CPU, RAM and wall time;
- warm and cold decode p50/p95/p99;
- progressive or partial decode behaviour;
- failure and decoder portability across supported platforms;
- visual retrieval quality after decode and projection rebuild.

A benchmark must publish encoder settings, corpus manifest, source digests,
toolchain, libjxl revision, output digests and raw receipts. Quality and speed
must be compared at matched quality targets, not only at matched byte sizes.

## Safety and lifecycle constraints

- Do not replace canonical image bytes merely because a JXL representation is
  smaller.
- Do not use codec compression as a substitute for vector quantization or
  text/body compression contracts.
- A failed, stale or unavailable JXL decoder must fail over to the canonical
  artifact or an independently validated representation.
- Representation deletion must not erase provenance or the canonical artifact.
- Rebuild, update and delete operations must preserve artifact identity and
  representation lineage.
- Any external decoder or encoder remains an optional adapter dependency.

## Current decision

Record JPEG XL as an optional research candidate in the artifact/visual
representation roadmaps. Do not add a mandatory dependency, public C++ API,
DBI or production status until the matched-corpus evaluation passes the existing
quality, lifecycle, provenance and operability gates.
