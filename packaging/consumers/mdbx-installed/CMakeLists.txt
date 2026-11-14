cmake_minimum_required(VERSION 3.20)
project(agent_memory_mdbx_installed_consumer LANGUAGES CXX)

find_package(agent_memory CONFIG REQUIRED)
include("${CMAKE_CURRENT_LIST_DIR}/../common/verify_public_header_manifest.cmake")

add_executable(agent_memory_mdbx_installed_consumer
    "${CMAKE_CURRENT_LIST_DIR}/../common/mdbx_consumer.cpp")
target_link_libraries(agent_memory_mdbx_installed_consumer
    PRIVATE agent_memory::agent_memory)
target_compile_features(agent_memory_mdbx_installed_consumer PRIVATE cxx_std_17)
