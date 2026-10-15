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

if(AGENT_MEMORY_ENABLE_MDBX)
    add_executable(agent_memory_durable_lexical_document_memory
        durable_lexical_document_memory.cpp
    )

    target_link_libraries(agent_memory_durable_lexical_document_memory
        PRIVATE
            agent_memory::agent_memory
    )

    add_executable(agent_memory_resource_importer
        resource_importer.cpp
    )

    target_link_libraries(agent_memory_resource_importer
        PRIVATE
            agent_memory::agent_memory
    )
endif()
