# Dependencies

## External Layout

Project-owned source dependencies live as flat Git submodules under
`external/`:

```text
external/
    libmdbx/
    mdbx-containers/
    simdcomp/
```

Do not add project-owned dependencies as nested submodules inside another
dependency just because the upstream project supports that layout. If a
dependency needs another dependency and both are owned by this checkout, keep
them as siblings under `external/` and pass targets through CMake.

## CMake Direction

Optional dependencies must stay behind CMake options and implementation
adapters. The core domain and contract layers remain dependency-free.

Preferred lookup order:

1. Reuse an already existing parent-provided CMake target.
2. Use a flat source dependency from `external/`.
3. Use an installed package when the selected dependency mode allows it.
4. Allow upstream fallback logic only as a last resort.

Avoid making `BUNDLED` a strict override over parent-provided targets. Parent
projects must be able to keep the dependency graph flat.

`AGENT_MEMORY_MDBX_DEPS_MODE` is forwarded to the source build of
`mdbx-containers` and controls how MDBXC provides libmdbx. It is not a strict
global selector for how `agent-memory-cpp` finds `mdbx-containers` itself.

## MDBX Chain

`agent-memory-cpp` owns these flat submodules for MDBX-backed storage work:

- `external/libmdbx`
- `external/mdbx-containers`

When `AGENT_MEMORY_ENABLE_MDBX=ON` and local source dependencies are present,
the build adds `external/libmdbx` first. `mdbx-containers` then sees the MDBX
target as parent-provided and exposes `mdbx_containers::mdbx_containers`
without creating a nested MDBX checkout.

Initialize dependencies with:

```bash
git submodule update --init external/libmdbx external/mdbx-containers
```

Use `AGENT_MEMORY_MDBX_CONTAINERS_SOURCE_DIR` only as an override for a custom
`mdbx-containers` checkout. The default source checkout is
`external/mdbx-containers` when it exists.

## SIMDComp Benchmark Adapter

`external/simdcomp` pins `fast-pack/simdcomp` for the optional x86/SSE2
BP128 codec benchmark. It is not a core-library dependency and it does not
define the durable on-disk representation. Non-x86 builds retain the portable
scalar decoder and report that the SIMDComp treatment is unavailable.

Initialize it with:

```bash
git submodule update --init external/simdcomp
```

## Planned Optional Dependencies

Future optimization work may add optional dependencies, but each must stay
behind a CMake option and an adapter boundary:

- Zstd for text/chunk compression and benchmarked bucket compression.
- Eigen for zero-copy math views and scoring/reranking helpers.
- simdutf for fast UTF-8 validation and UTF-8/UTF-32 transcoding in tokenizer
  adapters. It is not a normalization, stemming, or search engine dependency.
- utfcpp as a lightweight scalar UTF-8 validation, conversion, and code point
  iteration fallback when simdutf is not needed or not available.
- ICU for Unicode normalization, case folding, locale-aware segmentation, and
  other heavyweight tokenizer behavior when the std-only tokenizer is not
  enough.
- Miniz/zlib or LZ4 only as later codec alternatives when justified.
- Xapian or PISA only as optional lexical search adapters or optimization
  references after the project-owned `ILexicalIndex` contract and in-memory BM25
  baseline exist.

Planned options should be introduced only with corresponding implementation:

```cmake
AGENT_MEMORY_ENABLE_ZSTD
AGENT_MEMORY_ENABLE_EIGEN
AGENT_MEMORY_ENABLE_SIMDUTF
AGENT_MEMORY_ENABLE_UTFCPP
AGENT_MEMORY_ENABLE_ICU
AGENT_MEMORY_ENABLE_XAPIAN
AGENT_MEMORY_ENABLE_PISA
```

Planned public feature macros:

```cpp
AGENT_MEMORY_HAS_ZSTD
AGENT_MEMORY_HAS_EIGEN
AGENT_MEMORY_HAS_SIMDUTF
AGENT_MEMORY_HAS_UTFCPP
AGENT_MEMORY_HAS_ICU
AGENT_MEMORY_HAS_XAPIAN
AGENT_MEMORY_HAS_PISA
```

Define feature macros consistently as `0` or `1` once the corresponding option
exists.

Do not make Eigen, Zstd, Unicode backends, or external lexical engines part of
dependency-free public contracts.

Persist source and chunk text as UTF-8 by default. UTF-32/code point buffers are
allowed as temporary tokenizer internals or benchmarked derived artifacts, not
as the default storage representation.
