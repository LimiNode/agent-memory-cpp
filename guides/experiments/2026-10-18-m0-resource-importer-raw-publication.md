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

These rows are the pre-optimization baseline: the serialized `UnitId[]` value
was rewritten once per newly published Chunk. They are not a physical-I/O
estimate.

## Append-only index follow-up

The performance pass keeps the same DBI name and KeyValueTable ownership, but
stores one `(ScopeId, Kind, IndexSequence) -> UnitId` entry per append. Existing
serialized lists migrate lazily on the first write to their scope/kind. The
same benchmark was rerun in a Release MinGW build:

| body bytes | raw chunks | elapsed ms | index entry writes | index-write amplification |
|-----------:|-----------:|-----------:|------------------:|--------------------------:|
| 131,072 (128 KiB) | 128 | 179.582 | 128 | 1x |
| 1,048,576 (1 MiB) | 1,024 | 2,704.74 | 1,024 | 1x |

The timings are directional evidence from one Windows/MinGW environment, not
a production capacity claim. The write-amplification counter is logical: each
new index member writes one bounded entry instead of rewriting the accumulated
list. The benchmark uses fresh temporary environments and does not alter
repository or research artifacts.
