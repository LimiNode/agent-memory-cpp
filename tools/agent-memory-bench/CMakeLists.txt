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
