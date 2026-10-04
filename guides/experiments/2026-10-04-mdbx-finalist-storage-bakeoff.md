# MDBX finalist storage bake-off (research prototype)

Status: executed, audited, and intentionally research-only. This experiment
measures persistence and candidate-payload read costs; it does not define a
production storage API or select a product codec.

## Contract

The batch materialized the four current finalists from the canonical packed
source payloads:

| Codec | source-faithful row payload |
|---|---:|
| LSQ32 | 36 B |
| TQ1 | 52 B |
| TQ1+PQ8 | 64 B |
| INT8 | 392 B |

Each payload was stored in both layouts:

- `row_kv`: uint32 document ID → one fixed-width payload row;
- `segmented`: uint32 segment ID → one contiguous blob of 4096 rows.

The workloads are the fresh 305-query `prototype_ivf` and `modern_r4` route
streams (5000 candidate IDs/query), plus the corresponding fresh exact-THQ
top128 IDs. Every benchmark uses one warmup and five measured repeats in each
of three independent process runs. The first query after reopening the MDBX
environment is reported as `reopen-coldish`: only the durable metadata
contract is checked before that sample; full byte parity is a separate
`--verify` phase. The operating-system cache was not flushed.

Materialization and reopen verification compare every stored byte against the
source payload. Builds are marked `state=building` until the durable complete
metadata commit; incomplete stores fail verification. The fail-closed audit
checks all 40 matrix rows, raw timing/I/O sample lengths, nearest-rank
percentiles, checksums, payload/workload hashes, and layout-specific read
counts.

## Results

The committed machine-readable summary is
[`2026-10-04-mdbx-finalist-storage-bakeoff.summary.json`](2026-10-04-mdbx-finalist-storage-bakeoff.summary.json).
Summary SHA-256: `f356fb0b453bb7c266055828f386cc18ce93d59194d680a5482dc9bc3d67f434`.
The full raw receipt (including every timing/I/O sample) is retained in the
research workspace at `tmp/mdbx-finalist-batch/mdbx-finalist-storage.corrected.result.json`.
Its SHA-256 is:

```text
095526533464bceed62a871e8f8e48f26d41c532c2394e50009f9239d46247cc
```

The corrective replay refreshed `reopen-coldish` and MDBX space statistics for
all 40 rows × 3 process runs using metadata-only preflight. Warm timing arrays
were retained from the original canonical read-path batch because that path was
unchanged; the full byte-parity `--verify` phase remains separate.

The table below reports the median across the three process runs as
`p50/p95/p99 ms`; `cold` is the median `reopen-coldish` first-query time.
The workload suffix is `top128` or `route5000`.

| layout | codec | Prototype-IVF top128 | Prototype-IVF route5000 | Modern R4 top128 | Modern R4 route5000 |
|---|---|---:|---:|---:|---:|
| row_kv | LSQ32 | 0.097/0.145/0.163 (cold 0.838) | 4.400/5.150/5.434 (cold 17.625) | 0.106/0.150/0.171 (cold 0.919) | 4.346/5.155/5.444 (cold 18.398) |
| row_kv | TQ1 | 0.103/0.154/0.178 (cold 0.909) | 4.625/5.391/5.719 (cold 19.762) | 0.103/0.151/0.173 (cold 0.866) | 4.675/5.551/6.035 (cold 19.374) |
| row_kv | TQ1+PQ8 | 0.097/0.145/0.170 (cold 0.892) | 4.375/5.197/5.480 (cold 19.737) | 0.113/0.149/0.169 (cold 0.880) | 4.362/5.139/5.493 (cold 23.021) |
| row_kv | INT8 | 0.229/0.287/0.323 (cold 1.230) | 8.237/9.326/9.600 (cold 30.476) | 0.219/0.270/0.295 (cold 1.185) | 8.548/9.485/9.988 (cold 32.502) |
| segmented | LSQ32 | 3.248/4.619/6.256 (cold 19.373) | 12.013/13.247/21.173 (cold 51.219) | 3.196/4.496/5.925 (cold 21.169) | 12.309/13.503/21.335 (cold 52.987) |
| segmented | TQ1 | 4.331/5.921/8.087 (cold 26.822) | 15.846/17.394/29.143 (cold 72.234) | 4.862/6.839/8.733 (cold 28.269) | 16.257/18.116/28.674 (cold 73.945) |
| segmented | TQ1+PQ8 | 6.636/8.815/11.684 (cold 32.236) | 21.745/23.405/35.362 (cold 86.576) | 6.103/8.011/10.703 (cold 33.321) | 21.334/23.098/34.459 (cold 88.080) |
| segmented | INT8 | 105.249/119.469/124.834 (cold 185.561) | 289.463/304.327/310.468 (cold 503.090) | 104.980/119.517/124.288 (cold 191.792) | 289.361/305.107/312.881 (cold 504.306) |

MDBX files were page-rounded. The measured environment file sizes were 83,886,080 B
(LSQ32 row), 50,331,648 B (LSQ32 segmented), 100,663,296 B (TQ1 and TQ1+PQ8
row), 67,108,864 B (TQ1 and TQ1+PQ8 segmented), 536,870,912 B (INT8 row),
and 402,653,184 B (INT8 segmented). These are physical file observations,
not exact page occupancy or logical payload widths. The raw receipt also
records `mdbx_used_pages`, `mdbx_used_bytes`, `mdbx_data_pages`, and
`mdbx_reclaimable_bytes` from MDBX environment statistics.
`logical_value_bytes_fetched` is the value length returned by MDBX/application
code; it is not physical disk I/O.

Segmented reads expose the storage trade-off directly. For route5000 the
fresh streams touch 245 segments/query (about 95.1% candidate reuse), while
top128 touches a median 89 segments/query. Thus segmented INT8 fetches a
median 392,000,000 B/query for 1,960,000 useful bytes; this is an expected
prototype layout effect, not a codec-quality result.

## Provenance

- Fixture manifest SHA-256: `213769b5bc064366a65c2e8c8b95db3e7cde5f9db97791f24e4a33117c44e623`.
- MDBX revisions: `libmdbx fc8b8e4697e0ef8b2cd5aee1f2d9fb0974fc665f`; `mdbx-containers e9e9f2fd5139f7fb386afd458fcdd8e20d7ec6e3`.
- Source payload hashes and workload stream hashes are recorded in the JSON receipt.
- Environment: Windows 11-compatible Windows build, Intel Xeon E5-2696 v3, 36 logical processors, Python 3.11.9; OS cache was not flushed.

## Scope boundary

This is a storage/read-cost prototype. It does not provide cold-cache
guarantees, rebuild/publication, interrupted recovery, concurrent readers, or
production integration. Those are separate MDBX follow-up experiments after
finalist quality and Pareto decisions.

## Compression follow-up (Z0)

The next staged experiment is an offline Zstd compressibility screen over the
canonical document ordering: FP32 reference, conventional INT8 control, and
LSQ32/TQ1/TQ1+PQ8 compact arms. It uses 4 KiB, 16 KiB, and 64 KiB target
uncompressed blocks, plain Zstd levels 1 and 3, and an FP32 byte-shuffle
secondary arm. The screen measures ratio and compression/decompression
throughput only; it does not claim MDBX serving latency. Arms saving less than
5% are not carried into a future Z1 serving gate, which is a research filter,
not a product decision.
