cmake_minimum_required(VERSION 3.20)
project(agent_memory_add_subdirectory_consumer LANGUAGES CXX)

if(NOT DEFINED AGENT_MEMORY_SOURCE_DIR)
    message(FATAL_ERROR "AGENT_MEMORY_SOURCE_DIR is required")
endif()

# These are normal variables deliberately shadowing the parent's cache values.
# The embedded project must not rewrite unrelated parent-project options.
set(AGENT_MEMORY_BUILD_TESTS OFF)
set(AGENT_MEMORY_BUILD_EXAMPLES OFF)
set(AGENT_MEMORY_BUILD_BENCHMARKS OFF)
set(AGENT_MEMORY_BUILD_RESEARCH_TESTS OFF)
set(AGENT_MEMORY_ENABLE_MDBX OFF)
set(AGENT_MEMORY_ENABLE_JSON OFF)
add_subdirectory("${AGENT_MEMORY_SOURCE_DIR}" agent-memory-build EXCLUDE_FROM_ALL)

get_property(_parent_examples CACHE AGENT_MEMORY_BUILD_EXAMPLES PROPERTY VALUE)
if(DEFINED _parent_examples AND NOT _parent_examples STREQUAL "ON")
    message(FATAL_ERROR
        "add_subdirectory changed the parent AGENT_MEMORY_BUILD_EXAMPLES cache")
endif()

add_executable(agent_memory_add_subdirectory_consumer
    "${CMAKE_CURRENT_LIST_DIR}/../common/library_info_consumer.cpp")
target_link_libraries(agent_memory_add_subdirectory_consumer
    PRIVATE agent_memory::agent_memory)
target_compile_features(agent_memory_add_subdirectory_consumer PRIVATE cxx_std_17)
target_compile_definitions(agent_memory_add_subdirectory_consumer PRIVATE
    AGENT_MEMORY_EXPECTED_VERSION_MAJOR=$<TARGET_PROPERTY:agent_memory,AGENT_MEMORY_VERSION_MAJOR>
    AGENT_MEMORY_EXPECTED_VERSION_MINOR=$<TARGET_PROPERTY:agent_memory,AGENT_MEMORY_VERSION_MINOR>
    AGENT_MEMORY_EXPECTED_VERSION_PATCH=$<TARGET_PROPERTY:agent_memory,AGENT_MEMORY_VERSION_PATCH>)
