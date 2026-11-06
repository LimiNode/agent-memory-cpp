# Framed Zstd codec (optional)

`FramedZstdCodec` is a physical representation adapter, not a BlobStore. It
is enabled only with `AGENT_MEMORY_ENABLE_ZSTD=ON` and uses the pinned
`external/zstd` submodule; ordinary builds do not acquire the dependency.
Installed-package/export closure is intentionally coordinated with the
packaging workstream (#581); this PR does not claim that an installed consumer
can link Zstd without that package integration.

The wire format has a versioned descriptor, explicit frame and decoded-size
budgets, a decoded SHA-256 digest, and independently decodable frames. Decode
fails closed on unknown versions, truncation, frame-size violations, malformed
raw fallback, decompression errors, trailing bytes, or digest mismatch.

`ArtifactId` is always derived from decoded bytes. Compression level, frame
size, and the explicit raw fallback for already-compressed media affect only
the physical descriptor and payload. This slice does not replace
`FileRawBlobStore`, train dictionaries, perform migrations, or activate a
production codec policy.
