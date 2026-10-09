add_executable(agent_memory_basic_usage
    basic_usage.cpp
)

target_link_libraries(agent_memory_basic_usage
    PRIVATE
        agent_memory::agent_memory
)

add_executable(agent_memory_canonical_content
    canonical_content.cpp
)

target_link_libraries(agent_memory_canonical_content
    PRIVATE
        agent_memory::agent_memory
)
