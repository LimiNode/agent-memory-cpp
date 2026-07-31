foreach(required_var
        AGENT_MEMORY_BINARY_LIFECYCLE_BENCH_EXE
        AGENT_MEMORY_BINARY_LIFECYCLE_BENCH_CONFIG
        AGENT_MEMORY_BINARY_LIFECYCLE_BENCH_OUTPUT
        AGENT_MEMORY_BINARY_LIFECYCLE_BENCH_WORKDIR)
    if(NOT DEFINED ${required_var} OR "${${required_var}}" STREQUAL "")
        message(FATAL_ERROR "${required_var} is required")
    endif()
endforeach()

execute_process(
    COMMAND
        "${AGENT_MEMORY_BINARY_LIFECYCLE_BENCH_EXE}"
        "${AGENT_MEMORY_BINARY_LIFECYCLE_BENCH_CONFIG}"
        "${AGENT_MEMORY_BINARY_LIFECYCLE_BENCH_OUTPUT}"
    WORKING_DIRECTORY "${AGENT_MEMORY_BINARY_LIFECYCLE_BENCH_WORKDIR}"
    RESULT_VARIABLE bench_result
    OUTPUT_VARIABLE bench_stdout
    ERROR_VARIABLE bench_stderr
)

if(NOT bench_result EQUAL 0)
    message(FATAL_ERROR
        "binary lifecycle benchmark failed with ${bench_result}\n"
        "stdout:\n${bench_stdout}\n"
        "stderr:\n${bench_stderr}"
    )
endif()

if(NOT EXISTS "${AGENT_MEMORY_BINARY_LIFECYCLE_BENCH_OUTPUT}")
    message(FATAL_ERROR
        "binary lifecycle benchmark did not create output: "
        "${AGENT_MEMORY_BINARY_LIFECYCLE_BENCH_OUTPUT}"
    )
endif()

file(READ "${AGENT_MEMORY_BINARY_LIFECYCLE_BENCH_OUTPUT}" report_json)

function(require_json out_var)
    list(GET ARGN 0 json_operation)
    list(REMOVE_AT ARGN 0)
    string(
        JSON value
        ERROR_VARIABLE json_error
        ${json_operation}
        "${report_json}"
        ${ARGN}
    )
    if(json_error)
        message(FATAL_ERROR "missing or invalid JSON field ${ARGN}: ${json_error}")
    endif()
    set(${out_var} "${value}" PARENT_SCOPE)
endfunction()

function(require_nonnegative value label)
    if(${value} LESS 0)
        message(FATAL_ERROR "${label} must be non-negative, got ${value}")
    endif()
endfunction()

function(require_range value label min_value max_value)
    if(${value} LESS ${min_value} OR ${value} GREATER ${max_value})
        message(FATAL_ERROR
            "${label} must be in [${min_value}, ${max_value}], got ${value}"
        )
    endif()
endfunction()

function(require_nonempty_string_path label)
    require_json(value GET ${ARGN})
    if("${value}" STREQUAL "")
        message(FATAL_ERROR "${label} must be a non-empty string")
    endif()
    set(${label} "${value}" PARENT_SCOPE)
endfunction()

require_json(schema_version GET schema_version)
if(NOT schema_version EQUAL 1)
    message(FATAL_ERROR "schema_version must be 1")
endif()

require_json(mode GET mode)
if(NOT mode STREQUAL "synthetic_binary_lifecycle")
    message(FATAL_ERROR "unexpected mode: ${mode}")
endif()

require_json(document_count GET document_count)
require_json(query_count GET query_count)
require_json(embedding_dimensions GET embedding_dimensions)
require_json(bit_count GET bit_count)
require_json(oracle_k GET oracle_k)
require_json(returned_candidate_limit GET returned_candidate_limit)
require_json(result_limit GET result_limit)
string(JSON rerank_candidate_limit_count
    LENGTH
    "${report_json}"
    rerank_candidate_limits
)
require_json(mutation_count GET mutation_count)
require_json(seed GET seed)
require_json(query_noise_seed GET query_noise_seed)

if(NOT document_count EQUAL 256)
    message(FATAL_ERROR "document_count must stay 256")
endif()
if(NOT query_count EQUAL 64)
    message(FATAL_ERROR "query_count must stay 64")
endif()
if(NOT embedding_dimensions EQUAL 64)
    message(FATAL_ERROR "embedding_dimensions must stay 64")
endif()
if(NOT bit_count EQUAL 128)
    message(FATAL_ERROR "bit_count must stay 128")
endif()
if(NOT oracle_k EQUAL 10)
    message(FATAL_ERROR "oracle_k must stay 10")
endif()
if(NOT returned_candidate_limit EQUAL 16)
    message(FATAL_ERROR "returned_candidate_limit must stay 16")
endif()
if(NOT result_limit EQUAL returned_candidate_limit)
    message(FATAL_ERROR
        "legacy result_limit must match returned_candidate_limit"
    )
endif()
if(NOT rerank_candidate_limit_count EQUAL 3)
    message(FATAL_ERROR "rerank_candidate_limits must contain three smoke entries")
endif()
foreach(rerank_limit_index RANGE 0 2)
    require_json(rerank_limit GET rerank_candidate_limits ${rerank_limit_index})
    if(rerank_limit_index EQUAL 0 AND NOT rerank_limit EQUAL 16)
        message(FATAL_ERROR "first rerank candidate limit must stay 16")
    endif()
    if(rerank_limit_index EQUAL 1 AND NOT rerank_limit EQUAL 32)
        message(FATAL_ERROR "second rerank candidate limit must stay 32")
    endif()
    if(rerank_limit_index EQUAL 2 AND NOT rerank_limit EQUAL 64)
        message(FATAL_ERROR "third rerank candidate limit must stay 64")
    endif()
endforeach()
if(NOT mutation_count EQUAL 16)
    message(FATAL_ERROR "mutation_count must stay 16")
endif()
if(NOT seed EQUAL 42)
    message(FATAL_ERROR "seed must stay 42")
endif()
if(NOT query_noise_seed EQUAL 11400714819323198527)
    message(FATAL_ERROR "query_noise_seed must stay derived from seed 42")
endif()

function(require_json_number out_var label)
    require_json(value_type TYPE ${ARGN})
    if(NOT value_type STREQUAL "NUMBER")
        message(FATAL_ERROR
            "${label} must be a JSON number, got ${value_type}"
        )
    endif()
    require_json(value GET ${ARGN})
    set(${out_var} "${value}" PARENT_SCOPE)
endfunction()

function(require_nonnegative_path label)
    require_json_number(value "${label}" ${ARGN})
    require_nonnegative(${value} "${label}")
endfunction()

require_nonnegative_path("timing_ms.data_generation" timing_ms data_generation)
require_nonnegative_path(
    "timing_ms.binary_chunk_and_query_encoding"
    timing_ms
    binary_chunk_and_query_encoding
)
require_nonnegative_path(
    "timing_ms.rerank_prepare_ms"
    timing_ms
    rerank_prepare_ms
)
require_nonnegative_path("exact_vector.build_ms" exact_vector build_ms)
require_nonnegative_path("exact_vector.query_total_ms" exact_vector query_total_ms)
require_nonnegative_path("exact_vector.query_mean_ms" exact_vector query_mean_ms)
require_nonnegative_path(
    "exact_vector.query_mean_result_count"
    exact_vector
    query_mean_result_count
)
require_nonnegative_path("flat_binary.build_ms" flat_binary build_ms)
require_nonnegative_path("flat_binary.rebuild_ms" flat_binary rebuild_ms)
require_nonnegative_path("flat_binary.query.total_ms" flat_binary query total_ms)
require_nonnegative_path("flat_binary.query.mean_ms" flat_binary query mean_ms)
require_nonnegative_path(
    "flat_binary.query.mean_result_count"
    flat_binary
    query
    mean_result_count
)
require_nonnegative_path(
    "flat_binary.post_upsert_query.total_ms"
    flat_binary
    post_upsert_query
    total_ms
)
require_nonnegative_path(
    "flat_binary.post_upsert_query.mean_ms"
    flat_binary
    post_upsert_query
    mean_ms
)
require_nonnegative_path(
    "flat_binary.post_upsert_query.mean_result_count"
    flat_binary
    post_upsert_query
    mean_result_count
)
require_nonnegative_path(
    "flat_binary.post_rebuild_query.total_ms"
    flat_binary
    post_rebuild_query
    total_ms
)
require_nonnegative_path(
    "flat_binary.post_rebuild_query.mean_ms"
    flat_binary
    post_rebuild_query
    mean_ms
)
require_nonnegative_path(
    "flat_binary.post_rebuild_query.mean_result_count"
    flat_binary
    post_rebuild_query
    mean_result_count
)
require_nonnegative_path(
    "flat_binary.mutations.erase_ms"
    flat_binary
    mutations
    erase_ms
)
require_nonnegative_path(
    "flat_binary.mutations.upsert_ms"
    flat_binary
    mutations
    upsert_ms
)
require_nonnegative_path("multiprobe_binary.build_ms" multiprobe_binary build_ms)
require_nonnegative_path(
    "multiprobe_binary.rebuild_ms"
    multiprobe_binary
    rebuild_ms
)
require_nonnegative_path(
    "multiprobe_binary.query.total_ms"
    multiprobe_binary
    query
    total_ms
)
require_nonnegative_path(
    "multiprobe_binary.query.mean_ms"
    multiprobe_binary
    query
    mean_ms
)
require_nonnegative_path(
    "multiprobe_binary.query.mean_result_count"
    multiprobe_binary
    query
    mean_result_count
)
require_nonnegative_path(
    "multiprobe_binary.post_upsert_query.total_ms"
    multiprobe_binary
    post_upsert_query
    total_ms
)
require_nonnegative_path(
    "multiprobe_binary.post_upsert_query.mean_ms"
    multiprobe_binary
    post_upsert_query
    mean_ms
)
require_nonnegative_path(
    "multiprobe_binary.post_upsert_query.mean_result_count"
    multiprobe_binary
    post_upsert_query
    mean_result_count
)
require_nonnegative_path(
    "multiprobe_binary.post_rebuild_query.total_ms"
    multiprobe_binary
    post_rebuild_query
    total_ms
)
require_nonnegative_path(
    "multiprobe_binary.post_rebuild_query.mean_ms"
    multiprobe_binary
    post_rebuild_query
    mean_ms
)
require_nonnegative_path(
    "multiprobe_binary.post_rebuild_query.mean_result_count"
    multiprobe_binary
    post_rebuild_query
    mean_result_count
)
require_nonnegative_path(
    "multiprobe_binary.mutations.erase_ms"
    multiprobe_binary
    mutations
    erase_ms
)
require_nonnegative_path(
    "multiprobe_binary.mutations.upsert_ms"
    multiprobe_binary
    mutations
    upsert_ms
)
require_nonnegative_path(
    "multiprobe_binary.mean_candidate_count"
    multiprobe_binary
    mean_candidate_count
)
require_nonnegative_path(
    "multiprobe_binary.mean_probed_bucket_count"
    multiprobe_binary
    mean_probed_bucket_count
)
require_nonnegative_path(
    "multiprobe_binary.mean_visited_posting_count"
    multiprobe_binary
    mean_visited_posting_count
)
require_nonnegative_path(
    "process_peak_resident_set_bytes"
    process_peak_resident_set_bytes
)

require_nonempty_string_path(exact_similarity_backend
    exact_vector
    similarity_backend
)
if(NOT exact_similarity_backend MATCHES "^(scalar|sse2|avx2)$")
    message(FATAL_ERROR
        "unexpected exact vector similarity backend: ${exact_similarity_backend}"
    )
endif()
require_nonempty_string_path(flat_hamming_backend
    flat_binary
    hamming_backend
)
if(NOT flat_hamming_backend MATCHES "^(lookup_table|hardware_popcount|avx2_simd)$")
    message(FATAL_ERROR
        "unexpected flat binary Hamming backend: ${flat_hamming_backend}"
    )
endif()

require_json(flat_coverage
    GET flat_binary query exact_top_k_candidate_coverage)
require_json(multiprobe_coverage
    GET multiprobe_binary query exact_top_k_candidate_coverage)
require_range(${flat_coverage} "flat coverage" 0 1)
require_range(${multiprobe_coverage} "multiprobe coverage" 0 1)

require_json(flat_post_upsert_coverage
    GET flat_binary post_upsert_query exact_top_k_candidate_coverage)
require_json(flat_post_rebuild_coverage
    GET flat_binary post_rebuild_query exact_top_k_candidate_coverage)
require_json(multiprobe_post_upsert_coverage
    GET multiprobe_binary post_upsert_query exact_top_k_candidate_coverage)
require_json(multiprobe_post_rebuild_coverage
    GET multiprobe_binary post_rebuild_query exact_top_k_candidate_coverage)
if(NOT flat_post_upsert_coverage EQUAL flat_coverage)
    message(FATAL_ERROR "flat coverage must return after upsert")
endif()
if(NOT flat_post_rebuild_coverage EQUAL flat_coverage)
    message(FATAL_ERROR "flat coverage must return after rebuild")
endif()
if(NOT multiprobe_post_upsert_coverage EQUAL multiprobe_coverage)
    message(FATAL_ERROR "multi-probe coverage must return after upsert")
endif()
if(NOT multiprobe_post_rebuild_coverage EQUAL multiprobe_coverage)
    message(FATAL_ERROR "multi-probe coverage must return after rebuild")
endif()

require_json(exact_mean_result_count
    GET exact_vector query_mean_result_count)
require_json(flat_mean_result_count
    GET flat_binary query mean_result_count)
require_json(multiprobe_mean_result_count
    GET multiprobe_binary query mean_result_count)
require_json(flat_post_upsert_mean_result_count
    GET flat_binary post_upsert_query mean_result_count)
require_json(flat_post_rebuild_mean_result_count
    GET flat_binary post_rebuild_query mean_result_count)
require_json(multiprobe_post_upsert_mean_result_count
    GET multiprobe_binary post_upsert_query mean_result_count)
require_json(multiprobe_post_rebuild_mean_result_count
    GET multiprobe_binary post_rebuild_query mean_result_count)
foreach(result_count_label
        exact_mean_result_count
)
    if(NOT ${result_count_label} EQUAL oracle_k)
        message(FATAL_ERROR
            "${result_count_label} must be ${oracle_k}, "
            "got ${${result_count_label}}"
        )
    endif()
endforeach()
foreach(result_count_label
        flat_mean_result_count
        multiprobe_mean_result_count
        flat_post_upsert_mean_result_count
        flat_post_rebuild_mean_result_count
        multiprobe_post_upsert_mean_result_count
        multiprobe_post_rebuild_mean_result_count)
    if(NOT ${result_count_label} EQUAL returned_candidate_limit)
        message(FATAL_ERROR
            "${result_count_label} must be ${returned_candidate_limit}, "
            "got ${${result_count_label}}"
        )
    endif()
endforeach()

function(require_rerank_grid backend_label)
    string(JSON row_count
        LENGTH
        "${report_json}"
        ${ARGN}
        rerank
    )
    if(NOT row_count EQUAL 3)
        message(FATAL_ERROR "${backend_label}.rerank must contain three rows")
    endif()

    set(previous_flat_coverage -1)
    foreach(row_index RANGE 0 2)
        require_json_number(candidate_limit
            "${backend_label}.rerank[${row_index}].candidate_limit"
            ${ARGN} rerank ${row_index} candidate_limit)
        if(row_index EQUAL 0)
            set(expected_limit 16)
        elseif(row_index EQUAL 1)
            set(expected_limit 32)
        else()
            set(expected_limit 64)
        endif()
        if(NOT candidate_limit EQUAL expected_limit)
            message(FATAL_ERROR
                "${backend_label}.rerank[${row_index}].candidate_limit must be "
                "${expected_limit}, got ${candidate_limit}"
            )
        endif()

        require_json_number(binary_search_total_ms
            "${backend_label}.rerank[${row_index}].binary_search_total_ms"
            ${ARGN} rerank ${row_index} binary_search_total_ms)
        require_json_number(binary_search_mean_ms
            "${backend_label}.rerank[${row_index}].binary_search_mean_ms"
            ${ARGN} rerank ${row_index} binary_search_mean_ms)
        require_json_number(binary_mean_result_count
            "${backend_label}.rerank[${row_index}].binary_mean_result_count"
            ${ARGN} rerank ${row_index} binary_mean_result_count)
        require_json_number(candidate_coverage
            "${backend_label}.rerank[${row_index}].exact_top_k_candidate_coverage"
            ${ARGN} rerank ${row_index} exact_top_k_candidate_coverage)
        require_json_number(exact_rerank_total_ms
            "${backend_label}.rerank[${row_index}].exact_rerank_total_ms"
            ${ARGN} rerank ${row_index} exact_rerank_total_ms)
        require_json_number(exact_rerank_mean_ms
            "${backend_label}.rerank[${row_index}].exact_rerank_mean_ms"
            ${ARGN} rerank ${row_index} exact_rerank_mean_ms)
        require_json_number(reranked_recall
            "${backend_label}.rerank[${row_index}].reranked_recall_at_k_vs_exact"
            ${ARGN} rerank ${row_index} reranked_recall_at_k_vs_exact)
        require_json_number(reranked_top1
            "${backend_label}.rerank[${row_index}].reranked_top1_agreement"
            ${ARGN} rerank ${row_index} reranked_top1_agreement)
        require_json_number(end_to_end_total_ms
            "${backend_label}.rerank[${row_index}].end_to_end_total_ms"
            ${ARGN} rerank ${row_index} end_to_end_total_ms)
        require_json_number(end_to_end_mean_ms
            "${backend_label}.rerank[${row_index}].end_to_end_mean_ms"
            ${ARGN} rerank ${row_index} end_to_end_mean_ms)

        require_nonnegative(${binary_search_total_ms}
            "${backend_label}.rerank[${row_index}].binary_search_total_ms")
        require_nonnegative(${binary_search_mean_ms}
            "${backend_label}.rerank[${row_index}].binary_search_mean_ms")
        require_nonnegative(${exact_rerank_total_ms}
            "${backend_label}.rerank[${row_index}].exact_rerank_total_ms")
        require_nonnegative(${exact_rerank_mean_ms}
            "${backend_label}.rerank[${row_index}].exact_rerank_mean_ms")
        require_nonnegative(${end_to_end_total_ms}
            "${backend_label}.rerank[${row_index}].end_to_end_total_ms")
        require_nonnegative(${end_to_end_mean_ms}
            "${backend_label}.rerank[${row_index}].end_to_end_mean_ms")
        require_range(${candidate_coverage}
            "${backend_label}.rerank[${row_index}].coverage" 0 1)
        require_range(${reranked_recall}
            "${backend_label}.rerank[${row_index}].reranked_recall" 0 1)
        require_range(${reranked_top1}
            "${backend_label}.rerank[${row_index}].reranked_top1" 0 1)
        if("${backend_label}" STREQUAL "flat_binary")
            if(NOT binary_mean_result_count EQUAL candidate_limit)
                message(FATAL_ERROR
                    "${backend_label}.rerank[${row_index}].binary_mean_result_count "
                    "must match candidate_limit ${candidate_limit}, got "
                    "${binary_mean_result_count}"
                )
            endif()
        else()
            if(NOT binary_mean_result_count GREATER 0
               OR binary_mean_result_count GREATER candidate_limit)
                message(FATAL_ERROR
                    "${backend_label}.rerank[${row_index}].binary_mean_result_count "
                    "must be in (0, ${candidate_limit}], got "
                    "${binary_mean_result_count}"
                )
            endif()
        endif()
        if(NOT reranked_recall EQUAL candidate_coverage)
            message(FATAL_ERROR
                "${backend_label}.rerank[${row_index}] exact rerank recall "
                "must match candidate coverage for the same exact scorer"
            )
        endif()
        if("${backend_label}" STREQUAL "flat_binary")
            if(candidate_coverage LESS previous_flat_coverage)
                message(FATAL_ERROR
                    "flat rerank coverage must not decrease as candidate_limit grows"
                )
            endif()
            set(previous_flat_coverage ${candidate_coverage})
        else()
            require_json_number(mean_candidate_count
                "${backend_label}.rerank[${row_index}].mean_candidate_count"
                ${ARGN} rerank ${row_index} mean_candidate_count)
            require_json_number(mean_probed_bucket_count
                "${backend_label}.rerank[${row_index}].mean_probed_bucket_count"
                ${ARGN} rerank ${row_index} mean_probed_bucket_count)
            require_json_number(mean_visited_posting_count
                "${backend_label}.rerank[${row_index}].mean_visited_posting_count"
                ${ARGN} rerank ${row_index} mean_visited_posting_count)
            if(NOT mean_candidate_count GREATER 0)
                message(FATAL_ERROR
                    "${backend_label}.rerank[${row_index}].mean_candidate_count "
                    "must be positive"
                )
            endif()
            if(NOT mean_probed_bucket_count GREATER 0)
                message(FATAL_ERROR
                    "${backend_label}.rerank[${row_index}].mean_probed_bucket_count "
                    "must be positive"
                )
            endif()
            if(mean_visited_posting_count LESS mean_candidate_count)
                message(FATAL_ERROR
                    "${backend_label}.rerank[${row_index}].mean_visited_posting_count "
                    "must be at least mean_candidate_count"
                )
            endif()
        endif()
    endforeach()
endfunction()

require_rerank_grid("flat_binary" flat_binary)
require_rerank_grid("multiprobe_binary" multiprobe_binary)

require_json(multiprobe_mean_candidate_count
    GET multiprobe_binary mean_candidate_count)
require_range(${multiprobe_mean_candidate_count}
    "multi-probe mean candidate count" 0 ${document_count})
require_json(multiprobe_mean_probed_bucket_count
    GET multiprobe_binary mean_probed_bucket_count)
require_json(multiprobe_mean_visited_posting_count
    GET multiprobe_binary mean_visited_posting_count)
if(NOT multiprobe_mean_candidate_count GREATER 0)
    message(FATAL_ERROR "multi-probe mean candidate count must be positive")
endif()
if(NOT multiprobe_mean_probed_bucket_count GREATER 0)
    message(FATAL_ERROR "multi-probe mean probed bucket count must be positive")
endif()
if(multiprobe_mean_visited_posting_count LESS multiprobe_mean_candidate_count)
    message(FATAL_ERROR
        "multi-probe visited postings must be at least candidate count"
    )
endif()

require_json(flat_erased GET flat_binary mutations erased_count)
require_json(multiprobe_erased GET multiprobe_binary mutations erased_count)
if(NOT flat_erased EQUAL mutation_count)
    message(FATAL_ERROR "flat erase count must match mutation_count")
endif()
if(NOT multiprobe_erased EQUAL mutation_count)
    message(FATAL_ERROR "multiprobe erase count must match mutation_count")
endif()

require_json(exact_payload GET exact_vector payload_bytes)
require_json(flat_payload GET flat_binary payload_bytes)
require_json(multiprobe_payload GET multiprobe_binary payload_bytes)
math(EXPR expected_exact_payload
    "${document_count} * ${embedding_dimensions} * 4"
)
math(EXPR binary_word_count "(${bit_count} + 63) / 64")
math(EXPR expected_binary_payload
    "${document_count} * ${binary_word_count} * 8"
)
if(NOT exact_payload EQUAL expected_exact_payload)
    message(FATAL_ERROR
        "exact payload must be ${expected_exact_payload}, got ${exact_payload}"
    )
endif()
if(NOT flat_payload EQUAL expected_binary_payload)
    message(FATAL_ERROR
        "flat binary payload must be ${expected_binary_payload}, got ${flat_payload}"
    )
endif()
if(NOT flat_payload EQUAL multiprobe_payload)
    message(FATAL_ERROR "binary payload bytes should match between binary indexes")
endif()

require_json(exact_size_after_build GET exact_vector size_after_build)
require_json(flat_size_after_build GET flat_binary size_after_build)
require_json(flat_size_before_erase
    GET flat_binary mutations size_before_erase)
require_json(flat_size_after_erase
    GET flat_binary mutations size_after_erase)
require_json(flat_size_after_upsert
    GET flat_binary mutations size_after_upsert)
require_json(flat_size_after_clear GET flat_binary size_after_clear)
require_json(flat_size_after_rebuild GET flat_binary size_after_rebuild)
require_json(multiprobe_size_after_build
    GET multiprobe_binary size_after_build)
require_json(multiprobe_size_before_erase
    GET multiprobe_binary mutations size_before_erase)
require_json(multiprobe_size_after_erase
    GET multiprobe_binary mutations size_after_erase)
require_json(multiprobe_size_after_upsert
    GET multiprobe_binary mutations size_after_upsert)
require_json(multiprobe_size_after_clear
    GET multiprobe_binary size_after_clear)
require_json(multiprobe_size_after_rebuild
    GET multiprobe_binary size_after_rebuild)
math(EXPR expected_after_erase "${document_count} - ${mutation_count}")
foreach(size_label
        exact_size_after_build
        flat_size_after_build
        flat_size_before_erase
        flat_size_after_upsert
        flat_size_after_rebuild
        multiprobe_size_after_build
        multiprobe_size_before_erase
        multiprobe_size_after_upsert
        multiprobe_size_after_rebuild)
    if(NOT ${size_label} EQUAL document_count)
        message(FATAL_ERROR
            "${size_label} must be ${document_count}, got ${${size_label}}"
        )
    endif()
endforeach()
if(NOT flat_size_after_erase EQUAL expected_after_erase)
    message(FATAL_ERROR
        "flat_size_after_erase must be ${expected_after_erase}"
    )
endif()
if(NOT multiprobe_size_after_erase EQUAL expected_after_erase)
    message(FATAL_ERROR
        "multiprobe_size_after_erase must be ${expected_after_erase}"
    )
endif()
if(NOT flat_size_after_clear EQUAL 0)
    message(FATAL_ERROR "flat_size_after_clear must be 0")
endif()
if(NOT multiprobe_size_after_clear EQUAL 0)
    message(FATAL_ERROR "multiprobe_size_after_clear must be 0")
endif()

require_json(table_count GET multiprobe_binary options table_count)
require_json(bits_per_table GET multiprobe_binary options bits_per_table)
require_json(max_probe_radius GET multiprobe_binary options max_probe_radius)
require_json(candidate_multiplier
    GET multiprobe_binary options candidate_multiplier)
require_json(minimum_candidate_count
    GET multiprobe_binary options minimum_candidate_count)
if(NOT table_count EQUAL 8)
    message(FATAL_ERROR "multiprobe table_count must stay 8")
endif()
if(NOT bits_per_table EQUAL 8)
    message(FATAL_ERROR "multiprobe bits_per_table must stay 8")
endif()
if(NOT max_probe_radius EQUAL 1)
    message(FATAL_ERROR "multiprobe max_probe_radius must stay 1")
endif()
if(NOT candidate_multiplier EQUAL 64)
    message(FATAL_ERROR "multiprobe candidate_multiplier must stay 64")
endif()
if(NOT minimum_candidate_count EQUAL 64)
    message(FATAL_ERROR "multiprobe minimum_candidate_count must stay 64")
endif()
