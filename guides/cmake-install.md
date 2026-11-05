# CMake install and external consumers

`agent-memory-cpp` installs a relocatable CMake package. The supported target
for an external C++17 application is `agent_memory::agent_memory`.

## Minimal profile

The dependency-free profile keeps optional storage and evaluation integrations
out of a consumer build:

```bash
cmake -S . -B build/package \
  -DAGENT_MEMORY_ENABLE_MDBX=OFF \
  -DAGENT_MEMORY_ENABLE_JSON=OFF \
  -DAGENT_MEMORY_BUILD_TESTS=OFF \
  -DAGENT_MEMORY_BUILD_EXAMPLES=OFF \
  -DAGENT_MEMORY_BUILD_BENCHMARKS=OFF \
  -DAGENT_MEMORY_BUILD_RESEARCH_TESTS=OFF
cmake --build build/package --config Release
cmake --install build/package --prefix build/prefix
```

An independent consumer then uses the installed target:

```cmake
find_package(agent_memory CONFIG REQUIRED)
add_executable(app main.cpp)
target_link_libraries(app PRIVATE agent_memory::agent_memory)
target_compile_features(app PRIVATE cxx_std_17)
```

The installed package contains public headers, a static archive, an export set,
and version metadata. It does not enable tests, benchmarks, research tools,
Python, model runtimes, or network downloads.

## MDBX profile

`AGENT_MEMORY_ENABLE_MDBX=ON` requires package-provided `mdbx-containers` and
libmdbx targets in an external consumer. The installed `agent_memoryConfig.cmake`
uses `find_dependency(mdbx_containers CONFIG)`; it does not capture source or
build paths from the producer. `AGENT_MEMORY_MDBX_DEPS_MODE` controls only
source-tree dependency setup while building the library (`AUTO`, `SYSTEM`, or
`BUNDLED`).

MDBX-enabled consumers must provide the matching public MDBX headers and the
static-library transitive link closure for their platform. If that dependency
is unavailable, `find_package(agent_memory CONFIG REQUIRED)` fails with the
dependency diagnostic instead of silently producing a partial target.

## Scope and limitations

The package exposes the current stable C++ API. Lexical product-path
qualification still uses the bounded rebuild-per-query baseline; installation
does not imply a persistent product query API. Revision-bound summaries and
byte-verified citation hydration remain distinct capabilities, and metadata
filter support follows the individual retrieval implementation. Experimental
benchmark and ANN/graph/facet modules are not part of the minimal package.
