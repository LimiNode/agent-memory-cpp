option(AGENT_MEMORY_BUILD_TESTS "Build agent-memory-cpp tests" ${AGENT_MEMORY_TOP_LEVEL})
option(AGENT_MEMORY_BUILD_EXAMPLES "Build agent-memory-cpp examples" OFF)
option(AGENT_MEMORY_BUILD_BENCHMARKS "Build agent-memory-cpp benchmark tools" OFF)
option(AGENT_MEMORY_ENABLE_HNSW_BENCHMARK "Enable the research-only hnswlib candidate benchmark" OFF)
option(AGENT_MEMORY_ENABLE_WARNINGS "Enable compiler warnings for agent-memory-cpp" ON)
option(AGENT_MEMORY_ENABLE_MDBX "Enable MDBX-backed storage dependencies" OFF)
option(AGENT_MEMORY_ENABLE_JSON "Enable JSON dataset loader for retrieval eval (PR #27). Requires nlohmann_json." ON)

set(AGENT_MEMORY_MDBX_CONTAINERS_SOURCE_DIR "" CACHE PATH
    "Optional mdbx-containers source override; defaults to external/mdbx-containers when present")

set(AGENT_MEMORY_MDBX_DEPS_MODE "AUTO" CACHE STRING
    "MDBX dependency mode forwarded to mdbx-containers when added from source: AUTO|SYSTEM|BUNDLED")
set_property(CACHE AGENT_MEMORY_MDBX_DEPS_MODE PROPERTY STRINGS AUTO SYSTEM BUNDLED)
