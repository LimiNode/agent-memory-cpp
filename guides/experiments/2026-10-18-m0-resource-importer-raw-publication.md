# M0 raw Chunk publication benchmark

This is a bounded implementation measurement for the `LexicalWithRawChunks`
profile, not a production capacity claim. It was run with the MinGW MDBX build
and the repository's importer test binary:

```text
agent_memory_mdbx_resource_importer_test.exe --benchmark
```

| body bytes | raw chunks | elapsed ms | logical index rewrites | index-write amplification |
|-----------:|-----------:|-----------:|-----------------------:|--------------------------:|
| 131,072 (128 KiB) | 128 | 286.114 | 128 | 128x |
| 1,048,576 (1 MiB) | 1,024 | 4,066.58 | 1,024 | 1,024x |

The last two columns count the current `knowledge_units_by_kind` serialized
index rewrites: M0 performs one logical rewrite per newly published Chunk.
They are not a physical-I/O estimate. The super-linear growth is the known
cost of the current KeyValueTable-backed ordered `UnitId[]` index and is kept
as a follow-up performance task; this corrective PR does not change the DBI
layout or migrate it to DUPSORT.

The benchmark uses fresh temporary environments and does not alter repository
or research artifacts.
