cmake_minimum_required(VERSION 3.20)

project(agent_memory_official_rabitq_runner LANGUAGES CXX)

if(NOT DEFINED RABITQ_SOURCE_DIR)
    message(FATAL_ERROR "RABITQ_SOURCE_DIR must point at the pinned RaBitQ-Library checkout")
endif()

set(RABITQ_BUILD_SAMPLES OFF CACHE BOOL "" FORCE)
set(RABITQ_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(RABITQ_BUILD_PYTHON_BINDINGS OFF CACHE BOOL "" FORCE)
set(RABITQ_ENABLE_NATIVE_OPTIMIZATION OFF CACHE BOOL "" FORCE)
add_subdirectory("${RABITQ_SOURCE_DIR}" rabitq-library)

add_executable(agent-memory-official-rabitq-runner main.cpp)
target_compile_features(agent-memory-official-rabitq-runner PRIVATE cxx_std_17)
target_compile_definitions(
    agent-memory-official-rabitq-runner
    PRIVATE AM_RABITQ_BUILD_TYPE="$<CONFIG>"
)
target_link_libraries(agent-memory-official-rabitq-runner PRIVATE rabitqlib::rabitqlib)
