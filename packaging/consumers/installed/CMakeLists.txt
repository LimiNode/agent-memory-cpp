cmake_minimum_required(VERSION 3.20)
project(agent_memory_installed_consumer LANGUAGES CXX)

find_package(agent_memory CONFIG REQUIRED)

string(REPLACE "." ";" _agent_memory_version_parts "${agent_memory_PACKAGE_VERSION}")
list(LENGTH _agent_memory_version_parts _agent_memory_version_count)
if(NOT _agent_memory_version_count EQUAL 3)
    message(FATAL_ERROR "Unexpected agent_memory_PACKAGE_VERSION: ${agent_memory_PACKAGE_VERSION}")
endif()
list(GET _agent_memory_version_parts 0 _agent_memory_version_major)
list(GET _agent_memory_version_parts 1 _agent_memory_version_minor)
list(GET _agent_memory_version_parts 2 _agent_memory_version_patch)

add_executable(agent_memory_installed_consumer
    "${CMAKE_CURRENT_LIST_DIR}/../common/library_info_consumer.cpp")
target_link_libraries(agent_memory_installed_consumer
    PRIVATE agent_memory::agent_memory)
target_compile_features(agent_memory_installed_consumer PRIVATE cxx_std_17)
target_compile_definitions(agent_memory_installed_consumer PRIVATE
    AGENT_MEMORY_EXPECTED_VERSION_MAJOR=${_agent_memory_version_major}
    AGENT_MEMORY_EXPECTED_VERSION_MINOR=${_agent_memory_version_minor}
    AGENT_MEMORY_EXPECTED_VERSION_PATCH=${_agent_memory_version_patch})
