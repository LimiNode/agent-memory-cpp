# Typed representation and locator preview

This experimental slice makes the artifact-aware roadmap contracts executable
without adding parsers, OCR/ASR, downloaders, or storage adapters. It keeps
`SourceRevisionId`, `ArtifactBindingId`, representation identity and typed
locators distinct.

The value contracts cover normalized document spans, table columns/rows/cells,
image bindings with caption/alt placement, transcript intervals with speaker
and millisecond coordinates, and external video references. External video
references intentionally carry only provider identity/URL/time coordinates;
they never synthesize an `ArtifactId` for bytes that were not retained.

Retained representations require an output artifact identity. External-
reference-only representations must not claim one. The deterministic locator
codec is a bounded conformance value codec; it is not a catalog or hydration
implementation. All span and interval validators fail closed on inverted or
empty identities, and round-trip tests cover every locator family.
