foreach(required_var
    AGENT_MEMORY_BENCH_EXE
    AGENT_MEMORY_BENCH_CONFIG
    AGENT_MEMORY_BENCH_OUTPUT
    AGENT_MEMORY_BENCH_WORKDIR
)
    if(NOT DEFINED ${required_var})
        message(FATAL_ERROR "${required_var} must be defined")
    endif()
endforeach()

execute_process(
    COMMAND
        "${AGENT_MEMORY_BENCH_EXE}"
        "${AGENT_MEMORY_BENCH_CONFIG}"
        "${AGENT_MEMORY_BENCH_OUTPUT}"
    WORKING_DIRECTORY "${AGENT_MEMORY_BENCH_WORKDIR}"
    RESULT_VARIABLE bench_result
    OUTPUT_VARIABLE bench_stdout
    ERROR_VARIABLE bench_stderr
)
if(NOT bench_result EQUAL 0)
    message(STATUS "benchmark stdout:\n${bench_stdout}")
    message(STATUS "benchmark stderr:\n${bench_stderr}")
    message(FATAL_ERROR "precomputed multilingual E5 embedding benchmark failed")
endif()

file(READ "${AGENT_MEMORY_BENCH_OUTPUT}" report_json)
string(JSON schema_version GET "${report_json}" schema_version)
string(JSON mode GET "${report_json}" mode)
string(JSON dataset_name GET "${report_json}" dataset_name)
string(JSON corpus_size GET "${report_json}" corpus_size)
string(JSON query_count GET "${report_json}" query_count)
if(NOT schema_version EQUAL 1
   OR NOT mode STREQUAL "precomputed_embedding_binary_rerank_grid"
   OR NOT dataset_name STREQUAL "precomputed-embedding-multilingual-e5-small"
   OR NOT corpus_size EQUAL 12
   OR NOT query_count EQUAL 6)
    message(FATAL_ERROR "unexpected multilingual E5 benchmark report identity")
endif()

string(JSON model_id GET "${report_json}" embedding_model model_id)
string(JSON model_dimension GET "${report_json}" embedding_model dimension)
string(JSON model_pooling GET "${report_json}" embedding_model pooling_mode)
string(JSON model_normalized GET "${report_json}" embedding_model normalized)
if(NOT model_id STREQUAL "intfloat/multilingual-e5-small"
   OR NOT model_dimension EQUAL 384
   OR NOT model_pooling STREQUAL "model_default"
   OR NOT model_normalized)
    message(FATAL_ERROR "unexpected multilingual E5 embedding model metadata")
endif()

string(JSON artifact_generator GET "${report_json}" embedding_artifact generator_id)
string(JSON artifact_version GET "${report_json}" embedding_artifact generator_version)
string(JSON artifact_dataset_revision GET "${report_json}" embedding_artifact dataset_revision)
string(JSON artifact_generator_revision GET "${report_json}" embedding_artifact generator_revision)
string(JSON artifact_source_hash GET "${report_json}" embedding_artifact generator_source_hash)
string(JSON artifact_contract_source_hash GET
    "${report_json}" embedding_artifact generator_contract_source_hash
)
string(JSON artifact_requirements GET
    "${report_json}" embedding_artifact generator_requirements_lock
)
string(JSON artifact_model_revision GET "${report_json}" embedding_artifact model_revision)
string(JSON artifact_tokenizer_revision GET
    "${report_json}" embedding_artifact tokenizer_revision
)
string(JSON artifact_document_prompt GET
    "${report_json}" embedding_artifact document_prompt_id
)
string(JSON artifact_query_prompt GET
    "${report_json}" embedding_artifact query_prompt_id
)
string(JSON artifact_projection GET "${report_json}" embedding_artifact projection_kind)
if(NOT artifact_generator STREQUAL "agent-memory.tools.multilingual-e5-precomputed-embedding"
   OR NOT artifact_version STREQUAL "v1"
   OR NOT artifact_dataset_revision STREQUAL "agent-memory-multilingual-e5-small-fixture:2026-07-31"
   OR NOT artifact_generator_revision STREQUAL "agent-memory-cpp:multilingual-e5-small-fixture-v1"
   OR NOT artifact_model_revision STREQUAL "614241f622f53c4eeff9890bdc4f31cfecc418b3"
   OR NOT artifact_tokenizer_revision STREQUAL "614241f622f53c4eeff9890bdc4f31cfecc418b3"
   OR NOT artifact_document_prompt STREQUAL "e5-passage-prefix-title-plus-text-v1"
   OR NOT artifact_query_prompt STREQUAL "e5-query-prefix-query-text-v1"
   OR NOT artifact_projection STREQUAL "multilingual_e5_small_sentence_transformers_normalized")
    message(FATAL_ERROR "multilingual E5 artifact provenance was not reported")
endif()

file(SHA256
    "${AGENT_MEMORY_BENCH_WORKDIR}/tools/agent-memory-bench/generate-precomputed-multilingual-e5-small-fixture.py"
    actual_generator_driver_hash
)
file(SHA256
    "${AGENT_MEMORY_BENCH_WORKDIR}/tools/agent-memory-bench/multilingual_e5_fixture_generator_common.py"
    actual_generator_common_hash
)
file(SHA256
    "${AGENT_MEMORY_BENCH_WORKDIR}/tools/agent-memory-bench/precomputed_fixture_contract.py"
    actual_canonical_contract_hash
)
file(SHA256
    "${AGENT_MEMORY_BENCH_WORKDIR}/tools/agent-memory-bench/precomputed_fixture_multilingual_e5_contract.py"
    actual_content_contract_hash
)
set(generator_hash_payload
    "${actual_generator_driver_hash}\n${actual_generator_common_hash}\n"
)
string(SHA256 actual_generator_source_hash "${generator_hash_payload}")
set(contract_hash_payload
    "${actual_canonical_contract_hash}\n${actual_content_contract_hash}\n"
)
string(SHA256 actual_contract_source_hash "${contract_hash_payload}")
file(SHA256
    "${AGENT_MEMORY_BENCH_WORKDIR}/tools/agent-memory-bench/requirements-multilingual-e5-small-fixture.txt"
    actual_requirements_lock_hash
)
set(expected_requirements_lock
    "tools/agent-memory-bench/requirements-multilingual-e5-small-fixture.txt;sha256=${actual_requirements_lock_hash}"
)
if(NOT actual_generator_source_hash STREQUAL artifact_source_hash
   OR NOT actual_contract_source_hash STREQUAL artifact_contract_source_hash
   OR NOT expected_requirements_lock STREQUAL artifact_requirements)
    message(FATAL_ERROR "multilingual E5 generator provenance does not match checked-in files")
endif()

string(JSON exact_recall GET "${report_json}" exact_oracle quality recall_at_10)
string(JSON exact_ndcg GET "${report_json}" exact_oracle quality ndcg_at_10)
if(NOT exact_recall EQUAL 1 OR NOT exact_ndcg EQUAL 1)
    message(FATAL_ERROR "multilingual E5 exact oracle must recover this fixture's qrels")
endif()

string(JSON report_count LENGTH "${report_json}" reports)
if(NOT report_count EQUAL 4)
    message(FATAL_ERROR "expected 4 multilingual E5 encoder reports, got ${report_count}")
endif()

foreach(report_index RANGE 0 3)
    if(report_index LESS 2)
        set(expected_encoder_family "random_hyperplane_rademacher")
    else()
        set(expected_encoder_family "randomized_hadamard_projection")
    endif()
    if(report_index EQUAL 0 OR report_index EQUAL 2)
        set(expected_bit_count 128)
    else()
        set(expected_bit_count 256)
    endif()
    string(JSON encoder_family GET "${report_json}" reports ${report_index} encoder_family)
    string(JSON bit_count GET "${report_json}" reports ${report_index} bit_count)
    string(JSON encoder_seed GET "${report_json}" reports ${report_index} encoder_seed)
    if(NOT encoder_family STREQUAL expected_encoder_family
       OR NOT bit_count EQUAL expected_bit_count
       OR NOT encoder_seed EQUAL 42)
        message(FATAL_ERROR "unexpected multilingual E5 encoder report identity")
    endif()

    string(JSON rerank_count LENGTH "${report_json}" reports ${report_index} rerank)
    if(NOT rerank_count EQUAL 3)
        message(FATAL_ERROR "each multilingual E5 encoder report needs 3 candidate rows")
    endif()
    foreach(rerank_index RANGE 0 2)
        if(rerank_index EQUAL 0)
            set(expected_candidate_limit 6)
        elseif(rerank_index EQUAL 1)
            set(expected_candidate_limit 9)
        else()
            set(expected_candidate_limit 12)
        endif()
        string(JSON candidate_limit GET
            "${report_json}" reports ${report_index} rerank ${rerank_index} candidate_limit
        )
        string(JSON exact_coverage GET
            "${report_json}" reports ${report_index} rerank ${rerank_index}
            exact_top_k_candidate_coverage
        )
        string(JSON qrels_coverage GET
            "${report_json}" reports ${report_index} rerank ${rerank_index}
            qrels_candidate_relevant_coverage
        )
        if(NOT candidate_limit EQUAL expected_candidate_limit
           OR exact_coverage LESS 0 OR exact_coverage GREATER 1
           OR qrels_coverage LESS 0 OR qrels_coverage GREATER 1)
            message(FATAL_ERROR "invalid multilingual E5 candidate row")
        endif()
    endforeach()
    string(JSON full_exact_coverage GET
        "${report_json}" reports ${report_index} rerank 2 exact_top_k_candidate_coverage
    )
    string(JSON full_qrels_coverage GET
        "${report_json}" reports ${report_index} rerank 2 qrels_candidate_relevant_coverage
    )
    if(NOT full_exact_coverage EQUAL 1 OR NOT full_qrels_coverage EQUAL 1)
        message(FATAL_ERROR "full multilingual E5 candidate row must cover exact top-k and qrels")
    endif()
endforeach()
