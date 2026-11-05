cmake_minimum_required(VERSION 3.20)
project(agent_memory_add_subdirectory_consumer LANGUAGES CXX)

if(NOT DEFINED AGENT_MEMORY_SOURCE_DIR)
    message(FATAL_ERROR "AGENT_MEMORY_SOURCE_DIR is required")
endif()

set(AGENT_MEMORY_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(AGENT_MEMORY_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(AGENT_MEMORY_BUILD_BENCHMARKS OFF CACHE BOOL "" FORCE)
set(AGENT_MEMORY_BUILD_RESEARCH_TESTS OFF CACHE BOOL "" FORCE)
set(AGENT_MEMORY_ENABLE_MDBX OFF CACHE BOOL "" FORCE)
set(AGENT_MEMORY_ENABLE_JSON OFF CACHE BOOL "" FORCE)
add_subdirectory("${AGENT_MEMORY_SOURCE_DIR}" agent-memory-build EXCLUDE_FROM_ALL)

add_executable(agent_memory_add_subdirectory_consumer
    "${CMAKE_CURRENT_LIST_DIR}/../common/library_info_consumer.cpp")
target_link_libraries(agent_memory_add_subdirectory_consumer
    PRIVATE agent_memory::agent_memory)
target_compile_features(agent_memory_add_subdirectory_consumer PRIVATE cxx_std_17)
