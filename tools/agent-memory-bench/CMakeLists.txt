add_executable(agent-memory-bench
    main.cpp
)

target_link_libraries(agent-memory-bench
    PRIVATE
        agent_memory::agent_memory
    agent_memory::json
)

add_executable(agent-memory-autoencoder-eval
    autoencoder_evaluation.cpp
)

function(agent_memory_set_evaluator_source_manifest target)
    set(manifest_text "")
    foreach(source_file IN LISTS ARGN)
        set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${source_file}")
        file(SHA256 "${source_file}" source_sha256)
        file(RELATIVE_PATH source_relative_path "${PROJECT_SOURCE_DIR}" "${source_file}")
        string(APPEND manifest_text "${source_relative_path}:${source_sha256}\n")
    endforeach()
    string(SHA256 manifest_sha256 "${manifest_text}")
    target_compile_definitions(${target} PRIVATE
        AGENT_MEMORY_EVALUATOR_SOURCE_MANIFEST_SHA256="${manifest_sha256}"
    )
endfunction()

function(agent_memory_set_evaluator_build_environment target)
    math(EXPR pointer_bits "${CMAKE_SIZEOF_VOID_P} * 8")
    if(CMAKE_CXX_EXTENSIONS)
        set(cxx_extensions 1)
    else()
        set(cxx_extensions 0)
    endif()
    string(SHA256 base_cxx_flags_sha256 "${CMAKE_CXX_FLAGS}")
    if(CMAKE_CONFIGURATION_TYPES)
        set(build_configuration_definition "$<CONFIG>")
        set(active_configuration_flags_definition "")
        foreach(configuration IN LISTS CMAKE_CONFIGURATION_TYPES)
            string(TOUPPER "${configuration}" configuration_uppercase)
            string(SHA256 configuration_flags_sha256
                "${CMAKE_CXX_FLAGS_${configuration_uppercase}}")
            string(APPEND active_configuration_flags_definition
                "$<$<CONFIG:${configuration}>:${configuration_flags_sha256}>")
        endforeach()
    else()
        set(build_configuration_definition "${CMAKE_BUILD_TYPE}")
        if(CMAKE_BUILD_TYPE)
            string(TOUPPER "${CMAKE_BUILD_TYPE}" configuration_uppercase)
            set(active_configuration_flags "${CMAKE_CXX_FLAGS_${configuration_uppercase}}")
        else()
            set(active_configuration_flags "")
        endif()
        string(SHA256 active_configuration_flags_definition "${active_configuration_flags}")
    endif()
    set(build_manifest_text
        "compiler_id:${CMAKE_CXX_COMPILER_ID}\n"
        "compiler_version:${CMAKE_CXX_COMPILER_VERSION}\n"
        "cxx_standard:${CMAKE_CXX_STANDARD}\n"
        "cxx_extensions:${cxx_extensions}\n"
        "generator:${CMAKE_GENERATOR}\n"
        "system_name:${CMAKE_SYSTEM_NAME}\n"
        "system_processor:${CMAKE_SYSTEM_PROCESSOR}\n"
        "pointer_bits:${pointer_bits}\n"
        "base_cxx_flags_sha256:${base_cxx_flags_sha256}\n"
    )
    string(SHA256 build_manifest_sha256 "${build_manifest_text}")
    target_compile_definitions(${target} PRIVATE
        AGENT_MEMORY_EVALUATOR_CONFIGURED_ENVIRONMENT_SHA256="${build_manifest_sha256}"
        AGENT_MEMORY_EVALUATOR_COMPILER_ID="${CMAKE_CXX_COMPILER_ID}"
        AGENT_MEMORY_EVALUATOR_COMPILER_VERSION="${CMAKE_CXX_COMPILER_VERSION}"
        AGENT_MEMORY_EVALUATOR_CXX_STANDARD=${CMAKE_CXX_STANDARD}
        AGENT_MEMORY_EVALUATOR_CXX_EXTENSIONS=${cxx_extensions}
        AGENT_MEMORY_EVALUATOR_GENERATOR="${CMAKE_GENERATOR}"
        AGENT_MEMORY_EVALUATOR_BUILD_CONFIGURATION="${build_configuration_definition}"
        AGENT_MEMORY_EVALUATOR_SYSTEM_NAME="${CMAKE_SYSTEM_NAME}"
        AGENT_MEMORY_EVALUATOR_SYSTEM_PROCESSOR="${CMAKE_SYSTEM_PROCESSOR}"
        AGENT_MEMORY_EVALUATOR_POINTER_BITS=${pointer_bits}
        AGENT_MEMORY_EVALUATOR_BASE_CXX_FLAGS_SHA256="${base_cxx_flags_sha256}"
        AGENT_MEMORY_EVALUATOR_ACTIVE_CONFIGURATION_FLAGS_SHA256="${active_configuration_flags_definition}"
    )
endfunction()

agent_memory_set_evaluator_source_manifest(agent-memory-autoencoder-eval
    "${CMAKE_CURRENT_SOURCE_DIR}/autoencoder_evaluation.cpp"
    "${PROJECT_SOURCE_DIR}/src/agent_memory/eval/AutoencoderBinaryArtifact.cpp"
    "${PROJECT_SOURCE_DIR}/src/agent_memory/eval/AutoencoderBinaryEvaluation.cpp"
    "${PROJECT_SOURCE_DIR}/src/agent_memory/index/AsymmetricBinarySignatureScorer.cpp"
    "${PROJECT_SOURCE_DIR}/src/agent_memory/eval/Evaluation.cpp"
    "${PROJECT_SOURCE_DIR}/src/agent_memory/index/BinarySignature.cpp"
    "${PROJECT_SOURCE_DIR}/src/agent_memory/index/VectorSimilarityComputer.cpp"
    "${PROJECT_SOURCE_DIR}/src/agent_memory/index/AutoencoderBinaryEncoder.cpp"
)
agent_memory_set_evaluator_build_environment(agent-memory-autoencoder-eval)

target_link_libraries(agent-memory-autoencoder-eval
    PRIVATE
        agent_memory::agent_memory
        agent_memory::json
)

add_executable(agent-memory-autoencoder-cascade-eval
    autoencoder_cascade_evaluation.cpp
)
agent_memory_set_evaluator_source_manifest(agent-memory-autoencoder-cascade-eval
    "${CMAKE_CURRENT_SOURCE_DIR}/autoencoder_cascade_evaluation.cpp"
    "${PROJECT_SOURCE_DIR}/src/agent_memory/eval/AutoencoderBinaryArtifact.cpp"
    "${PROJECT_SOURCE_DIR}/src/agent_memory/eval/AutoencoderBinaryEvaluation.cpp"
    "${PROJECT_SOURCE_DIR}/src/agent_memory/eval/Evaluation.cpp"
    "${PROJECT_SOURCE_DIR}/src/agent_memory/index/AsymmetricBinarySignatureScorer.cpp"
    "${PROJECT_SOURCE_DIR}/src/agent_memory/index/BinarySignature.cpp"
    "${PROJECT_SOURCE_DIR}/src/agent_memory/index/VectorSimilarityComputer.cpp"
    "${PROJECT_SOURCE_DIR}/src/agent_memory/index/AutoencoderBinaryEncoder.cpp"
)
agent_memory_set_evaluator_build_environment(agent-memory-autoencoder-cascade-eval)
target_link_libraries(agent-memory-autoencoder-cascade-eval
    PRIVATE
        agent_memory::agent_memory
        agent_memory::json
)

add_executable(agent-memory-standard-binary-eval
    standard_binary_encoder_evaluation.cpp
)

agent_memory_set_evaluator_source_manifest(agent-memory-standard-binary-eval
    "${CMAKE_CURRENT_SOURCE_DIR}/standard_binary_encoder_evaluation.cpp"
    "${PROJECT_SOURCE_DIR}/src/agent_memory/eval/AutoencoderBinaryArtifact.cpp"
    "${PROJECT_SOURCE_DIR}/src/agent_memory/eval/Evaluation.cpp"
    "${PROJECT_SOURCE_DIR}/src/agent_memory/index/BinarySignature.cpp"
    "${PROJECT_SOURCE_DIR}/src/agent_memory/index/VectorSimilarityComputer.cpp"
    "${PROJECT_SOURCE_DIR}/src/agent_memory/index/CoordinateSignBinaryEncoder.cpp"
    "${PROJECT_SOURCE_DIR}/src/agent_memory/index/ItqRotationBinaryEncoder.cpp"
    "${PROJECT_SOURCE_DIR}/src/agent_memory/index/LearnedProjectionBinaryEncoder.cpp"
    "${PROJECT_SOURCE_DIR}/src/agent_memory/index/PcaProjectionBinaryEncoder.cpp"
    "${PROJECT_SOURCE_DIR}/src/agent_memory/index/RandomizedHadamardBinaryEncoder.cpp"
    "${PROJECT_SOURCE_DIR}/src/agent_memory/index/RandomHyperplaneBinaryEncoder.cpp"
)
agent_memory_set_evaluator_build_environment(agent-memory-standard-binary-eval)

target_link_libraries(agent-memory-standard-binary-eval
    PRIVATE
        agent_memory::agent_memory
        agent_memory::json
)

if(WIN32)
    target_link_libraries(agent-memory-bench PRIVATE Psapi)
endif()

add_executable(agent-memory-hamming-hot-path-bench
    hamming_hot_path.cpp
)

target_link_libraries(agent-memory-hamming-hot-path-bench
    PRIVATE
        agent_memory::agent_memory
        agent_memory::json
)

add_executable(agent-memory-mih-native-hot-path
    mih_native_hot_path.cpp
)

agent_memory_set_evaluator_source_manifest(agent-memory-mih-native-hot-path
    "${CMAKE_CURRENT_SOURCE_DIR}/mih_native_hot_path.cpp"
    "${CMAKE_CURRENT_SOURCE_DIR}/materialize-mih-storage-input.py"
    "${PROJECT_SOURCE_DIR}/src/agent_memory/index/VectorSimilarityComputer.cpp"
    "${PROJECT_SOURCE_DIR}/src/agent_memory/index/BinarySignature.cpp"
)
agent_memory_set_evaluator_build_environment(agent-memory-mih-native-hot-path)
file(SHA256 "${CMAKE_CURRENT_SOURCE_DIR}/mih_native_hot_path.cpp" agent_memory_native_hot_path_source_sha256)
file(SHA256 "${CMAKE_CURRENT_SOURCE_DIR}/materialize-mih-storage-input.py" agent_memory_native_hot_path_materializer_source_sha256)
file(SHA256 "${PROJECT_SOURCE_DIR}/src/agent_memory/index/VectorSimilarityComputer.cpp" agent_memory_native_hot_path_vector_similarity_source_sha256)
file(SHA256 "${PROJECT_SOURCE_DIR}/src/agent_memory/index/BinarySignature.cpp" agent_memory_native_hot_path_binary_signature_source_sha256)
target_compile_definitions(agent-memory-mih-native-hot-path
    PRIVATE
        AGENT_MEMORY_NATIVE_HOT_PATH_SOURCE_SHA256="${agent_memory_native_hot_path_source_sha256}"
        AGENT_MEMORY_NATIVE_HOT_PATH_MATERIALIZER_SOURCE_SHA256="${agent_memory_native_hot_path_materializer_source_sha256}"
        AGENT_MEMORY_NATIVE_HOT_PATH_VECTOR_SIMILARITY_SOURCE_SHA256="${agent_memory_native_hot_path_vector_similarity_source_sha256}"
        AGENT_MEMORY_NATIVE_HOT_PATH_BINARY_SIGNATURE_SOURCE_SHA256="${agent_memory_native_hot_path_binary_signature_source_sha256}"
)

target_link_libraries(agent-memory-mih-native-hot-path
    PRIVATE
        agent_memory::agent_memory
        agent_memory::json
)

add_executable(agent-memory-native-centroid-routing-scan
    native_centroid_routing_scan.cpp
)

target_link_libraries(agent-memory-native-centroid-routing-scan
    PRIVATE
        agent_memory::json
)

add_executable(agent-memory-mih-native-sparse-arbitrary-m
    mih_native_sparse_arbitrary_m.cpp
)

agent_memory_set_evaluator_source_manifest(agent-memory-mih-native-sparse-arbitrary-m
    "${CMAKE_CURRENT_SOURCE_DIR}/mih_native_sparse_arbitrary_m.cpp"
    "${CMAKE_CURRENT_SOURCE_DIR}/materialize-mih-storage-input.py"
    "${PROJECT_SOURCE_DIR}/src/agent_memory/index/VectorSimilarityComputer.cpp"
    "${PROJECT_SOURCE_DIR}/src/agent_memory/index/BinarySignature.cpp"
)
agent_memory_set_evaluator_build_environment(agent-memory-mih-native-sparse-arbitrary-m)
file(SHA256 "${CMAKE_CURRENT_SOURCE_DIR}/mih_native_sparse_arbitrary_m.cpp" agent_memory_native_sparse_arbitrary_m_source_sha256)
file(SHA256 "${CMAKE_CURRENT_SOURCE_DIR}/materialize-mih-storage-input.py" agent_memory_native_sparse_arbitrary_m_materializer_source_sha256)
file(SHA256 "${PROJECT_SOURCE_DIR}/src/agent_memory/index/VectorSimilarityComputer.cpp" agent_memory_native_sparse_arbitrary_m_vector_similarity_source_sha256)
file(SHA256 "${PROJECT_SOURCE_DIR}/src/agent_memory/index/BinarySignature.cpp" agent_memory_native_sparse_arbitrary_m_binary_signature_source_sha256)
target_compile_definitions(agent-memory-mih-native-sparse-arbitrary-m
    PRIVATE
        AGENT_MEMORY_NATIVE_SPARSE_ARBITRARY_M_SOURCE_SHA256="${agent_memory_native_sparse_arbitrary_m_source_sha256}"
        AGENT_MEMORY_NATIVE_SPARSE_ARBITRARY_M_MATERIALIZER_SOURCE_SHA256="${agent_memory_native_sparse_arbitrary_m_materializer_source_sha256}"
        AGENT_MEMORY_NATIVE_SPARSE_ARBITRARY_M_VECTOR_SIMILARITY_SOURCE_SHA256="${agent_memory_native_sparse_arbitrary_m_vector_similarity_source_sha256}"
        AGENT_MEMORY_NATIVE_SPARSE_ARBITRARY_M_BINARY_SIGNATURE_SOURCE_SHA256="${agent_memory_native_sparse_arbitrary_m_binary_signature_source_sha256}"
)

target_link_libraries(agent-memory-mih-native-sparse-arbitrary-m
    PRIVATE
        agent_memory::agent_memory
        agent_memory::json
)

if(AGENT_MEMORY_ENABLE_HNSW_BENCHMARK)
    if(NOT EXISTS "${PROJECT_SOURCE_DIR}/external/hnswlib/hnswlib/hnswlib.h")
        message(FATAL_ERROR
            "AGENT_MEMORY_ENABLE_HNSW_BENCHMARK requires external/hnswlib")
    endif()
    execute_process(
        COMMAND git -C "${PROJECT_SOURCE_DIR}/external/hnswlib" rev-parse HEAD
        RESULT_VARIABLE agent_memory_hnswlib_revision_result
        OUTPUT_VARIABLE agent_memory_hnswlib_revision
        OUTPUT_STRIP_TRAILING_WHITESPACE
    )
    string(LENGTH "${agent_memory_hnswlib_revision}" agent_memory_hnswlib_revision_length)
    if(NOT agent_memory_hnswlib_revision_result EQUAL 0 OR
       NOT agent_memory_hnswlib_revision MATCHES "^[0-9a-f]+$" OR
       NOT agent_memory_hnswlib_revision_length EQUAL 40)
        message(FATAL_ERROR
            "AGENT_MEMORY_ENABLE_HNSW_BENCHMARK requires a pinned hnswlib Git revision")
    endif()
    add_executable(agent-memory-native-centroid-routing
        native_centroid_routing.cpp
    )
    target_link_libraries(agent-memory-native-centroid-routing
        PRIVATE
            agent_memory::json
    )
    target_include_directories(agent-memory-mih-native-sparse-arbitrary-m
        PRIVATE
            "${PROJECT_SOURCE_DIR}/external/hnswlib"
    )
    target_include_directories(agent-memory-native-centroid-routing
        PRIVATE
            "${PROJECT_SOURCE_DIR}/external/hnswlib"
    )
    target_compile_definitions(agent-memory-mih-native-sparse-arbitrary-m
        PRIVATE
            AGENT_MEMORY_ENABLE_HNSW_BENCHMARK=1
            AGENT_MEMORY_HNSWLIB_REVISION="${agent_memory_hnswlib_revision}"
    )
    target_compile_definitions(agent-memory-native-centroid-routing
        PRIVATE
            AGENT_MEMORY_HNSWLIB_REVISION="${agent_memory_hnswlib_revision}"
    )
endif()

if(AGENT_MEMORY_ENABLE_MDBX)
    set(agent_memory_storage_provenance_authoritative 0)
    set(agent_memory_storage_provenance_reason "non_pinned_dependency_resolution")
    set(agent_memory_libmdbx_revision "unavailable")
    set(agent_memory_mdbx_containers_revision "unavailable")
    if(AGENT_MEMORY_MDBX_DEPS_MODE STREQUAL "BUNDLED"
       AND "${AGENT_MEMORY_MDBX_CONTAINERS_SOURCE_DIR}" STREQUAL ""
       AND EXISTS "${PROJECT_SOURCE_DIR}/external/libmdbx/.git"
       AND EXISTS "${PROJECT_SOURCE_DIR}/external/mdbx-containers/.git")
        find_program(agent_memory_storage_git_executable git)
        if(agent_memory_storage_git_executable)
            execute_process(
                COMMAND ${agent_memory_storage_git_executable} -C ${PROJECT_SOURCE_DIR}/external/libmdbx rev-parse HEAD
                OUTPUT_VARIABLE agent_memory_libmdbx_revision
                OUTPUT_STRIP_TRAILING_WHITESPACE
                RESULT_VARIABLE agent_memory_libmdbx_revision_result
                ERROR_QUIET
            )
            execute_process(
                COMMAND ${agent_memory_storage_git_executable} -C ${PROJECT_SOURCE_DIR}/external/mdbx-containers rev-parse HEAD
                OUTPUT_VARIABLE agent_memory_mdbx_containers_revision
                OUTPUT_STRIP_TRAILING_WHITESPACE
                RESULT_VARIABLE agent_memory_mdbx_containers_revision_result
                ERROR_QUIET
            )
            execute_process(
                COMMAND ${agent_memory_storage_git_executable} -C ${PROJECT_SOURCE_DIR} rev-parse HEAD:external/libmdbx
                OUTPUT_VARIABLE agent_memory_pinned_libmdbx_revision
                OUTPUT_STRIP_TRAILING_WHITESPACE
                RESULT_VARIABLE agent_memory_pinned_libmdbx_revision_result
                ERROR_QUIET
            )
            execute_process(
                COMMAND ${agent_memory_storage_git_executable} -C ${PROJECT_SOURCE_DIR} rev-parse HEAD:external/mdbx-containers
                OUTPUT_VARIABLE agent_memory_pinned_mdbx_containers_revision
                OUTPUT_STRIP_TRAILING_WHITESPACE
                RESULT_VARIABLE agent_memory_pinned_mdbx_containers_revision_result
                ERROR_QUIET
            )
            execute_process(
                COMMAND ${agent_memory_storage_git_executable} -C ${PROJECT_SOURCE_DIR}/external/libmdbx diff --quiet
                RESULT_VARIABLE agent_memory_libmdbx_worktree_clean_result
                ERROR_QUIET
            )
            execute_process(
                COMMAND ${agent_memory_storage_git_executable} -C ${PROJECT_SOURCE_DIR}/external/libmdbx diff --cached --quiet
                RESULT_VARIABLE agent_memory_libmdbx_index_clean_result
                ERROR_QUIET
            )
            execute_process(
                COMMAND ${agent_memory_storage_git_executable} -C ${PROJECT_SOURCE_DIR}/external/mdbx-containers diff --quiet
                RESULT_VARIABLE agent_memory_mdbx_containers_worktree_clean_result
                ERROR_QUIET
            )
            execute_process(
                COMMAND ${agent_memory_storage_git_executable} -C ${PROJECT_SOURCE_DIR}/external/mdbx-containers diff --cached --quiet
                RESULT_VARIABLE agent_memory_mdbx_containers_index_clean_result
                ERROR_QUIET
            )
            if(agent_memory_libmdbx_revision_result EQUAL 0
               AND agent_memory_mdbx_containers_revision_result EQUAL 0
               AND agent_memory_pinned_libmdbx_revision_result EQUAL 0
               AND agent_memory_pinned_mdbx_containers_revision_result EQUAL 0
               AND agent_memory_libmdbx_worktree_clean_result EQUAL 0
               AND agent_memory_libmdbx_index_clean_result EQUAL 0
               AND agent_memory_mdbx_containers_worktree_clean_result EQUAL 0
               AND agent_memory_mdbx_containers_index_clean_result EQUAL 0
               AND agent_memory_libmdbx_revision STREQUAL agent_memory_pinned_libmdbx_revision
               AND agent_memory_mdbx_containers_revision STREQUAL agent_memory_pinned_mdbx_containers_revision)
                set(agent_memory_storage_provenance_authoritative 1)
                set(agent_memory_storage_provenance_reason "repository_pinned_external_submodules")
            endif()
        endif()
    endif()
    add_executable(agent-memory-mih-storage-bench
        mih_storage_benchmark.cpp
    )
    agent_memory_set_evaluator_source_manifest(agent-memory-mih-storage-bench
        "${CMAKE_CURRENT_SOURCE_DIR}/mih_storage_benchmark.cpp"
    )
    agent_memory_set_evaluator_build_environment(agent-memory-mih-storage-bench)
    target_compile_definitions(agent-memory-mih-storage-bench
        PRIVATE
            AGENT_MEMORY_LIBMDBX_REVISION="${agent_memory_libmdbx_revision}"
            AGENT_MEMORY_MDBX_CONTAINERS_REVISION="${agent_memory_mdbx_containers_revision}"
            AGENT_MEMORY_STORAGE_PROVENANCE_AUTHORITATIVE=${agent_memory_storage_provenance_authoritative}
            AGENT_MEMORY_STORAGE_PROVENANCE_REASON="${agent_memory_storage_provenance_reason}"
    )
    target_link_libraries(agent-memory-mih-storage-bench
        PRIVATE
            agent_memory::agent_memory
            agent_memory::json
            mdbx_containers::mdbx_containers
    )
    add_executable(agent-memory-neuroute-native-mdbx-cost
        neuroute_native_mdbx_cost.cpp
    )
    agent_memory_set_evaluator_source_manifest(agent-memory-neuroute-native-mdbx-cost
        "${CMAKE_CURRENT_SOURCE_DIR}/neuroute_native_mdbx_cost.cpp"
    )
    agent_memory_set_evaluator_build_environment(agent-memory-neuroute-native-mdbx-cost)
    target_compile_definitions(agent-memory-neuroute-native-mdbx-cost
        PRIVATE
            AGENT_MEMORY_LIBMDBX_REVISION="${agent_memory_libmdbx_revision}"
            AGENT_MEMORY_MDBX_CONTAINERS_REVISION="${agent_memory_mdbx_containers_revision}"
            AGENT_MEMORY_STORAGE_PROVENANCE_AUTHORITATIVE=${agent_memory_storage_provenance_authoritative}
            AGENT_MEMORY_STORAGE_PROVENANCE_REASON="${agent_memory_storage_provenance_reason}"
    )
    target_link_libraries(agent-memory-neuroute-native-mdbx-cost
        PRIVATE
            agent_memory::agent_memory
            agent_memory::json
            mdbx_containers::mdbx_containers
    )
endif()

add_executable(agent-memory-aggregate-signature-bench
    aggregate_signature.cpp
)

target_link_libraries(agent-memory-aggregate-signature-bench
    PRIVATE
        agent_memory::agent_memory
        agent_memory::json
)

add_executable(agent-memory-binary-lifecycle-bench
    binary_lifecycle.cpp
)

target_link_libraries(agent-memory-binary-lifecycle-bench
    PRIVATE
        agent_memory::agent_memory
        agent_memory::json
)

if(WIN32)
    target_link_libraries(agent-memory-binary-lifecycle-bench PRIVATE Psapi)
endif()

add_executable(agent-memory-precomputed-artifact-verify
    precomputed_artifact_verify.cpp
)

target_link_libraries(agent-memory-precomputed-artifact-verify
    PRIVATE
        agent_memory::json
)
