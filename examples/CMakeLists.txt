add_executable(agent_memory_basic_usage
    basic_usage.cpp
)

add_executable(agent_memory_external_identity external_identity.cpp)
target_link_libraries(agent_memory_external_identity PRIVATE agent_memory::agent_memory)

add_executable(agent_memory_conversation conversation.cpp)
target_link_libraries(agent_memory_conversation PRIVATE agent_memory::agent_memory)
set_target_properties(agent_memory_conversation PROPERTIES
    CXX_STANDARD 17 CXX_STANDARD_REQUIRED YES CXX_EXTENSIONS NO)

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

add_executable(agent_memory_prompt_composition
    prompt_composition.cpp
)

target_link_libraries(agent_memory_prompt_composition
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
