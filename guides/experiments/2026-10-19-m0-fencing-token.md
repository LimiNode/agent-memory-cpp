# M0 compact fencing-token benchmark receipt

This receipt records the first matched Release measurements after replacing
repeated full serialized-intent ownership reads with a durable 32-byte token.
The full intent remains the recovery payload. No batching or MDBX durability
mode change was made.

## Reproduction

From a Release MDBX build:

```text
agent_memory_mdbx_resource_importer_test.exe --benchmark
```

The command was run seven times on fresh temporary environments. Each run
measured 128 KiB, 1 MiB, and 4 MiB bodies with the same importer and storage
configuration. The reported `ownership_payload_bytes` is now the compact token
materialized by the hot path; the pre-token baseline is retained in
`2026-10-19-m0-importer-root-cause.md`.

## Candidate medians

| Body | Import elapsed | Import-only | Ownership lookups | Ownership bytes | Lookup time |
| --- | ---: | ---: | ---: | ---: | ---: |
| 128 KiB | 170.190 ms | 148.334 ms | 258 | 8,256 | 1.291 ms |
| 1 MiB | 1,532.900 ms | 1,511.340 ms | 2,050 | 65,600 | 10.238 ms |
| 4 MiB | 6,600.160 ms | 6,576.510 ms | 8,194 | 262,208 | 45.388 ms |

For comparison, the pre-token seven-run candidate medians were 194.874 ms,
2,683.09 ms, and 24,438.5 ms. The token path is therefore approximately
1.15x, 1.75x, and 3.70x faster for these three bodies on this host. These are
single-host measurements, not a portability guarantee.

The synthetic fencing probe also measured full-intent versus token lookup:

| Body / probes | Full materialized bytes | Full lookup | Token bytes | Token lookup |
| --- | ---: | ---: | ---: | ---: |
| 128 KiB / 256 | 33,604,608 | 2.997 ms | 8,192 | 0.362 ms |
| 1 MiB / 1,024 | 1,073,943,552 | 474.554 ms | 32,768 | 1.432 ms |
| 4 MiB / 4,096 | 17,180,676,096 | 6,285.390 ms | 131,072 | 4.860 ms |

## Correctness coverage

The importer test covers token creation and fixed 32-byte persistence,
transaction-level checks for canonical, lexical, raw-unit, and manifest writes,
atomic release, stale takeover fencing, and idempotent raw recovery. Additional
schema-negative cases reject missing or mismatched tokens, orphan tokens, and
unsupported schema markers. Legacy v3/v4 pending intents are held for the
quiesced/offline token upgrade instead of being auto-recovered by a mixed
binary deployment.

The raw KnowledgeUnit `create_or_get_unit()` API is unchanged for ordinary
callers; the importer uses a separate guarded entry point that checks the token
inside the same writable transaction before deduplication or insertion.
