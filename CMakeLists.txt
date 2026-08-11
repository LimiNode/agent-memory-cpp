cmake_minimum_required(VERSION 3.20)

project(agent-memory-cpp
    VERSION 0.1.0
    DESCRIPTION "Embedded C++17 memory and retrieval toolkit for AI agents"
    LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

set(AGENT_MEMORY_TOP_LEVEL OFF)
if(CMAKE_SOURCE_DIR STREQUAL CMAKE_CURRENT_SOURCE_DIR)
    set(AGENT_MEMORY_TOP_LEVEL ON)
endif()

include(cmake/AgentMemoryOptions.cmake)
include(cmake/AgentMemoryDependencies.cmake)
include(cmake/AgentMemoryJson.cmake)

if(AGENT_MEMORY_BUILD_BENCHMARKS AND NOT AGENT_MEMORY_ENABLE_JSON)
    message(FATAL_ERROR
        "AGENT_MEMORY_BUILD_BENCHMARKS requires AGENT_MEMORY_ENABLE_JSON=ON")
endif()

add_library(agent_memory STATIC
    src/agent_memory/chat/Message.cpp
    src/agent_memory/core/LibraryInfo.cpp
    src/agent_memory/domain/Document.cpp
    src/agent_memory/domain/Identifiers.cpp
    src/agent_memory/domain/KnowledgeUnit.cpp
    src/agent_memory/domain/Metadata.cpp
    src/agent_memory/domain/MetadataFilter.cpp
    src/agent_memory/domain/Resource.cpp
    src/agent_memory/domain/SourceKind.cpp
    src/agent_memory/embedding/embedding_types.cpp
    src/agent_memory/embedding/enums.cpp
    src/agent_memory/embedding/IEmbedder.cpp
    src/agent_memory/eval/BenchmarkReport.cpp
    src/agent_memory/eval/Evaluation.cpp
    src/agent_memory/eval/IRetrieverAdapter.cpp
    src/agent_memory/eval/RetrievalEvalRunner.cpp
    src/agent_memory/eval/StubRetriever.cpp
    src/agent_memory/facts/Fact.cpp
    src/agent_memory/ingestion/ResourceIndexer.cpp
    src/agent_memory/index/AggregateBinarySignature.cpp
    src/agent_memory/index/AsymmetricBinarySignatureScorer.cpp
    src/agent_memory/index/AutoencoderBinaryEncoder.cpp
    src/agent_memory/index/BinarySignature.cpp
    src/agent_memory/index/BinarySignatureEncoderRegistry.cpp
    src/agent_memory/index/BinarySignatureInfo.cpp
    src/agent_memory/index/CoordinateSignBinaryEncoder.cpp
    src/agent_memory/index/ExactVectorIndex.cpp
    src/agent_memory/index/FlatBinarySignatureIndex.cpp
    src/agent_memory/index/IBinarySignatureEncoder.cpp
    src/agent_memory/index/IBinarySignatureIndex.cpp
    src/agent_memory/index/ItqRotationBinaryEncoder.cpp
    src/agent_memory/index/IVectorIndex.cpp
    src/agent_memory/index/LearnedProjectionBinaryEncoder.cpp
    src/agent_memory/index/MultiProbeHammingIndex.cpp
    src/agent_memory/index/PcaProjectionBinaryEncoder.cpp
    src/agent_memory/index/RandomizedHadamardBinaryEncoder.cpp
    src/agent_memory/index/RandomHyperplaneBinaryEncoder.cpp
    src/agent_memory/index/VectorSimilarityComputer.cpp
    src/agent_memory/index/VectorIndex.cpp
    src/agent_memory/lexical/ExactLexicalIndex.cpp
    src/agent_memory/lexical/IChunkEnricher.cpp
    src/agent_memory/lexical/ILexicalIndex.cpp
    src/agent_memory/lexical/IQueryAnalyzer.cpp
    src/agent_memory/lexical/IReranker.cpp
    src/agent_memory/lexical/ITokenDictionary.cpp
    src/agent_memory/lexical/ITokenizer.cpp
    src/agent_memory/lexical/KeywordOverlapIndex.cpp
    src/agent_memory/lexical/Lexical.cpp
    src/agent_memory/lexical/StandardTokenizer.cpp
    src/agent_memory/lexical/TokenDictionary.cpp
    src/agent_memory/lexical/Tokenizer.cpp
    src/agent_memory/memory/MemoryObject.cpp
    src/agent_memory/retrieval/BowEmbedder.cpp
    src/agent_memory/retrieval/BowVectorRetriever.cpp
    src/agent_memory/retrieval/BruteForceTopKIndex.cpp
    src/agent_memory/retrieval/ExactLexicalRetriever.cpp
    src/agent_memory/retrieval/HybridRetrievalEngine.cpp
    src/agent_memory/retrieval/IRetrievalEngine.cpp
    src/agent_memory/retrieval/IRetriever.cpp
    src/agent_memory/retrieval/Retrieval.cpp
    src/agent_memory/storage/IDocumentStorage.cpp
    src/agent_memory/storage/IResourceIndexRecordOwnerStorage.cpp
    src/agent_memory/storage/IResourceManifestStorage.cpp
)
add_library(agent_memory::agent_memory ALIAS agent_memory)

target_compile_features(agent_memory PUBLIC cxx_std_17)

target_include_directories(agent_memory
    PUBLIC
        $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/src>
        $<INSTALL_INTERFACE:include>
)

target_compile_definitions(agent_memory
    PUBLIC
        AGENT_MEMORY_HAS_MDBX=$<BOOL:${AGENT_MEMORY_ENABLE_MDBX}>
        AGENT_MEMORY_ENABLE_JSON=$<BOOL:${AGENT_MEMORY_ENABLE_JSON}>
)

set_target_properties(agent_memory PROPERTIES
    OUTPUT_NAME agent_memory
)

if(AGENT_MEMORY_ENABLE_WARNINGS)
    if(MSVC)
        target_compile_options(agent_memory PRIVATE /W4 /permissive-)
    else()
        target_compile_options(agent_memory PRIVATE -Wall -Wextra -Wpedantic)
    endif()
endif()

if(AGENT_MEMORY_ENABLE_MDBX)
    target_sources(agent_memory
        PRIVATE
            src/agent_memory/infrastructure/mdbx/MdbxDocumentStorage.cpp
            src/agent_memory/infrastructure/mdbx/MdbxResourceIndexRecordOwnerStorage.cpp
            src/agent_memory/infrastructure/mdbx/MdbxResourceManifestStorage.cpp
    )

    agent_memory_provide_mdbx_containers(AGENT_MEMORY_MDBX_CONTAINERS_TARGET)
    target_link_libraries(agent_memory
        PRIVATE
            ${AGENT_MEMORY_MDBX_CONTAINERS_TARGET}
    )
endif()

agent_memory_provide_json(AGENT_MEMORY_JSON_TARGET)
if(AGENT_MEMORY_ENABLE_JSON)
    target_sources(agent_memory
        PRIVATE
            src/agent_memory/eval/AutoencoderBinaryArtifact.cpp
            src/agent_memory/eval/AutoencoderBinaryEvaluation.cpp
            src/agent_memory/eval/DatasetLoader.cpp
            src/agent_memory/eval/PrecomputedEmbeddingDataset.cpp
    )
    target_link_libraries(agent_memory
        PRIVATE
            ${AGENT_MEMORY_JSON_TARGET}
    )
endif()

if(AGENT_MEMORY_BUILD_EXAMPLES)
    add_subdirectory(examples)
endif()

if(AGENT_MEMORY_BUILD_BENCHMARKS)
    add_subdirectory(tools/agent-memory-bench)
endif()

if(AGENT_MEMORY_BUILD_TESTS)
    enable_testing()
    if(AGENT_MEMORY_BUILD_BENCHMARKS)
        add_test(
            NAME agent_memory_mih_native_hot_path_self_test
            COMMAND agent-memory-mih-native-hot-path --self-test
        )
        add_test(
            NAME agent_memory_standard_binary_encoder_evaluator_self_test
            COMMAND agent-memory-standard-binary-eval --self-test
        )
        add_test(
            NAME agent_memory_autoencoder_evaluator_self_test
            COMMAND agent-memory-autoencoder-eval --self-test
        )
    endif()
    find_package(Python3 COMPONENTS Interpreter REQUIRED)
    execute_process(
        COMMAND ${Python3_EXECUTABLE} -c "import numpy; import yaml"
        RESULT_VARIABLE AGENT_MEMORY_PYTHON_TOOLS_IMPORT_RESULT
        OUTPUT_QUIET
        ERROR_VARIABLE AGENT_MEMORY_PYTHON_TOOLS_IMPORT_ERROR
    )
    if(NOT AGENT_MEMORY_PYTHON_TOOLS_IMPORT_RESULT EQUAL 0)
        message(FATAL_ERROR
            "Python evaluator self-tests require NumPy and PyYAML from ${Python3_EXECUTABLE}: "
            "${AGENT_MEMORY_PYTHON_TOOLS_IMPORT_ERROR}"
        )
    endif()
    if(Python3_Interpreter_FOUND)
        add_test(
            NAME agent_memory_miracl_ae_preparer_self_test
            COMMAND
                ${Python3_EXECUTABLE}
                ${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/prepare-miracl-ae-study.py
                --self-test
            WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
        )
        add_test(
            NAME agent_memory_miracl_ae_manifest_validator_self_test
            COMMAND
                ${Python3_EXECUTABLE}
                ${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/validate-miracl-ae-study.py
                --self-test
            WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
        )
        add_test(
            NAME agent_memory_e5_materializer_self_test
            COMMAND
                ${Python3_EXECUTABLE}
                ${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/materialize-prepared-e5.py
                --self-test
            WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
        )
        add_test(
            NAME agent_memory_binary_autoencoder_trainer_self_test
            COMMAND
                ${Python3_EXECUTABLE}
                ${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/train-binary-autoencoder.py
                --self-test
            WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
        )
        add_test(
            NAME agent_memory_learned_binary_adc_trainer_self_test
            COMMAND
                ${Python3_EXECUTABLE}
                ${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/train-learned-binary-adc.py
                --self-test
            WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
        )
        add_test(
            NAME agent_memory_nlb_qrels_supervised_trainer_self_test
            COMMAND
                ${Python3_EXECUTABLE}
                ${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/train-nlb-qrels-supervised.py
                --self-test
            WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
        )
        add_test(
            NAME agent_memory_projection_quantization_evaluator_self_test
            COMMAND
                ${Python3_EXECUTABLE}
                ${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/evaluate-projection-quantization.py
                self-test
            WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
        )
        add_test(
            NAME agent_memory_mih_banding_evaluator_self_test
            COMMAND
                ${Python3_EXECUTABLE}
                ${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/evaluate-mih-banding.py
                self-test
            WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
        )
        add_test(
            NAME agent_memory_mih_calibrated_weighted_hamming_matrix_self_test
            COMMAND
                ${Python3_EXECUTABLE}
                ${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/run-mih-calibrated-weighted-hamming-matrix.py
                self-test
                --matrix
                ${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/mih-calibrated-weighted-hamming.example.json
            WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
        )
        add_test(
            NAME agent_memory_mih_local_approximation_evidence_packager_self_test
            COMMAND
                ${Python3_EXECUTABLE}
                ${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/write-mih-local-approximation-evidence.py
                --self-test
            WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
        )
        add_test(
            NAME agent_memory_mih_adc_guided_probing_matrix_self_test
            COMMAND
                ${Python3_EXECUTABLE}
                ${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/run-mih-adc-guided-probing-matrix.py
                self-test
                --matrix
                ${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/mih-adc-guided-probing.example.json
            WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
        )
        add_test(
            NAME agent_memory_mih_calibration_balanced_bands_matrix_self_test
            COMMAND
                ${Python3_EXECUTABLE}
                ${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/run-mih-calibration-balanced-bands-matrix.py
                self-test
                --matrix
                ${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/mih-calibration-balanced-bands.example.json
            WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
        )
        add_test(
            NAME agent_memory_ann_cascade_comparison_self_test
            COMMAND
                ${Python3_EXECUTABLE}
                ${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/evaluate-ann-cascade.py
                self-test
            WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
        )
        add_test(
            NAME agent_memory_ann_cascade_matrix_self_test
            COMMAND
                ${Python3_EXECUTABLE}
                ${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/run-ann-cascade-matrix.py
                self-test
                --matrix
                ${CMAKE_CURRENT_SOURCE_DIR}/guides/experiments/2026-08-10-ann-cascade-comparison-matrix-v1.json
            WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
        )
        add_test(
            NAME agent_memory_ann_cascade_evidence_packager_self_test
            COMMAND
                ${Python3_EXECUTABLE}
                ${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/write-ann-cascade-evidence.py
                --self-test
            WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
        )
        add_test(
            NAME agent_memory_mih_banding_evidence_validator_self_test
            COMMAND
                ${Python3_EXECUTABLE}
                ${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/validate-mih-banding-evidence.py
                self-test
            WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
        )
        add_test(
            NAME agent_memory_mih_storage_input_materializer_self_test
            COMMAND
                ${Python3_EXECUTABLE}
                ${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/materialize-mih-storage-input.py
                self-test
            WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
        )
        add_test(
            NAME agent_memory_mih_storage_evidence_writer_self_test
            COMMAND
                ${Python3_EXECUTABLE}
                ${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/write-mih-storage-evidence.py
                self-test
            WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
        )
        if(AGENT_MEMORY_ENABLE_MDBX AND AGENT_MEMORY_BUILD_BENCHMARKS)
            add_test(
                NAME agent_memory_mih_storage_benchmark_self_test
                COMMAND
                    $<TARGET_FILE:agent-memory-mih-storage-bench>
                    --self-test
                    ${CMAKE_CURRENT_BINARY_DIR}/mih-storage-benchmark-self-test.mdbx
                WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
            )
        endif()
        add_test(
            NAME agent_memory_pq_opq_evaluator_self_test
            COMMAND
                ${Python3_EXECUTABLE}
                ${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/evaluate-pq-opq.py
                self-test
            WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
        )
        add_test(
            NAME agent_memory_pq_opq_evidence_validator_self_test
            COMMAND
                ${Python3_EXECUTABLE}
                ${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/validate-pq-opq-evidence.py
                self-test
            WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
        )
        add_test(
            NAME agent_memory_nlb_retrieval_finetuner_self_test
            COMMAND
                ${Python3_EXECUTABLE}
                ${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/fine-tune-nlb-retrieval.py
                --self-test
            WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
        )
        add_test(
            NAME agent_memory_nlb_median_threshold_calibrator_self_test
            COMMAND
                ${Python3_EXECUTABLE}
                ${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/calibrate-nlb-median-threshold.py
                --self-test
            WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
        )
        add_test(
            NAME agent_memory_nlb_pilot_gate_validator_self_test
            COMMAND
                ${Python3_EXECUTABLE}
                ${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/validate-nlb-pilot-gates.py
                --self-test
            WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
        )
        execute_process(
            COMMAND ${Python3_EXECUTABLE} -c "import yaml"
            RESULT_VARIABLE AGENT_MEMORY_PYYAML_IMPORT_RESULT
            OUTPUT_QUIET
            ERROR_QUIET
        )
        if(AGENT_MEMORY_PYYAML_IMPORT_RESULT EQUAL 0)
            add_test(
                NAME agent_memory_dbi_manifest_validate
                COMMAND
                    ${Python3_EXECUTABLE}
                    ${CMAKE_CURRENT_SOURCE_DIR}/tools/validate-dbi-manifest.py
                    ${CMAKE_CURRENT_SOURCE_DIR}/guides/dbi-manifest.yaml
                    --tz
                    ${CMAKE_CURRENT_SOURCE_DIR}/guides/mdbx-containers-extension-tz.md
                WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
            )
            add_test(
                NAME agent_memory_dbi_manifest_validator_self_test
                COMMAND
                    ${Python3_EXECUTABLE}
                    ${CMAKE_CURRENT_SOURCE_DIR}/tools/validate-dbi-manifest.py
                    ${CMAKE_CURRENT_SOURCE_DIR}/guides/dbi-manifest.yaml
                    --self-test
                WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
            )
        else()
            message(STATUS "PyYAML not found; skipping DBI manifest validator tests")
        endif()
    else()
        message(STATUS "Python3 interpreter not found; skipping DBI manifest validator tests")
    endif()
    if(AGENT_MEMORY_BUILD_BENCHMARKS)
        add_test(
            NAME agent_memory_benchmark_cli_synthetic_sweep
            COMMAND
                $<TARGET_FILE:agent-memory-bench>
                ${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/synthetic-sweep.example.json
                ${CMAKE_CURRENT_BINARY_DIR}/synthetic-sweep-ctest.json
            WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
        )
        add_test(
            NAME agent_memory_benchmark_cli_synthetic_binary_flat_vs_float
            COMMAND
                $<TARGET_FILE:agent-memory-bench>
                ${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/synthetic-binary-flat-vs-float.example.json
                ${CMAKE_CURRENT_BINARY_DIR}/synthetic-binary-flat-vs-float-ctest.json
            WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
        )
        add_test(
            NAME agent_memory_benchmark_cli_synthetic_binary_rerank_grid
            COMMAND
                ${CMAKE_COMMAND}
                -DAGENT_MEMORY_BENCH_EXE=$<TARGET_FILE:agent-memory-bench>
                -DAGENT_MEMORY_BENCH_CONFIG=${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/synthetic-binary-rerank-grid-smoke.example.json
                -DAGENT_MEMORY_BENCH_OUTPUT=${CMAKE_CURRENT_BINARY_DIR}/synthetic-binary-rerank-grid-ctest.json
                -DAGENT_MEMORY_BENCH_WORKDIR=${CMAKE_CURRENT_SOURCE_DIR}
                -P ${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/validate-binary-rerank-grid-smoke.cmake
            WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
        )
        add_test(
            NAME agent_memory_benchmark_cli_precomputed_embedding_binary_rerank_grid
            COMMAND
                ${CMAKE_COMMAND}
                -DAGENT_MEMORY_BENCH_EXE=$<TARGET_FILE:agent-memory-bench>
                -DAGENT_MEMORY_BENCH_CONFIG=${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/precomputed-embedding-binary-rerank-grid-smoke.example.json
                -DAGENT_MEMORY_BENCH_OUTPUT=${CMAKE_CURRENT_BINARY_DIR}/precomputed-embedding-binary-rerank-grid-ctest.json
                -DAGENT_MEMORY_BENCH_WORKDIR=${CMAKE_CURRENT_SOURCE_DIR}
                -P ${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/validate-precomputed-embedding-binary-rerank-smoke.cmake
            WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
        )
        add_test(
            NAME agent_memory_precomputed_embedding_artifact_verify_tiny
            COMMAND
                $<TARGET_FILE:agent-memory-precomputed-artifact-verify>
                ${CMAKE_CURRENT_SOURCE_DIR}/tests/eval/fixtures/precomputed-embedding-tiny.json
            WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
        )
        add_test(
            NAME agent_memory_precomputed_embedding_artifact_verify_negative
            COMMAND
                ${CMAKE_COMMAND}
                -DAGENT_MEMORY_PRECOMPUTED_ARTIFACT_VERIFY_EXE=$<TARGET_FILE:agent-memory-precomputed-artifact-verify>
                -DAGENT_MEMORY_PRECOMPUTED_EMBEDDING_DATASET=${CMAKE_CURRENT_SOURCE_DIR}/tests/eval/fixtures/precomputed-embedding-tiny.json
                -DAGENT_MEMORY_PRECOMPUTED_EMBEDDING_NEGATIVE_WORKDIR=${CMAKE_CURRENT_BINARY_DIR}/precomputed-embedding-artifact-negative
                -P ${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/validate-precomputed-embedding-artifact-negative.cmake
            WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
        )
        add_test(
            NAME agent_memory_precomputed_embedding_artifact_verify_canonicalization
            COMMAND
                ${CMAKE_COMMAND}
                -DAGENT_MEMORY_PRECOMPUTED_ARTIFACT_VERIFY_EXE=$<TARGET_FILE:agent-memory-precomputed-artifact-verify>
                -DAGENT_MEMORY_PRECOMPUTED_EMBEDDING_DATASET=${CMAKE_CURRENT_SOURCE_DIR}/tests/eval/fixtures/precomputed-embedding-tiny.json
                -DAGENT_MEMORY_PRECOMPUTED_EMBEDDING_CANONICAL_WORKDIR=${CMAKE_CURRENT_BINARY_DIR}/precomputed-embedding-artifact-canonical
                -P ${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/validate-precomputed-embedding-artifact-canonicalization.cmake
            WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
        )
        add_test(
            NAME agent_memory_benchmark_cli_precomputed_embedding_binary_rerank_grid_medium
            COMMAND
                ${CMAKE_COMMAND}
                -DAGENT_MEMORY_BENCH_EXE=$<TARGET_FILE:agent-memory-bench>
                -DAGENT_MEMORY_BENCH_CONFIG=${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/precomputed-embedding-binary-rerank-grid-medium.example.json
                -DAGENT_MEMORY_BENCH_OUTPUT=${CMAKE_CURRENT_BINARY_DIR}/precomputed-embedding-binary-rerank-grid-medium-ctest.json
                -DAGENT_MEMORY_BENCH_WORKDIR=${CMAKE_CURRENT_SOURCE_DIR}
                -P ${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/validate-precomputed-embedding-binary-rerank-medium.cmake
            WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
        )
        add_test(
            NAME agent_memory_precomputed_embedding_artifact_verify_medium
            COMMAND
                $<TARGET_FILE:agent-memory-precomputed-artifact-verify>
                ${CMAKE_CURRENT_SOURCE_DIR}/tests/eval/fixtures/precomputed-embedding-medium.json
            WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
        )
        add_test(
            NAME agent_memory_benchmark_cli_precomputed_embedding_binary_rerank_grid_external_hash
            COMMAND
                ${CMAKE_COMMAND}
                -DAGENT_MEMORY_BENCH_EXE=$<TARGET_FILE:agent-memory-bench>
                -DAGENT_MEMORY_BENCH_CONFIG=${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/precomputed-embedding-binary-rerank-grid-external-hash.example.json
                -DAGENT_MEMORY_BENCH_OUTPUT=${CMAKE_CURRENT_BINARY_DIR}/precomputed-embedding-binary-rerank-grid-external-hash-ctest.json
                -DAGENT_MEMORY_BENCH_WORKDIR=${CMAKE_CURRENT_SOURCE_DIR}
                -P ${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/validate-precomputed-embedding-binary-rerank-external-hash.cmake
            WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
        )
        add_test(
            NAME agent_memory_precomputed_embedding_artifact_verify_external_hash
            COMMAND
                $<TARGET_FILE:agent-memory-precomputed-artifact-verify>
                ${CMAKE_CURRENT_SOURCE_DIR}/tests/eval/fixtures/precomputed-embedding-external-hash.json
            WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
        )
        add_test(
            NAME agent_memory_benchmark_cli_precomputed_embedding_binary_rerank_grid_minilm
            COMMAND
                ${CMAKE_COMMAND}
                -DAGENT_MEMORY_BENCH_EXE=$<TARGET_FILE:agent-memory-bench>
                -DAGENT_MEMORY_BENCH_CONFIG=${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/precomputed-embedding-binary-rerank-grid-minilm.example.json
                -DAGENT_MEMORY_BENCH_OUTPUT=${CMAKE_CURRENT_BINARY_DIR}/precomputed-embedding-binary-rerank-grid-minilm-l6-v2-ctest.json
                -DAGENT_MEMORY_BENCH_WORKDIR=${CMAKE_CURRENT_SOURCE_DIR}
                -P ${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/validate-precomputed-embedding-binary-rerank-minilm.cmake
            WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
        )
        add_test(
            NAME agent_memory_precomputed_embedding_artifact_verify_minilm
            COMMAND
                $<TARGET_FILE:agent-memory-precomputed-artifact-verify>
                ${CMAKE_CURRENT_SOURCE_DIR}/tests/eval/fixtures/precomputed-embedding-minilm-l6-v2.json
            WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
        )
        add_test(
            NAME agent_memory_benchmark_cli_precomputed_embedding_binary_rerank_grid_minilm_large
            COMMAND
                ${CMAKE_COMMAND}
                -DAGENT_MEMORY_BENCH_EXE=$<TARGET_FILE:agent-memory-bench>
                -DAGENT_MEMORY_BENCH_CONFIG=${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/precomputed-embedding-binary-rerank-grid-minilm-large.example.json
                -DAGENT_MEMORY_BENCH_OUTPUT=${CMAKE_CURRENT_BINARY_DIR}/precomputed-embedding-binary-rerank-grid-minilm-l6-v2-large-ctest.json
                -DAGENT_MEMORY_BENCH_WORKDIR=${CMAKE_CURRENT_SOURCE_DIR}
                -P ${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/validate-precomputed-embedding-binary-rerank-minilm-large.cmake
            WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
        )
        add_test(
            NAME agent_memory_precomputed_embedding_artifact_verify_minilm_large
            COMMAND
                $<TARGET_FILE:agent-memory-precomputed-artifact-verify>
                ${CMAKE_CURRENT_SOURCE_DIR}/tests/eval/fixtures/precomputed-embedding-minilm-l6-v2-large.json
            WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
        )
        add_test(
            NAME agent_memory_benchmark_cli_precomputed_embedding_binary_rerank_grid_multilingual_e5_small
            COMMAND
                ${CMAKE_COMMAND}
                -DAGENT_MEMORY_BENCH_EXE=$<TARGET_FILE:agent-memory-bench>
                -DAGENT_MEMORY_BENCH_CONFIG=${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/precomputed-embedding-binary-rerank-grid-multilingual-e5-small.example.json
                -DAGENT_MEMORY_BENCH_OUTPUT=${CMAKE_CURRENT_BINARY_DIR}/precomputed-embedding-binary-rerank-grid-multilingual-e5-small-ctest.json
                -DAGENT_MEMORY_BENCH_WORKDIR=${CMAKE_CURRENT_SOURCE_DIR}
                -P ${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/validate-precomputed-embedding-binary-rerank-multilingual-e5-small.cmake
            WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
        )
        add_test(
            NAME agent_memory_precomputed_embedding_artifact_verify_multilingual_e5_small
            COMMAND
                $<TARGET_FILE:agent-memory-precomputed-artifact-verify>
                ${CMAKE_CURRENT_SOURCE_DIR}/tests/eval/fixtures/precomputed-embedding-multilingual-e5-small.json
            WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
        )
        if(Python3_Interpreter_FOUND)
            add_test(
                NAME agent_memory_precomputed_embedding_external_hash_regeneration
                COMMAND
                    ${CMAKE_COMMAND}
                    -DAGENT_MEMORY_PYTHON_EXECUTABLE=${Python3_EXECUTABLE}
                    -DAGENT_MEMORY_PRECOMPUTED_EXTERNAL_HASH_GENERATOR=${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/generate-precomputed-external-hash-fixture.py
                    -DAGENT_MEMORY_PRECOMPUTED_EXTERNAL_HASH_FIXTURE=${CMAKE_CURRENT_SOURCE_DIR}/tests/eval/fixtures/precomputed-embedding-external-hash.json
                    -DAGENT_MEMORY_PRECOMPUTED_EXTERNAL_HASH_REGEN_WORKDIR=${CMAKE_CURRENT_BINARY_DIR}/precomputed-embedding-external-hash-regeneration
                    -P ${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/validate-precomputed-embedding-external-hash-regeneration.cmake
                WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
            )
            add_test(
                NAME agent_memory_precomputed_embedding_content_contract_minilm
                COMMAND
                    ${Python3_EXECUTABLE}
                    ${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/verify-precomputed-fixture-content-contract.py
                    --fixture
                    ${CMAKE_CURRENT_SOURCE_DIR}/tests/eval/fixtures/precomputed-embedding-minilm-l6-v2.json
                    --contract
                    ${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/precomputed_fixture_contract.py
                WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
            )
            add_test(
                NAME agent_memory_precomputed_embedding_content_contract_minilm_large
                COMMAND
                    ${Python3_EXECUTABLE}
                    ${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/verify-precomputed-fixture-content-contract.py
                    --fixture
                    ${CMAKE_CURRENT_SOURCE_DIR}/tests/eval/fixtures/precomputed-embedding-minilm-l6-v2-large.json
                    --contract
                    ${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/precomputed_fixture_large_contract.py
                WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
            )
            add_test(
                NAME agent_memory_precomputed_embedding_content_contract_multilingual_e5_small
                COMMAND
                    ${Python3_EXECUTABLE}
                    ${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/verify-precomputed-fixture-content-contract.py
                    --fixture
                    ${CMAKE_CURRENT_SOURCE_DIR}/tests/eval/fixtures/precomputed-embedding-multilingual-e5-small.json
                    --contract
                    ${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/precomputed_fixture_multilingual_e5_contract.py
                WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
            )
            add_test(
                NAME agent_memory_precomputed_embedding_content_contract_negative
                COMMAND
                    ${CMAKE_COMMAND}
                    -DAGENT_MEMORY_PYTHON_EXECUTABLE=${Python3_EXECUTABLE}
                    -DAGENT_MEMORY_PRECOMPUTED_CONTENT_CONTRACT_VERIFY=${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/verify-precomputed-fixture-content-contract.py
                    -DAGENT_MEMORY_PRECOMPUTED_CONTENT_CONTRACT_FIXTURE=${CMAKE_CURRENT_SOURCE_DIR}/tests/eval/fixtures/precomputed-embedding-minilm-l6-v2.json
                    -DAGENT_MEMORY_PRECOMPUTED_CONTENT_CONTRACT_MODULE=${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/precomputed_fixture_contract.py
                    -DAGENT_MEMORY_PRECOMPUTED_CONTENT_CONTRACT_NEGATIVE_WORKDIR=${CMAKE_CURRENT_BINARY_DIR}/precomputed-embedding-content-contract-negative
                    -P ${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/validate-precomputed-fixture-content-contract-negative.cmake
                WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
            )
        else()
            message(WARNING
                "Python3 interpreter not found; skipping Python-based precomputed fixture tests")
        endif()
        add_test(
            NAME agent_memory_benchmark_cli_hamming_hot_path
            COMMAND
                $<TARGET_FILE:agent-memory-hamming-hot-path-bench>
                ${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/hamming-hot-path-smoke.example.json
                ${CMAKE_CURRENT_BINARY_DIR}/hamming-hot-path-ctest.json
            WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
        )
        add_test(
            NAME agent_memory_benchmark_cli_aggregate_signature_smoke
            COMMAND
                ${CMAKE_COMMAND}
                -DAGENT_MEMORY_AGGREGATE_BENCH_EXE=$<TARGET_FILE:agent-memory-aggregate-signature-bench>
                -DAGENT_MEMORY_AGGREGATE_BENCH_CONFIG=${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/aggregate-signature-smoke.example.json
                -DAGENT_MEMORY_AGGREGATE_BENCH_OUTPUT=${CMAKE_CURRENT_BINARY_DIR}/aggregate-signature-smoke-ctest.json
                -DAGENT_MEMORY_AGGREGATE_BENCH_WORKDIR=${CMAKE_CURRENT_SOURCE_DIR}
                -P ${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/validate-aggregate-signature-smoke.cmake
            WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
        )
        add_test(
            NAME agent_memory_benchmark_cli_binary_lifecycle_smoke
            COMMAND
                ${CMAKE_COMMAND}
                -DAGENT_MEMORY_BINARY_LIFECYCLE_BENCH_EXE=$<TARGET_FILE:agent-memory-binary-lifecycle-bench>
                -DAGENT_MEMORY_BINARY_LIFECYCLE_BENCH_CONFIG=${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/binary-lifecycle-smoke.example.json
                -DAGENT_MEMORY_BINARY_LIFECYCLE_BENCH_OUTPUT=${CMAKE_CURRENT_BINARY_DIR}/binary-lifecycle-smoke-ctest.json
                -DAGENT_MEMORY_BINARY_LIFECYCLE_BENCH_WORKDIR=${CMAKE_CURRENT_SOURCE_DIR}
                -P ${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/validate-binary-lifecycle-smoke.cmake
            WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
        )
        add_test(
            NAME agent_memory_benchmark_cli_binary_lifecycle_config_compat
            COMMAND
                ${CMAKE_COMMAND}
                -DAGENT_MEMORY_BINARY_LIFECYCLE_BENCH_EXE=$<TARGET_FILE:agent-memory-binary-lifecycle-bench>
                -DAGENT_MEMORY_BINARY_LIFECYCLE_COMPAT_WORKDIR=${CMAKE_CURRENT_BINARY_DIR}/binary-lifecycle-config-compat
                -P ${CMAKE_CURRENT_SOURCE_DIR}/tools/agent-memory-bench/validate-binary-lifecycle-config-compat.cmake
            WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}
        )
    endif()
    add_subdirectory(tests)
endif()
