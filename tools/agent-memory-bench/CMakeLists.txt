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
        "${PROJECT_SOURCE_DIR}/src/agent_memory/index/BinarySignature.cpp"
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

add_executable(agent-memory-neuroute-exact-e5-rerank
    neuroute_exact_e5_rerank.cpp
)
agent_memory_set_evaluator_source_manifest(agent-memory-neuroute-exact-e5-rerank
    "${CMAKE_CURRENT_SOURCE_DIR}/neuroute_exact_e5_rerank.cpp"
)
agent_memory_set_evaluator_build_environment(agent-memory-neuroute-exact-e5-rerank)
target_link_libraries(agent-memory-neuroute-exact-e5-rerank
    PRIVATE
        agent_memory::agent_memory
        agent_memory::json
)

add_executable(agent-memory-neuroute-final-representation
    neuroute_final_representation.cpp
)

add_executable(agent-memory-neuroute-int6-codec neuroute_int6_codec.cpp)
agent_memory_set_evaluator_source_manifest(agent-memory-neuroute-int6-codec
    "${CMAKE_CURRENT_SOURCE_DIR}/neuroute_int6_codec.cpp")
agent_memory_set_evaluator_build_environment(agent-memory-neuroute-int6-codec)
target_link_libraries(agent-memory-neuroute-int6-codec PRIVATE agent_memory::agent_memory agent_memory::json)

set(AGENT_MEMORY_NEUROUTE_HAS_SIMDCOMP 0)
set(AGENT_MEMORY_NEUROUTE_HAS_SIMDCOMP_AVX2 0)
set(AGENT_MEMORY_NEUROUTE_FINAL_CODEC_SOURCES
    "${CMAKE_CURRENT_SOURCE_DIR}/neuroute_final_codec.cpp")
if(CMAKE_SYSTEM_PROCESSOR MATCHES "^(x86_64|AMD64|amd64|i[3-6]86)$"
        AND EXISTS "${PROJECT_SOURCE_DIR}/external/simdcomp/src/simdbitpacking.c")
    add_library(agent_memory_neuroute_simdcomp STATIC
        "${PROJECT_SOURCE_DIR}/external/simdcomp/src/simdbitpacking.c")
    set_source_files_properties(
        "${PROJECT_SOURCE_DIR}/external/simdcomp/src/simdbitpacking.c"
        PROPERTIES LANGUAGE CXX)
    target_include_directories(agent_memory_neuroute_simdcomp PUBLIC
        "${PROJECT_SOURCE_DIR}/external/simdcomp/include")
    target_compile_features(agent_memory_neuroute_simdcomp PRIVATE cxx_std_17)
    if(NOT MSVC)
        target_compile_options(agent_memory_neuroute_simdcomp PRIVATE -msse2)
    endif()
    if(AGENT_MEMORY_NEUROUTE_ENABLE_AVX2
            AND EXISTS "${PROJECT_SOURCE_DIR}/external/simdcomp/src/avxbitpacking.c")
        target_sources(agent_memory_neuroute_simdcomp PRIVATE
            "${PROJECT_SOURCE_DIR}/external/simdcomp/src/avxbitpacking.c"
            "${PROJECT_SOURCE_DIR}/external/simdcomp/src/simdcomputil.c")
        set_source_files_properties(
            "${PROJECT_SOURCE_DIR}/external/simdcomp/src/avxbitpacking.c"
            "${PROJECT_SOURCE_DIR}/external/simdcomp/src/simdcomputil.c"
            PROPERTIES LANGUAGE CXX)
        if(MSVC)
            target_compile_options(agent_memory_neuroute_simdcomp PRIVATE /arch:AVX2)
        else()
            target_compile_options(agent_memory_neuroute_simdcomp PRIVATE -mavx2)
        endif()
        set(AGENT_MEMORY_NEUROUTE_HAS_SIMDCOMP_AVX2 1)
        list(APPEND AGENT_MEMORY_NEUROUTE_FINAL_CODEC_SOURCES
            "${PROJECT_SOURCE_DIR}/external/simdcomp/src/avxbitpacking.c"
            "${PROJECT_SOURCE_DIR}/external/simdcomp/src/simdcomputil.c"
            "${PROJECT_SOURCE_DIR}/external/simdcomp/include/avxbitpacking.h"
            "${PROJECT_SOURCE_DIR}/external/simdcomp/include/simdcomputil.h")
    endif()
    set(AGENT_MEMORY_NEUROUTE_HAS_SIMDCOMP 1)
    list(APPEND AGENT_MEMORY_NEUROUTE_FINAL_CODEC_SOURCES
        "${PROJECT_SOURCE_DIR}/external/simdcomp/src/simdbitpacking.c"
        "${PROJECT_SOURCE_DIR}/external/simdcomp/include/simdbitpacking.h")
endif()

add_executable(agent-memory-neuroute-final-codec neuroute_final_codec.cpp)
target_compile_definitions(agent-memory-neuroute-final-codec PRIVATE
    AGENT_MEMORY_NEUROUTE_HAS_SIMDCOMP=${AGENT_MEMORY_NEUROUTE_HAS_SIMDCOMP})
target_link_libraries(agent-memory-neuroute-final-codec PRIVATE
    agent_memory::agent_memory agent_memory::json)
if(TARGET agent_memory_neuroute_simdcomp)
    target_link_libraries(agent-memory-neuroute-final-codec PRIVATE
        agent_memory_neuroute_simdcomp)
endif()
agent_memory_set_evaluator_source_manifest(agent-memory-neuroute-final-codec
    ${AGENT_MEMORY_NEUROUTE_FINAL_CODEC_SOURCES})
agent_memory_set_evaluator_build_environment(agent-memory-neuroute-final-codec)

add_executable(agent-memory-neuroute-full-corpus-codec
    neuroute_full_corpus_codec.cpp)
target_compile_definitions(agent-memory-neuroute-full-corpus-codec PRIVATE
    AGENT_MEMORY_NEUROUTE_HAS_SIMDCOMP=${AGENT_MEMORY_NEUROUTE_HAS_SIMDCOMP})
target_link_libraries(agent-memory-neuroute-full-corpus-codec PRIVATE
    agent_memory::agent_memory agent_memory::json)
if(TARGET agent_memory_neuroute_simdcomp)
    target_link_libraries(agent-memory-neuroute-full-corpus-codec PRIVATE
        agent_memory_neuroute_simdcomp)
endif()
if(WIN32)
    target_link_libraries(agent-memory-neuroute-full-corpus-codec PRIVATE Psapi)
endif()
agent_memory_set_evaluator_source_manifest(agent-memory-neuroute-full-corpus-codec
    "${CMAKE_CURRENT_SOURCE_DIR}/neuroute_full_corpus_codec.cpp"
    ${AGENT_MEMORY_NEUROUTE_FINAL_CODEC_SOURCES})
agent_memory_set_evaluator_build_environment(agent-memory-neuroute-full-corpus-codec)

add_executable(agent-memory-neuroute-r4-representative-codec
    neuroute_r4_representative_codec.cpp)
target_compile_definitions(agent-memory-neuroute-r4-representative-codec PRIVATE
    AGENT_MEMORY_NEUROUTE_HAS_SIMDCOMP=${AGENT_MEMORY_NEUROUTE_HAS_SIMDCOMP})
target_link_libraries(agent-memory-neuroute-r4-representative-codec PRIVATE
    agent_memory::agent_memory agent_memory::json)
if(TARGET agent_memory_neuroute_simdcomp)
    target_link_libraries(agent-memory-neuroute-r4-representative-codec PRIVATE
        agent_memory_neuroute_simdcomp)
endif()
agent_memory_set_evaluator_source_manifest(agent-memory-neuroute-r4-representative-codec
    "${CMAKE_CURRENT_SOURCE_DIR}/neuroute_r4_representative_codec.cpp"
    ${AGENT_MEMORY_NEUROUTE_FINAL_CODEC_SOURCES})
agent_memory_set_evaluator_build_environment(agent-memory-neuroute-r4-representative-codec)

add_executable(agent-memory-neuroute-r4-layout-benchmark
    neuroute_r4_layout_benchmark.cpp)
set(AGENT_MEMORY_NEUROUTE_R4_HAS_AVX2 0)
set(AGENT_MEMORY_NEUROUTE_R4_HAS_ZSTD 0)
if(EXISTS "${PROJECT_SOURCE_DIR}/external/zstd/build/cmake/CMakeLists.txt")
    set(ZSTD_BUILD_PROGRAMS OFF CACHE BOOL "" FORCE)
    set(ZSTD_BUILD_SHARED OFF CACHE BOOL "" FORCE)
    set(ZSTD_BUILD_STATIC ON CACHE BOOL "" FORCE)
    set(ZSTD_BUILD_TESTS OFF CACHE BOOL "" FORCE)
    set(ZSTD_BUILD_CONTRIB OFF CACHE BOOL "" FORCE)
    set(ZSTD_BUILD_DICTBUILDER ON CACHE BOOL "" FORCE)
    add_subdirectory("${PROJECT_SOURCE_DIR}/external/zstd/build/cmake"
        "${CMAKE_CURRENT_BINARY_DIR}/zstd" EXCLUDE_FROM_ALL)
    set(AGENT_MEMORY_NEUROUTE_R4_HAS_ZSTD 1)
endif()
if(AGENT_MEMORY_NEUROUTE_ENABLE_AVX2
        AND CMAKE_SYSTEM_PROCESSOR MATCHES "^(x86_64|AMD64|amd64|i[3-6]86)$")
    set(AGENT_MEMORY_NEUROUTE_R4_HAS_AVX2 1)
    if(MSVC)
        target_compile_options(agent-memory-neuroute-r4-layout-benchmark PRIVATE /arch:AVX2)
    else()
        target_compile_options(agent-memory-neuroute-r4-layout-benchmark PRIVATE -mavx2)
    endif()
endif()
target_compile_definitions(agent-memory-neuroute-r4-layout-benchmark PRIVATE
    AGENT_MEMORY_NEUROUTE_R4_HAS_AVX2=${AGENT_MEMORY_NEUROUTE_R4_HAS_AVX2}
    AGENT_MEMORY_NEUROUTE_R4_HAS_ZSTD=${AGENT_MEMORY_NEUROUTE_R4_HAS_ZSTD}
    AGENT_MEMORY_NEUROUTE_HAS_SIMDCOMP_AVX2=${AGENT_MEMORY_NEUROUTE_HAS_SIMDCOMP_AVX2}
    AGENT_MEMORY_NEUROUTE_HAS_SIMDCOMP=${AGENT_MEMORY_NEUROUTE_HAS_SIMDCOMP})
target_link_libraries(agent-memory-neuroute-r4-layout-benchmark PRIVATE
    agent_memory::agent_memory agent_memory::json)
if(TARGET agent_memory_neuroute_simdcomp)
    target_link_libraries(agent-memory-neuroute-r4-layout-benchmark PRIVATE
        agent_memory_neuroute_simdcomp)
endif()
if(TARGET libzstd_static)
    target_link_libraries(agent-memory-neuroute-r4-layout-benchmark PRIVATE
        libzstd_static)
endif()
if(WIN32)
    target_link_libraries(agent-memory-neuroute-r4-layout-benchmark PRIVATE Psapi)
endif()
agent_memory_set_evaluator_source_manifest(agent-memory-neuroute-r4-layout-benchmark
    "${CMAKE_CURRENT_SOURCE_DIR}/neuroute_r4_layout_benchmark.cpp"
    "${CMAKE_CURRENT_SOURCE_DIR}/neuroute_record_codec.hpp"
    "${CMAKE_CURRENT_SOURCE_DIR}/neuroute_record_codec.cpp"
    "${CMAKE_CURRENT_SOURCE_DIR}/neuroute_record_codec_sse2.cpp"
    "${CMAKE_CURRENT_SOURCE_DIR}/neuroute_record_codec_avx2.cpp"
    ${AGENT_MEMORY_NEUROUTE_FINAL_CODEC_SOURCES})
agent_memory_set_evaluator_build_environment(agent-memory-neuroute-r4-layout-benchmark)

set(AGENT_MEMORY_NEUROUTE_CODEC_HAS_SSE2 0)
set(AGENT_MEMORY_NEUROUTE_CODEC_HAS_AVX2 0)
add_library(agent_memory_neuroute_record_codec STATIC
    neuroute_record_codec.cpp
    neuroute_record_codec.hpp)
target_compile_features(agent_memory_neuroute_record_codec PRIVATE cxx_std_17)
target_include_directories(agent_memory_neuroute_record_codec PUBLIC
    "${CMAKE_CURRENT_SOURCE_DIR}")
if(CMAKE_SYSTEM_PROCESSOR MATCHES "^(x86_64|AMD64|amd64|i[3-6]86)$")
    target_sources(agent_memory_neuroute_record_codec PRIVATE
        neuroute_record_codec_sse2.cpp)
    if(NOT MSVC)
        set_source_files_properties(neuroute_record_codec_sse2.cpp
            PROPERTIES COMPILE_OPTIONS "-msse2")
    endif()
    set(AGENT_MEMORY_NEUROUTE_CODEC_HAS_SSE2 1)
    if(AGENT_MEMORY_NEUROUTE_ENABLE_AVX2)
        target_sources(agent_memory_neuroute_record_codec PRIVATE
            neuroute_record_codec_avx2.cpp)
        if(MSVC)
            set_source_files_properties(neuroute_record_codec_avx2.cpp
                PROPERTIES COMPILE_OPTIONS "/arch:AVX2")
        else()
            set_source_files_properties(neuroute_record_codec_avx2.cpp
                PROPERTIES COMPILE_OPTIONS "-mavx2")
        endif()
        set(AGENT_MEMORY_NEUROUTE_CODEC_HAS_AVX2 1)
    endif()
endif()
target_compile_definitions(agent_memory_neuroute_record_codec PRIVATE
    AGENT_MEMORY_NEUROUTE_CODEC_HAS_SSE2=${AGENT_MEMORY_NEUROUTE_CODEC_HAS_SSE2}
    AGENT_MEMORY_NEUROUTE_CODEC_HAS_AVX2=${AGENT_MEMORY_NEUROUTE_CODEC_HAS_AVX2})
target_link_libraries(agent-memory-neuroute-r4-layout-benchmark PRIVATE
    agent_memory_neuroute_record_codec)

add_executable(agent-memory-neuroute-record-codec-contract
    neuroute_record_codec_contract.cpp)
target_compile_definitions(agent-memory-neuroute-record-codec-contract PRIVATE
    AGENT_MEMORY_NEUROUTE_CODEC_HAS_SSE2=${AGENT_MEMORY_NEUROUTE_CODEC_HAS_SSE2}
    AGENT_MEMORY_NEUROUTE_CODEC_HAS_AVX2=${AGENT_MEMORY_NEUROUTE_CODEC_HAS_AVX2}
    AGENT_MEMORY_NEUROUTE_HAS_SIMDCOMP=${AGENT_MEMORY_NEUROUTE_HAS_SIMDCOMP})
target_link_libraries(agent-memory-neuroute-record-codec-contract PRIVATE
    agent_memory::agent_memory agent_memory::json
    agent_memory_neuroute_record_codec)
if(TARGET agent_memory_neuroute_simdcomp)
    target_link_libraries(agent-memory-neuroute-record-codec-contract PRIVATE
        agent_memory_neuroute_simdcomp)
endif()
agent_memory_set_evaluator_source_manifest(
    agent-memory-neuroute-record-codec-contract
    "${CMAKE_CURRENT_SOURCE_DIR}/neuroute_record_codec.hpp"
    "${CMAKE_CURRENT_SOURCE_DIR}/neuroute_record_codec.cpp"
    "${CMAKE_CURRENT_SOURCE_DIR}/neuroute_record_codec_sse2.cpp"
    "${CMAKE_CURRENT_SOURCE_DIR}/neuroute_record_codec_avx2.cpp"
    "${CMAKE_CURRENT_SOURCE_DIR}/neuroute_record_codec_contract.cpp")
agent_memory_set_evaluator_build_environment(
    agent-memory-neuroute-record-codec-contract)
agent_memory_set_evaluator_source_manifest(agent-memory-neuroute-final-representation
    "${CMAKE_CURRENT_SOURCE_DIR}/neuroute_final_representation.cpp"
)
agent_memory_set_evaluator_build_environment(agent-memory-neuroute-final-representation)
target_link_libraries(agent-memory-neuroute-final-representation
    PRIVATE
        agent_memory::agent_memory
        agent_memory::json
)

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
