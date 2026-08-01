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

agent_memory_set_evaluator_source_manifest(agent-memory-autoencoder-eval
    "${CMAKE_CURRENT_SOURCE_DIR}/autoencoder_evaluation.cpp"
    "${PROJECT_SOURCE_DIR}/src/agent_memory/eval/AutoencoderBinaryArtifact.cpp"
    "${PROJECT_SOURCE_DIR}/src/agent_memory/eval/AutoencoderBinaryEvaluation.cpp"
    "${PROJECT_SOURCE_DIR}/src/agent_memory/eval/Evaluation.cpp"
    "${PROJECT_SOURCE_DIR}/src/agent_memory/index/BinarySignature.cpp"
    "${PROJECT_SOURCE_DIR}/src/agent_memory/index/VectorSimilarityComputer.cpp"
    "${PROJECT_SOURCE_DIR}/src/agent_memory/index/AutoencoderBinaryEncoder.cpp"
)

target_link_libraries(agent-memory-autoencoder-eval
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
