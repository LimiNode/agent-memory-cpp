cmake_minimum_required(VERSION 3.20)
project(agent_memory_mdbx_hydration_installed_consumer LANGUAGES CXX)

find_package(agent_memory CONFIG REQUIRED)

add_executable(agent_memory_mdbx_hydration_installed_consumer
    "${CMAKE_CURRENT_LIST_DIR}/../common/mdbx_hydration_consumer.cpp")
target_link_libraries(agent_memory_mdbx_hydration_installed_consumer
    PRIVATE agent_memory::agent_memory)
target_compile_features(agent_memory_mdbx_hydration_installed_consumer PRIVATE cxx_std_17)

add_executable(agent_memory_mdbx_raw_markdown_product_consumer
    "${CMAKE_CURRENT_LIST_DIR}/../common/mdbx_raw_markdown_product_consumer.cpp")
target_link_libraries(agent_memory_mdbx_raw_markdown_product_consumer
    PRIVATE agent_memory::agent_memory)
target_compile_features(agent_memory_mdbx_raw_markdown_product_consumer PRIVATE cxx_std_17)
