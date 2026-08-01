add_executable(agent_memory_smoke
    smoke/agent_memory_smoke.cpp
)

target_link_libraries(agent_memory_smoke
    PRIVATE
        agent_memory::agent_memory
)

add_test(NAME agent_memory_smoke COMMAND agent_memory_smoke)

file(GLOB_RECURSE agent_memory_public_headers
    CONFIGURE_DEPENDS
    RELATIVE ${PROJECT_SOURCE_DIR}/src
    ${PROJECT_SOURCE_DIR}/src/*.hpp
)
list(SORT agent_memory_public_headers)

set(agent_memory_header_self_sufficiency_sources)
set(agent_memory_header_self_sufficiency_dir
    ${CMAKE_CURRENT_BINARY_DIR}/header_self_sufficiency
)
file(MAKE_DIRECTORY ${agent_memory_header_self_sufficiency_dir})

foreach(agent_memory_public_header IN LISTS agent_memory_public_headers)
    string(MAKE_C_IDENTIFIER
        "${agent_memory_public_header}"
        agent_memory_header_source_name
    )
    set(agent_memory_header_source
        "${agent_memory_header_self_sufficiency_dir}/${agent_memory_header_source_name}.cpp"
    )
    file(WRITE
        "${agent_memory_header_source}"
        "#include <${agent_memory_public_header}>\n\n"
        "void ${agent_memory_header_source_name}_is_self_sufficient() {}\n"
    )
    list(APPEND agent_memory_header_self_sufficiency_sources
        "${agent_memory_header_source}"
    )
endforeach()

set(agent_memory_header_self_sufficiency_main
    "${agent_memory_header_self_sufficiency_dir}/main.cpp"
)
file(WRITE
    "${agent_memory_header_self_sufficiency_main}"
    "int main() { return 0; }\n"
)

add_executable(agent_memory_header_self_sufficiency_test
    ${agent_memory_header_self_sufficiency_main}
    ${agent_memory_header_self_sufficiency_sources}
)

target_link_libraries(agent_memory_header_self_sufficiency_test
    PRIVATE
        agent_memory::agent_memory
)

add_test(
    NAME agent_memory_header_self_sufficiency_test
    COMMAND agent_memory_header_self_sufficiency_test
)

add_executable(agent_memory_domain_primitives_test
    domain/domain_primitives_test.cpp
)

target_link_libraries(agent_memory_domain_primitives_test
    PRIVATE
        agent_memory::agent_memory
)

add_test(NAME agent_memory_domain_primitives_test COMMAND agent_memory_domain_primitives_test)

add_executable(agent_memory_knowledge_unit_test
    domain/knowledge_unit_test.cpp
)

target_link_libraries(agent_memory_knowledge_unit_test
    PRIVATE
        agent_memory::agent_memory
)

add_test(NAME agent_memory_knowledge_unit_test COMMAND agent_memory_knowledge_unit_test)

add_executable(agent_memory_resource_manifest_test
    domain/resource_manifest_test.cpp
)

target_link_libraries(agent_memory_resource_manifest_test
    PRIVATE
        agent_memory::agent_memory
)

add_test(NAME agent_memory_resource_manifest_test COMMAND agent_memory_resource_manifest_test)

add_executable(agent_memory_embedding_contracts_test
    embedding/embedding_contracts_test.cpp
)

target_link_libraries(agent_memory_embedding_contracts_test
    PRIVATE
        agent_memory::agent_memory
)

add_test(NAME agent_memory_embedding_contracts_test COMMAND agent_memory_embedding_contracts_test)

add_executable(agent_memory_evaluation_contracts_test
    eval/evaluation_contracts_test.cpp
)

target_link_libraries(agent_memory_evaluation_contracts_test
    PRIVATE
        agent_memory::agent_memory
)

add_test(NAME agent_memory_evaluation_contracts_test COMMAND agent_memory_evaluation_contracts_test)

add_executable(agent_memory_eval_runner_test
    eval/runner_test.cpp
)

target_link_libraries(agent_memory_eval_runner_test
    PRIVATE
        agent_memory::agent_memory
)

add_test(NAME agent_memory_eval_runner_test COMMAND agent_memory_eval_runner_test)

add_executable(agent_memory_benchmark_report_test
    eval/benchmark_report_test.cpp
)

target_link_libraries(agent_memory_benchmark_report_test
    PRIVATE
        agent_memory::agent_memory
        agent_memory::json
)

add_test(NAME agent_memory_benchmark_report_test COMMAND agent_memory_benchmark_report_test)

add_executable(agent_memory_retriever_adapter_test
    eval/retriever_adapter_test.cpp
)

target_link_libraries(agent_memory_retriever_adapter_test
    PRIVATE
        agent_memory::agent_memory
)

add_test(NAME agent_memory_retriever_adapter_test COMMAND agent_memory_retriever_adapter_test)

if(AGENT_MEMORY_ENABLE_JSON)
    add_executable(agent_memory_autoencoder_binary_artifact_test
        eval/autoencoder_binary_artifact_test.cpp
    )

    target_link_libraries(agent_memory_autoencoder_binary_artifact_test
        PRIVATE
            agent_memory::agent_memory
            agent_memory::json
    )

    add_test(
        NAME agent_memory_autoencoder_binary_artifact_test
        COMMAND agent_memory_autoencoder_binary_artifact_test
    )

    add_executable(agent_memory_dataset_loader_test
        eval/loader_test.cpp
    )

    target_link_libraries(agent_memory_dataset_loader_test
        PRIVATE
            agent_memory::agent_memory
            agent_memory::json
    )

    target_include_directories(agent_memory_dataset_loader_test
        PRIVATE
            ${CMAKE_CURRENT_SOURCE_DIR}/eval/fixtures
    )

    add_test(
        NAME agent_memory_dataset_loader_test
        COMMAND agent_memory_dataset_loader_test
        WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}/eval
    )

    add_executable(agent_memory_precomputed_embedding_dataset_loader_test
        eval/precomputed_embedding_dataset_loader_test.cpp
    )

    target_link_libraries(agent_memory_precomputed_embedding_dataset_loader_test
        PRIVATE
            agent_memory::agent_memory
            agent_memory::json
    )

    add_test(
        NAME agent_memory_precomputed_embedding_dataset_loader_test
        COMMAND agent_memory_precomputed_embedding_dataset_loader_test
        WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}/eval
    )

    add_executable(agent_memory_synthetic_sweep_test
        eval/synthetic_sweep_test.cpp
    )

    target_link_libraries(agent_memory_synthetic_sweep_test
        PRIVATE
            agent_memory::agent_memory
            agent_memory::json
    )

    target_include_directories(agent_memory_synthetic_sweep_test
        PRIVATE
            ${CMAKE_CURRENT_SOURCE_DIR}/eval/fixtures
    )

    add_test(
        NAME agent_memory_synthetic_sweep_test
        COMMAND agent_memory_synthetic_sweep_test
        WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}/eval
    )
endif()

add_executable(agent_memory_vector_index_contracts_test
    index/vector_index_contracts_test.cpp
)

target_link_libraries(agent_memory_vector_index_contracts_test
    PRIVATE
        agent_memory::agent_memory
)

add_test(NAME agent_memory_vector_index_contracts_test COMMAND agent_memory_vector_index_contracts_test)

add_executable(agent_memory_vector_similarity_computer_test
    index/vector_similarity_computer_test.cpp
)

target_link_libraries(agent_memory_vector_similarity_computer_test
    PRIVATE
        agent_memory::agent_memory
)

add_test(
    NAME agent_memory_vector_similarity_computer_test
    COMMAND agent_memory_vector_similarity_computer_test
)

add_executable(agent_memory_binary_signature_test
    index/binary_signature_test.cpp
)

target_link_libraries(agent_memory_binary_signature_test
    PRIVATE
        agent_memory::agent_memory
)

add_test(NAME agent_memory_binary_signature_test COMMAND agent_memory_binary_signature_test)

add_executable(agent_memory_aggregate_binary_signature_test
    index/aggregate_binary_signature_test.cpp
)

target_link_libraries(agent_memory_aggregate_binary_signature_test
    PRIVATE
        agent_memory::agent_memory
)

add_test(
    NAME agent_memory_aggregate_binary_signature_test
    COMMAND agent_memory_aggregate_binary_signature_test
)

add_executable(agent_memory_flat_binary_signature_index_test
    index/flat_binary_signature_index_test.cpp
)

target_link_libraries(agent_memory_flat_binary_signature_index_test
    PRIVATE
        agent_memory::agent_memory
)

add_test(
    NAME agent_memory_flat_binary_signature_index_test
    COMMAND agent_memory_flat_binary_signature_index_test
)

add_executable(agent_memory_multi_probe_hamming_index_test
    index/multi_probe_hamming_index_test.cpp
)

target_link_libraries(agent_memory_multi_probe_hamming_index_test
    PRIVATE
        agent_memory::agent_memory
)

add_test(
    NAME agent_memory_multi_probe_hamming_index_test
    COMMAND agent_memory_multi_probe_hamming_index_test
)

add_executable(agent_memory_binary_signature_encoder_test
    index/binary_signature_encoder_test.cpp
)

target_link_libraries(agent_memory_binary_signature_encoder_test
    PRIVATE
        agent_memory::agent_memory
)

add_test(NAME agent_memory_binary_signature_encoder_test COMMAND agent_memory_binary_signature_encoder_test)

add_executable(agent_memory_binary_signature_encoder_registry_test
    index/binary_signature_encoder_registry_test.cpp
)

target_link_libraries(agent_memory_binary_signature_encoder_registry_test
    PRIVATE
        agent_memory::agent_memory
)

add_test(
    NAME agent_memory_binary_signature_encoder_registry_test
    COMMAND agent_memory_binary_signature_encoder_registry_test
)

add_executable(agent_memory_exact_vector_index_test
    index/exact_vector_index_test.cpp
)

target_link_libraries(agent_memory_exact_vector_index_test
    PRIVATE
        agent_memory::agent_memory
)

add_test(NAME agent_memory_exact_vector_index_test COMMAND agent_memory_exact_vector_index_test)

add_executable(agent_memory_retrieval_contracts_test
    retrieval/retrieval_contracts_test.cpp
)

target_link_libraries(agent_memory_retrieval_contracts_test
    PRIVATE
        agent_memory::agent_memory
)

add_test(NAME agent_memory_retrieval_contracts_test COMMAND agent_memory_retrieval_contracts_test)

if(AGENT_MEMORY_ENABLE_JSON)
    add_executable(agent_memory_exact_vector_retriever_test
        retrieval/exact_vector_retriever_test.cpp
    )

    target_link_libraries(agent_memory_exact_vector_retriever_test
        PRIVATE
            agent_memory::agent_memory
            agent_memory::json
    )

    target_include_directories(agent_memory_exact_vector_retriever_test
        PRIVATE
            ${CMAKE_CURRENT_SOURCE_DIR}/eval/fixtures
    )

    add_test(
        NAME agent_memory_exact_vector_retriever_test
        COMMAND agent_memory_exact_vector_retriever_test
        WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}/retrieval
    )
endif()

add_executable(agent_memory_exact_lexical_retriever_test
    retrieval/exact_lexical_retriever_test.cpp
)

target_link_libraries(agent_memory_exact_lexical_retriever_test
    PRIVATE
        agent_memory::agent_memory
)

add_test(
    NAME agent_memory_exact_lexical_retriever_test
    COMMAND agent_memory_exact_lexical_retriever_test
    WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}/retrieval
)

add_executable(agent_memory_resource_indexer_test
    ingestion/resource_indexer_test.cpp
)

target_link_libraries(agent_memory_resource_indexer_test
    PRIVATE
        agent_memory::agent_memory
)

add_test(NAME agent_memory_resource_indexer_test COMMAND agent_memory_resource_indexer_test)

add_executable(agent_memory_tokenizer_contracts_test
    lexical/tokenizer_contracts_test.cpp
)

target_link_libraries(agent_memory_tokenizer_contracts_test
    PRIVATE
        agent_memory::agent_memory
)

add_test(NAME agent_memory_tokenizer_contracts_test COMMAND agent_memory_tokenizer_contracts_test)

add_executable(agent_memory_standard_tokenizer_test
    lexical/standard_tokenizer_test.cpp
)

target_link_libraries(agent_memory_standard_tokenizer_test
    PRIVATE
        agent_memory::agent_memory
)

add_test(NAME agent_memory_standard_tokenizer_test COMMAND agent_memory_standard_tokenizer_test)

add_executable(agent_memory_lexical_value_types_test
    lexical/lexical_value_types_test.cpp
)

target_link_libraries(agent_memory_lexical_value_types_test
    PRIVATE
        agent_memory::agent_memory
)

add_test(NAME agent_memory_lexical_value_types_test COMMAND agent_memory_lexical_value_types_test)

add_executable(agent_memory_token_dictionary_contract_test
    lexical/token_dictionary_contract_test.cpp
)

target_link_libraries(agent_memory_token_dictionary_contract_test
    PRIVATE
        agent_memory::agent_memory
)

add_test(
    NAME agent_memory_token_dictionary_contract_test
    COMMAND agent_memory_token_dictionary_contract_test
)

add_executable(agent_memory_lexical_index_contract_test
    lexical/lexical_index_contract_test.cpp
)

target_link_libraries(agent_memory_lexical_index_contract_test
    PRIVATE
        agent_memory::agent_memory
)

add_test(
    NAME agent_memory_lexical_index_contract_test
    COMMAND agent_memory_lexical_index_contract_test
)

add_executable(agent_memory_exact_lexical_index_test
    lexical/exact_lexical_index_test.cpp
)

target_link_libraries(agent_memory_exact_lexical_index_test
    PRIVATE
        agent_memory::agent_memory
)

add_test(
    NAME agent_memory_exact_lexical_index_test
    COMMAND agent_memory_exact_lexical_index_test
)

add_executable(agent_memory_keyword_overlap_index_test
    lexical/keyword_overlap_index_test.cpp
)

target_link_libraries(agent_memory_keyword_overlap_index_test
    PRIVATE
        agent_memory::agent_memory
)

add_test(
    NAME agent_memory_keyword_overlap_index_test
    COMMAND agent_memory_keyword_overlap_index_test
)

add_executable(agent_memory_scaffolding_test
    lexical/scaffolding_test.cpp
)

target_link_libraries(agent_memory_scaffolding_test
    PRIVATE
        agent_memory::agent_memory
)

add_test(
    NAME agent_memory_scaffolding_test
    COMMAND agent_memory_scaffolding_test
)

add_executable(agent_memory_document_storage_contract_test
    storage/document_storage_contract_test.cpp
)

target_link_libraries(agent_memory_document_storage_contract_test
    PRIVATE
        agent_memory::agent_memory
)

add_test(
    NAME agent_memory_document_storage_contract_test
    COMMAND agent_memory_document_storage_contract_test
)

add_executable(agent_memory_resource_manifest_storage_contract_test
    storage/resource_manifest_storage_contract_test.cpp
)

target_link_libraries(agent_memory_resource_manifest_storage_contract_test
    PRIVATE
        agent_memory::agent_memory
)

add_test(
    NAME agent_memory_resource_manifest_storage_contract_test
    COMMAND agent_memory_resource_manifest_storage_contract_test
)

add_executable(agent_memory_feature_flags_test
    storage/feature_flags_test.cpp
)

target_link_libraries(agent_memory_feature_flags_test
    PRIVATE
        agent_memory::agent_memory
)

target_compile_definitions(agent_memory_feature_flags_test
    PRIVATE
        AGENT_MEMORY_EXPECTED_HAS_MDBX=$<BOOL:${AGENT_MEMORY_ENABLE_MDBX}>
)

add_test(NAME agent_memory_feature_flags_test COMMAND agent_memory_feature_flags_test)

if(AGENT_MEMORY_ENABLE_MDBX)
    add_executable(agent_memory_mdbx_document_storage_test
        infrastructure/mdbx/mdbx_document_storage_test.cpp
    )

    target_link_libraries(agent_memory_mdbx_document_storage_test
        PRIVATE
            agent_memory::agent_memory
    )

    add_test(
        NAME agent_memory_mdbx_document_storage_test
        COMMAND agent_memory_mdbx_document_storage_test
    )

    add_executable(agent_memory_mdbx_resource_manifest_storage_test
        infrastructure/mdbx/mdbx_resource_manifest_storage_test.cpp
    )

    target_link_libraries(agent_memory_mdbx_resource_manifest_storage_test
        PRIVATE
            agent_memory::agent_memory
            mdbx_containers::mdbx_containers
    )

    add_test(
        NAME agent_memory_mdbx_resource_manifest_storage_test
        COMMAND agent_memory_mdbx_resource_manifest_storage_test
    )
endif()
