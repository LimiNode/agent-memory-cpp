# CMake package and consumer checks

This directory contains independent CMake consumers for the install/export
surface. The consumers intentionally use only the public target and headers;
they do not enable tests, examples, benchmarks, research tooling, or Python.

The minimal profile is configured with MDBX and JSON disabled:

```text
cmake -S . -B build-package-minimal \
  -DAGENT_MEMORY_ENABLE_MDBX=OFF \
  -DAGENT_MEMORY_ENABLE_JSON=OFF \
  -DAGENT_MEMORY_BUILD_TESTS=OFF \
  -DAGENT_MEMORY_BUILD_EXAMPLES=OFF \
  -DAGENT_MEMORY_BUILD_BENCHMARKS=OFF \
  -DAGENT_MEMORY_BUILD_RESEARCH_TESTS=OFF
cmake --build build-package-minimal --config Release
cmake --install build-package-minimal --prefix "$PWD/package-prefix-minimal"
```

Then configure `consumers/installed` with
`-DCMAKE_PREFIX_PATH=$PWD/package-prefix-minimal`, or configure
`consumers/add_subdirectory` with `-DAGENT_MEMORY_SOURCE_DIR=$PWD`.

The MDBX profile must install `mdbx-containers` and libmdbx into a dependency
prefix first. The installed `agent_memoryConfig.cmake` deliberately uses
`find_dependency(mdbx_containers CONFIG)`; it never embeds source-tree or
build-tree dependency paths into the consumer package.

The JSON profile is exercised when a `nlohmann_json` CMake package is
available. Its consumer parses a dataset through the installed public target;
it is kept separate from the MDBX profile because the JSON dependency is
optional. The add-subdirectory consumer also passes parent options that must
remain unchanged, while using normal (non-cache) child variables for its
minimal profile.

`verify-consumer.cmake` is used by CI and local checks to configure, build and
run each independent consumer. A successful configure/build/run is required;
compile-only checks are insufficient for the MDBX profile.
