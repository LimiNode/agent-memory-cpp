cmake_minimum_required(VERSION 3.20)
project(agent_memory_header_manifest_fixture NONE)

if(NOT DEFINED AGENT_MEMORY_SOURCE_DIR)
    message(FATAL_ERROR "AGENT_MEMORY_SOURCE_DIR is required")
endif()

set(_fixture_include_dir "${CMAKE_BINARY_DIR}/prefix/include")
set(_fixture_package_dir
    "${CMAKE_BINARY_DIR}/prefix/lib/x86_64-linux-gnu/cmake/agent_memory")
file(MAKE_DIRECTORY "${_fixture_include_dir}/agent_memory/storage")
file(MAKE_DIRECTORY "${_fixture_package_dir}")
if(AGENT_MEMORY_FIXTURE_WITH_FORBIDDEN_HEADER)
    file(WRITE
        "${_fixture_include_dir}/agent_memory/storage/CanonicalContentStoreInternal.hpp"
        "// deliberate forbidden-header fixture\n"
    )
endif()

add_library(agent_memory::agent_memory INTERFACE IMPORTED)
set_property(TARGET agent_memory::agent_memory PROPERTY
    INTERFACE_INCLUDE_DIRECTORIES "${_fixture_include_dir}")
set(agent_memory_DIR "${_fixture_package_dir}")

include(
    "${AGENT_MEMORY_SOURCE_DIR}/packaging/consumers/common/verify_public_header_manifest.cmake"
)

if(AGENT_MEMORY_FIXTURE_WITH_FORBIDDEN_HEADER)
    message(FATAL_ERROR "negative header-manifest fixture unexpectedly passed")
endif()
