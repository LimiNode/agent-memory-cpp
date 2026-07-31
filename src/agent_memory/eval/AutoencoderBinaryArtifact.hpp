#pragma once
#ifndef AGENT_MEMORY_HEADER_EVAL_AUTOENCODER_BINARY_ARTIFACT_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_EVAL_AUTOENCODER_BINARY_ARTIFACT_HPP_INCLUDED

/// \file AutoencoderBinaryArtifact.hpp
/// \brief Verified loader for offline linear binary autoencoder artifacts.

#include <agent_memory/index/AutoencoderBinaryEncoder.hpp>
#include <agent_memory/eval/Evaluation.hpp>

#include <filesystem>
#include <string>
#include <vector>

#if !defined(AGENT_MEMORY_ENABLE_JSON) || !AGENT_MEMORY_ENABLE_JSON
#error "AutoencoderBinaryArtifact is unavailable: rebuild with -DAGENT_MEMORY_ENABLE_JSON=ON (nlohmann_json is required)."
#endif

namespace agent_memory {

    /// \brief ID-bearing float vector loaded from a materialized E5 study.
    struct MaterializedEmbeddingRecord final {
        std::string id;
        Embedding embedding;
    };

    /// \brief Held-out E5 vectors and qrels from a verified materialization root.
    struct MaterializedAutoencoderEvaluationDataset final {
        std::string materialization_manifest_sha256;
        std::string prepared_study_manifest_sha256;
        /// \brief Document-only rows available for offline encoder training.
        ///
        /// These IDs are verified against the materialization manifest. Callers
        /// must not mix them with held-out evaluation documents, queries, or
        /// qrels when reporting an encoder-comparison result.
        std::vector<MaterializedEmbeddingRecord> training_embeddings;
        std::vector<MaterializedEmbeddingRecord> document_embeddings;
        std::vector<MaterializedEmbeddingRecord> query_embeddings;
        std::vector<RelevanceJudgment> judgments;
    };

    /// \brief A verified trained artifact ready for C++ binary inference.
    struct AutoencoderBinaryArtifact final {
        std::string artifact_sha256;
        std::string trainer_id;
        std::string trainer_version;
        std::string input_materialization_manifest_sha256;
        std::string prepared_study_manifest_sha256;
        AutoencoderBinaryEncoder encoder;
        AutoencoderBinaryDecoder decoder;
    };

    /// \brief Loads and verifies a supported v1 binary-autoencoder artifact.
    ///
    /// The loader validates the JSON schema, all declared SHA-256 weight-file
    /// digests, exact row-major float32-le byte sizes, finite weights, and the
    /// encoder/decoder shapes. `linear_binary_autoencoder_ste` retains its
    /// independent linear decoder; `nlb_paper_tied_v1` and
    /// `nlb_median_threshold_v1` derive a zero/one, `tanh` decoder from the
    /// verified transpose of their encoder matrix. The median-threshold variant
    /// additionally requires its declared document-only calibration provenance
    /// and uses its persisted per-bit encoder bias. The loader returns the
    /// artifact JSON digest as stable encoder identity input;
    /// changing any artifact metadata or weights creates a distinct binary
    /// signature space.
    /// \throws std::runtime_error on missing, malformed, or integrity-invalid input.
    [[nodiscard]] AutoencoderBinaryArtifact load_autoencoder_binary_artifact(
        const std::filesystem::path& artifact_path
    );

    /// \brief Loads held-out vectors and qrels emitted by the E5 materializer.
    ///
    /// All selected output hashes, float32-le descriptors, counts, dimensions,
    /// IDs, and qrels closure are verified before values become embeddings.
    [[nodiscard]] MaterializedAutoencoderEvaluationDataset
    load_materialized_autoencoder_evaluation_dataset(
        const std::filesystem::path& materialization_root
    );

} // namespace agent_memory

#endif
