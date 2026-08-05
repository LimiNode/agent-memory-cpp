#include <agent_memory/eval/AutoencoderBinaryArtifact.hpp>
#include <agent_memory/eval/AutoencoderBinaryEvaluation.hpp>

#include <cmath>
#include <cstdint>
#include <cstring>
#include <exception>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <iterator>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

    [[nodiscard]] int fail(const char* message) {
        std::cerr << "autoencoder binary artifact test failed: " << message << '\n';
        return 1;
    }

    template <typename Function>
    [[nodiscard]] bool throws_runtime_error(Function&& function) {
        try {
            function();
        } catch(const std::runtime_error&) {
            return true;
        }
        return false;
    }

    void write_floats(const std::filesystem::path& path, const std::vector<float>& values) {
        std::ofstream output(path, std::ios::binary);
        if(!output) {
            throw std::runtime_error("cannot create test weight file");
        }
        for(const auto value : values) {
            std::uint32_t bits = 0;
            std::memcpy(&bits, &value, sizeof(bits));
            const std::uint8_t bytes[] = {
                static_cast<std::uint8_t>(bits & 0xFFU),
                static_cast<std::uint8_t>((bits >> 8U) & 0xFFU),
                static_cast<std::uint8_t>((bits >> 16U) & 0xFFU),
                static_cast<std::uint8_t>((bits >> 24U) & 0xFFU),
            };
            output.write(reinterpret_cast<const char*>(bytes), sizeof(bytes));
        }
    }

    void write_artifact(const std::filesystem::path& path) {
        std::ofstream output(path, std::ios::binary);
        output << R"({
  "schema_version": 1,
  "trainer": {"id": "agent-memory-cpp:linear-binary-autoencoder-trainer", "version": "v1", "source_hash": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"},
  "input_materialization_manifest_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "prepared_study_manifest_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "source_encoder_artifact_sha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
  "architecture": {
    "family": "linear_binary_autoencoder_ste",
    "input_dimension": 2,
    "bit_count": 2,
    "encoder_activation": "tanh_sign_ste_v1",
    "decoder": "linear"
  },
  "training": {
    "seed": 42,
    "shuffle_recipe": {"id": "python_fisher_yates_sha256_seed_v1", "per_epoch": true},
    "stable_id_lists": {
      "train_sha256": "1111111111111111111111111111111111111111111111111111111111111111",
      "validation_sha256": "2222222222222222222222222222222222222222222222222222222222222222",
      "selection": "stable_sha256_id_split_v1"
    }
  },
  "weights": {
    "encoder_weights": {"path": "encoder-weights.f32", "sha256": "a666c95f0822c64e01580063e9bb27c629d4d0534e3163a9611738599f97df2a", "shape": [2, 2], "layout": "row_major_out_by_in", "dtype": "float32_le"},
    "encoder_bias": {"path": "encoder-bias.f32", "sha256": "1dc7fbfac33e9a09c59d17f9ff8c27e3de8d248f2b7488fbee7768e307abdd33", "shape": [2], "dtype": "float32_le"},
    "decoder_weights": {"path": "decoder-weights.f32", "sha256": "00fde0d04d1701de053663248300b0cb3e09542cc3e9413005a992ea57665a06", "shape": [2, 2], "layout": "row_major_out_by_in", "dtype": "float32_le"},
    "decoder_bias": {"path": "decoder-bias.f32", "sha256": "af5570f5a1810b7af78caf4bc70a660f0df51e42baf91d4de5b2328de0e83dfc", "shape": [2], "dtype": "float32_le"}
  }
})";
    }

    void write_nlb_paper_artifact(const std::filesystem::path& path) {
        std::ofstream output(path, std::ios::binary);
        output << R"({
  "schema_version": 1,
  "trainer": {"id": "agent-memory-cpp:nlb-tied-binary-autoencoder-trainer", "version": "v1", "source_hash": "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"},
  "input_materialization_manifest_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "prepared_study_manifest_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "architecture": {
    "family": "nlb_paper_tied_v1",
    "input_dimension": 2,
    "bit_count": 2,
    "encoder_activation": "hard_step_no_ste_v1",
    "decoder": "tied_transpose_tanh",
    "code_value_encoding": "zero_one",
    "input_transform": "clip_minus_one_one_v1",
    "regularizer": {"id": "paper_w_transpose_w_identity_v1", "weight": 1.0}
  },
  "training": {"seed": 42, "shuffle_recipe": {"id": "python_fisher_yates_sha256_seed_v1", "per_epoch": true}},
  "weights": {
    "encoder_weights": {"path": "encoder-weights.f32", "sha256": "a666c95f0822c64e01580063e9bb27c629d4d0534e3163a9611738599f97df2a", "shape": [2, 2], "layout": "row_major_out_by_in", "dtype": "float32_le"},
    "decoder_bias": {"path": "decoder-bias.f32", "sha256": "af5570f5a1810b7af78caf4bc70a660f0df51e42baf91d4de5b2328de0e83dfc", "shape": [2], "dtype": "float32_le"}
  }
})";
    }

    void write_nlb_median_threshold_artifact(const std::filesystem::path& path) {
        std::ofstream output(path, std::ios::binary);
        output << R"({
  "schema_version": 1,
  "trainer": {"id": "agent-memory-cpp:nlb-median-threshold-calibrator", "version": "v1", "source_hash": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"},
  "input_materialization_manifest_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "prepared_study_manifest_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "source_encoder_artifact_sha256": "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
  "architecture": {
    "family": "nlb_median_threshold_v1",
    "input_dimension": 2,
    "bit_count": 2,
    "encoder_activation": "affine_hard_step_median_threshold_v1",
    "decoder": "tied_transpose_tanh",
    "code_value_encoding": "zero_one",
    "input_transform": "clip_minus_one_one_v1"
  },
  "training": {"seed": 42, "shuffle_recipe": {"id": "python_fisher_yates_sha256_seed_v1", "per_epoch": true}},
  "calibration": {"policy": "per_bit_projection_median_v1", "split_id": "stable_document_only_train_v1", "document_count": 2, "document_ids_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
  "weights": {
    "encoder_weights": {"path": "encoder-weights.f32", "sha256": "a666c95f0822c64e01580063e9bb27c629d4d0534e3163a9611738599f97df2a", "shape": [2, 2], "layout": "row_major_out_by_in", "dtype": "float32_le"},
    "encoder_bias": {"path": "encoder-bias.f32", "sha256": "1dc7fbfac33e9a09c59d17f9ff8c27e3de8d248f2b7488fbee7768e307abdd33", "shape": [2], "dtype": "float32_le"},
    "decoder_bias": {"path": "decoder-bias.f32", "sha256": "af5570f5a1810b7af78caf4bc70a660f0df51e42baf91d4de5b2328de0e83dfc", "shape": [2], "dtype": "float32_le"}
  }
})";
    }

    void write_nlb_quantile_threshold_artifact(const std::filesystem::path& path) {
        std::ofstream output(path, std::ios::binary);
        output << R"({
  "schema_version": 1,
  "trainer": {"id": "agent-memory-cpp:nlb-median-threshold-calibrator", "version": "v1", "source_hash": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"},
  "input_materialization_manifest_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "prepared_study_manifest_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "source_encoder_artifact_sha256": "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
  "architecture": {
    "family": "nlb_quantile_threshold_v1",
    "input_dimension": 2,
    "bit_count": 2,
    "encoder_activation": "affine_hard_step_quantile_threshold_v1",
    "decoder": "tied_transpose_tanh",
    "code_value_encoding": "zero_one",
    "input_transform": "clip_minus_one_one_v1"
  },
  "training": {"seed": 42, "shuffle_recipe": {"id": "python_fisher_yates_sha256_seed_v1", "per_epoch": true}},
  "calibration": {"policy": "per_bit_projection_quantile_v1", "quantile": 0.75, "split_id": "stable_document_only_train_v1", "document_count": 2, "document_ids_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
  "weights": {
    "encoder_weights": {"path": "encoder-weights.f32", "sha256": "a666c95f0822c64e01580063e9bb27c629d4d0534e3163a9611738599f97df2a", "shape": [2, 2], "layout": "row_major_out_by_in", "dtype": "float32_le"},
    "encoder_bias": {"path": "encoder-bias.f32", "sha256": "1dc7fbfac33e9a09c59d17f9ff8c27e3de8d248f2b7488fbee7768e307abdd33", "shape": [2], "dtype": "float32_le"},
    "decoder_bias": {"path": "decoder-bias.f32", "sha256": "af5570f5a1810b7af78caf4bc70a660f0df51e42baf91d4de5b2328de0e83dfc", "shape": [2], "dtype": "float32_le"}
  }
})";
    }

    void write_nlb_retrieval_distilled_artifact(const std::filesystem::path& path) {
        std::ofstream output(path, std::ios::binary);
        output << R"({
  "schema_version": 1,
  "trainer": {"id": "agent-memory-cpp:nlb-retrieval-finetuner", "version": "v1", "source_hash": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee", "base_trainer_source_hash": "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff", "requirements_lock": "requirements-binary-autoencoder-trainer.txt;sha256=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
  "input_materialization_manifest_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "prepared_study_manifest_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "source_encoder_artifact_sha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
  "architecture": {
    "family": "nlb_retrieval_distilled_v1",
    "input_dimension": 2,
    "bit_count": 2,
    "encoder_activation": "affine_hard_step_learned_bias_v1",
    "decoder": "tied_transpose_tanh",
    "code_value_encoding": "zero_one",
    "input_transform": "clip_minus_one_one_v1"
  },
  "training": {
    "seed": 42,
    "epochs": 1,
    "batch_size": 1,
    "learning_rate": 0.001,
    "train_vector_count": 1,
    "validation_vector_count": 1,
    "best_document_only_validation_loss": 0.0,
    "best_epoch": 0,
    "best_training_temperature": 8.0,
    "objective": "document_geometry_distillation_v1",
    "torch_threads": 1,
    "initialization": {"mode": "median_artifact", "source_artifact_sha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc", "source_family": "nlb_median_threshold_v1", "itq_iterations": 0},
    "optimizer": {"id": "adamw", "weight_decay": 0.0},
    "selection": {"id": "fixed_soft_code_validation_loss_v1", "temperature": 8.0},
    "optimization": {"initialization_only": false, "optimizer_step_count": 1},
    "loss_weights": {"reconstruction": 1.0, "decorrelation": 0.0, "document_geometry_distillation": 0.0, "row_orthogonality": 0.0},
    "soft_to_hard": {"id": "geometric_tanh_temperature_schedule_v1", "start": 1.0, "end": 8.0},
    "distillation": {"id": "document_only_in_batch_listwise_kl_v1", "teacher": "normalized_clipped_e5_cosine", "student": "soft_binary_cosine_v1", "teacher_temperature": 0.05, "student_temperature": 0.05, "queries_or_qrels_used": false},
    "shuffle_recipe": {"id": "python_fisher_yates_sha256_seed_v1", "per_epoch": true},
    "stable_id_lists": {
      "train_sha256": "1111111111111111111111111111111111111111111111111111111111111111",
      "validation_sha256": "2222222222222222222222222222222222222222222222222222222222222222",
      "selection": "stable_sha256_id_split_v1"
    }
  },
  "weights": {
    "encoder_weights": {"path": "encoder-weights.f32", "sha256": "a666c95f0822c64e01580063e9bb27c629d4d0534e3163a9611738599f97df2a", "shape": [2, 2], "layout": "row_major_out_by_in", "dtype": "float32_le"},
    "encoder_bias": {"path": "encoder-bias.f32", "sha256": "1dc7fbfac33e9a09c59d17f9ff8c27e3de8d248f2b7488fbee7768e307abdd33", "shape": [2], "dtype": "float32_le"},
    "decoder_bias": {"path": "decoder-bias.f32", "sha256": "af5570f5a1810b7af78caf4bc70a660f0df51e42baf91d4de5b2328de0e83dfc", "shape": [2], "dtype": "float32_le"}
  }
})";
    }

    void write_nlb_qrels_supervised_artifact(const std::filesystem::path& path) {
        std::ofstream output(path, std::ios::binary);
        output << R"({
  "schema_version": 1,
  "trainer": {"id": "agent-memory-cpp:nlb-qrels-supervised-trainer", "version": "v1", "source_hash": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee", "base_trainer_source_hash": "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff", "requirements_lock": "requirements-binary-autoencoder-trainer.txt;sha256=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
  "input_materialization_manifest_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "prepared_study_manifest_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "source_encoder_artifact_sha256": "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
  "architecture": {"family": "nlb_qrels_supervised_v1", "input_dimension": 2, "bit_count": 2, "encoder_activation": "affine_hard_step_document_median_v1", "decoder": "tied_transpose_tanh", "code_value_encoding": "zero_one", "input_transform": "clip_minus_one_one_v1"},
  "training": {
    "seed": 42, "epochs": 1, "batch_size": 1, "learning_rate": 0.001, "objective": "qrels_soft_hamming_triplet_v1", "queries_or_qrels_used": true, "optimization_qrels_used": true, "candidate_limit": 512, "margin": 0.1, "torch_threads": 1,
    "optimizer": {"id": "adamw", "weight_decay": 0.0}, "shuffle_recipe": {"id": "python_fisher_yates_sha256_seed_v1", "per_epoch": true},
    "initialization": {"mode": "pca_median_document_only_v1", "source_materialization_manifest_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "source_family": "label_free_document_only_e5_v1", "itq_iterations": 0},
    "calibration": {"policy": "per_bit_projection_median_v1", "source": "label_free_document_only_train_v1", "document_count": 2, "document_ids_sha256": "1111111111111111111111111111111111111111111111111111111111111111"},
    "teacher": {"id": "intfloat/multilingual-e5-small", "revision": "pinned-revision", "query_prefix": "query: ", "document_prefix": "passage: ", "normalized": true}, "supervision": {"qrels_sha256": "7777777777777777777777777777777777777777777777777777777777777777", "positive_qrels": "grade_gt_zero_v1"},
    "held_out_exclusion": {"id": "external_excluded_document_ids_set_v1", "document_ids_set_sha256": "8888888888888888888888888888888888888888888888888888888888888888"},
    "query_split": {"id": "stable_sha256_query_split_v1", "validation_fraction": 0.2, "train_query_ids_sha256": "2222222222222222222222222222222222222222222222222222222222222222", "validation_query_ids_sha256": "3333333333333333333333333333333333333333333333333333333333333333", "train_query_count": 1, "validation_query_count": 1},
    "hard_negative_mining": {"id": "frozen_e5_cosine_topk_nonpositive_v1", "teacher": "normalized_e5_cosine", "negative_count_per_query": 1, "mined_negative_count_per_query": 1, "consumed_negatives_per_query_per_epoch": 1, "consumed_negative_count_per_query": 1, "sampling_policy": "epoch_indexed_without_replacement_multi_negative_v1", "positive_exclusion": "all_grade_gt_zero_v1", "path": "frozen-hard-negatives.json", "sha256": "4444444444444444444444444444444444444444444444444444444444444444", "canonical_sha256": "5555555555555555555555555555555555555555555555555555555555555555", "train_query_ids_sha256": "2222222222222222222222222222222222222222222222222222222222222222", "validation_query_ids_sha256": "3333333333333333333333333333333333333333333333333333333333333333"},
    "selection": {"id": "qrels_lexicographic_hard_code_v1", "candidate_limit": 512, "lexicographic_order": ["hard_code_health", "positive_qrels_query_coverage_at_512", "reranked_ndcg_at_10", "lower_occupancy_deviation", "earlier_epoch"], "selected_epoch": 0, "metrics": {"positive_qrels_query_coverage_at_512": 0.5, "reranked_ndcg_at_10": 0.5}, "hard_code_health": {"vector_count": 2, "unique_code_count": 2}, "occupancy_deviation": 0.0},
    "run_provenance": {"planned_epoch_count": 1, "completed_epoch_count": 1, "selected_epoch": 0, "selected_optimizer_step_count": 1, "selected_consumed_negative_count_per_query": 1},
    "loss_weights": {"triplet": 1.0, "reconstruction": 0.01, "decorrelation": 0.01, "row_orthogonality": 0.001}, "source_materialization_outputs_sha256": "6666666666666666666666666666666666666666666666666666666666666666"
  },
  "weights": {
    "encoder_weights": {"path": "encoder-weights.f32", "sha256": "a666c95f0822c64e01580063e9bb27c629d4d0534e3163a9611738599f97df2a", "shape": [2, 2], "layout": "row_major_out_by_in", "dtype": "float32_le"},
    "encoder_bias": {"path": "encoder-bias.f32", "sha256": "1dc7fbfac33e9a09c59d17f9ff8c27e3de8d248f2b7488fbee7768e307abdd33", "shape": [2], "dtype": "float32_le"},
    "decoder_bias": {"path": "decoder-bias.f32", "sha256": "af5570f5a1810b7af78caf4bc70a660f0df51e42baf91d4de5b2328de0e83dfc", "shape": [2], "dtype": "float32_le"}
  }
})";
    }

    void write_text(const std::filesystem::path& path, const char* text) {
        std::ofstream output(path, std::ios::binary);
        output << text;
    }

    void replace_once(
        const std::filesystem::path& path,
        const std::string& expected,
        const std::string& replacement
    ) {
        std::ifstream input(path, std::ios::binary);
        const std::string content(
            (std::istreambuf_iterator<char>(input)),
            std::istreambuf_iterator<char>()
        );
        const auto position = content.find(expected);
        if(position == std::string::npos || content.find(expected, position + expected.size()) !=
            std::string::npos) {
            throw std::runtime_error("test artifact replacement is ambiguous");
        }
        auto modified = content;
        modified.replace(position, expected.size(), replacement);
        write_text(path, modified.c_str());
    }

    void write_materialization_manifest(const std::filesystem::path& path) {
        write_text(path, R"({
  "schema_version": 1,
  "materializer": {"id": "agent-memory-cpp:multilingual-e5-materializer", "version": "v1", "source_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
  "prepared_study_manifest_sha256": "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a",
  "vector_format": {"dtype": "float32_le", "endianness": "little", "dimension": 2},
  "outputs": {
    "train_ids": {"path": "train-ids.jsonl", "sha256": "140ba51a53b80e6ecff1266a9c07bb25f430fa5714ae4d57b4a6be63caf5c61e", "count": 2},
    "train_vectors": {"path": "train-vectors.f32", "sha256": "a666c95f0822c64e01580063e9bb27c629d4d0534e3163a9611738599f97df2a", "count": 2, "dimension": 2, "dtype": "float32_le"},
    "evaluation_document_ids": {"path": "evaluation-document-ids.jsonl", "sha256": "5cf5c698207e8b94589039eb93110df0e6a02fcbdba751c2318d66ded450103c", "count": 2},
    "evaluation_document_vectors": {"path": "evaluation-document-vectors.f32", "sha256": "a666c95f0822c64e01580063e9bb27c629d4d0534e3163a9611738599f97df2a", "count": 2, "dimension": 2, "dtype": "float32_le"},
    "evaluation_query_ids": {"path": "evaluation-query-ids.jsonl", "sha256": "8549672b4c462771e4d8447ada71e66a04caf49137b453f84ce513b2b2b9c522", "count": 1},
    "evaluation_query_vectors": {"path": "evaluation-query-vectors.f32", "sha256": "434b26042aff3fb844a4c4c6be0d81a079b0ce84cfb8190679024404e5dc4822", "count": 1, "dimension": 2, "dtype": "float32_le"},
    "evaluation_qrels": {"path": "evaluation-qrels.tsv", "sha256": "bb75672eaca6b3cc5b5ffe05bde25ee9aa1a6fdac0bd3dd5244bc67bf850a870", "count": 1},
    "prepared_study_manifest": {"path": "prepared-study-manifest.json", "sha256": "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a", "count": 1}
  }
})");
    }

} // namespace

int main() {
    const auto root = std::filesystem::temp_directory_path() /
        "agent-memory-autoencoder-binary-artifact-test";
    std::filesystem::remove_all(root);
    std::filesystem::create_directories(root);
    try {
        write_floats(root / "encoder-weights.f32", {1.0F, 0.0F, 0.0F, 1.0F});
        write_floats(root / "encoder-bias.f32", {-0.5F, 0.25F});
        write_floats(root / "decoder-weights.f32", {2.0F, 0.0F, 0.0F, 2.0F});
        write_floats(root / "decoder-bias.f32", {0.0F, 0.0F});
        write_artifact(root / "artifact.json");

        const auto artifact = agent_memory::load_autoencoder_binary_artifact(
            root / "artifact.json"
        );
        if(artifact.artifact_family != "linear_binary_autoencoder_ste" ||
           artifact.training_document_ids_sha256 != std::string(64, '1') ||
           artifact.validation_document_ids_sha256 != std::string(64, '2') ||
           !artifact.calibration_document_ids_sha256.empty()) {
            return fail("linear artifact provenance contract");
        }
        const auto signature = artifact.encoder.encode({{0.25F, 0.25F}});
        const auto projections = artifact.encoder.affine_projections({{0.25F, 0.25F}});
        if(signature.bit_count() != 2 || signature.bit(0) || !signature.bit(1)) {
            return fail("encoder hard-sign contract");
        }
        if(projections.size() != 2 || std::fabs(projections[0] + 0.25F) > 1.0e-6F ||
           std::fabs(projections[1] - 0.5F) > 1.0e-6F) {
            return fail("encoder affine projection contract");
        }
        const auto reconstructed = artifact.decoder.decode(signature);
        if(reconstructed.dimension() != 2 ||
           std::fabs(reconstructed.values[0] + 2.0F) > 1.0e-6F ||
           std::fabs(reconstructed.values[1] - 2.0F) > 1.0e-6F) {
            return fail("decoder hard-code reconstruction contract");
        }
        write_nlb_paper_artifact(root / "nlb-paper-artifact.json");
        const auto nlb_artifact = agent_memory::load_autoencoder_binary_artifact(
            root / "nlb-paper-artifact.json"
        );
        const auto nlb_signature = nlb_artifact.encoder.encode({{2.0F, -2.0F}});
        const auto nlb_reconstructed = nlb_artifact.decoder.decode(nlb_signature);
        if(nlb_artifact.encoder.info().encoder_id != "nlb_paper_tied" ||
           !nlb_artifact.training_document_ids_sha256.empty() ||
           !nlb_artifact.validation_document_ids_sha256.empty() ||
           !nlb_signature.bit(0) || nlb_signature.bit(1) ||
           std::fabs(nlb_reconstructed.values[0] - std::tanh(1.0F)) > 1.0e-6F ||
           std::fabs(nlb_reconstructed.values[1]) > 1.0e-6F) {
            return fail("NLB-paper tied artifact contract");
        }
        write_nlb_median_threshold_artifact(root / "nlb-median-threshold-artifact.json");
        const auto nlb_median_artifact = agent_memory::load_autoencoder_binary_artifact(
            root / "nlb-median-threshold-artifact.json"
        );
        const auto nlb_median_signature = nlb_median_artifact.encoder.encode({{0.25F, 0.25F}});
        const auto nlb_median_reconstructed = nlb_median_artifact.decoder.decode(
            nlb_median_signature
        );
        if(nlb_median_artifact.encoder.info().encoder_id != "nlb_median_threshold" ||
           nlb_median_artifact.calibration_document_ids_sha256 != std::string(64, 'a') ||
           nlb_median_signature.bit(0) || !nlb_median_signature.bit(1) ||
           std::fabs(nlb_median_reconstructed.values[0]) > 1.0e-6F ||
           std::fabs(nlb_median_reconstructed.values[1] - std::tanh(1.0F)) > 1.0e-6F) {
            return fail("NLB median-threshold artifact contract");
        }
        write_nlb_quantile_threshold_artifact(root / "nlb-quantile-threshold-artifact.json");
        const auto nlb_quantile_artifact = agent_memory::load_autoencoder_binary_artifact(
            root / "nlb-quantile-threshold-artifact.json"
        );
        if(nlb_quantile_artifact.encoder.info().encoder_id != "nlb_quantile_threshold" ||
           nlb_quantile_artifact.encoder.encode({{0.25F, 0.25F}}).bit(0)) {
            return fail("NLB quantile-threshold artifact contract");
        }
        write_nlb_retrieval_distilled_artifact(root / "nlb-retrieval-distilled-artifact.json");
        const auto nlb_retrieval_artifact = agent_memory::load_autoencoder_binary_artifact(
            root / "nlb-retrieval-distilled-artifact.json"
        );
        if(nlb_retrieval_artifact.encoder.info().encoder_id != "nlb_retrieval_distilled" ||
           nlb_retrieval_artifact.training_document_ids_sha256 != std::string(64, '1') ||
           nlb_retrieval_artifact.validation_document_ids_sha256 != std::string(64, '2') ||
           nlb_retrieval_artifact.encoder.encode({{0.25F, 0.25F}}).bit(0)) {
            return fail("NLB retrieval-distilled artifact contract");
        }
        write_nlb_retrieval_distilled_artifact(root / "nlb-retrieval-distilled-artifact.json");
        replace_once(
            root / "nlb-retrieval-distilled-artifact.json",
            "agent-memory-cpp:nlb-retrieval-finetuner",
            "agent-memory-cpp:nlb-median-preserving-finetuner"
        );
        replace_once(
            root / "nlb-retrieval-distilled-artifact.json",
            "\"family\": \"nlb_retrieval_distilled_v1\"",
            "\"family\": \"nlb_median_preserving_retrieval_v1\""
        );
        replace_once(
            root / "nlb-retrieval-distilled-artifact.json",
            "\"objective\": \"document_geometry_distillation_v1\"",
            "\"objective\": \"document_geometry_distillation_v1\", \"bias_policy\": \"recalibrate_document_median_each_epoch_v1\""
        );
        const auto median_preserving_artifact = agent_memory::load_autoencoder_binary_artifact(
            root / "nlb-retrieval-distilled-artifact.json"
        );
        if(median_preserving_artifact.encoder.info().encoder_id !=
           "nlb_median_preserving_retrieval") {
            return fail("median-preserving NLB retrieval artifact contract");
        }
        write_nlb_retrieval_distilled_artifact(root / "nlb-retrieval-distilled-artifact.json");
        replace_once(
            root / "nlb-retrieval-distilled-artifact.json",
            "agent-memory-cpp:nlb-retrieval-finetuner",
            "agent-memory-cpp:nlb-local-geometry-finetuner"
        );
        replace_once(
            root / "nlb-retrieval-distilled-artifact.json",
            "\"family\": \"nlb_retrieval_distilled_v1\"",
            "\"family\": \"nlb_local_geometry_v1\""
        );
        replace_once(
            root / "nlb-retrieval-distilled-artifact.json",
            "\"objective\": \"document_geometry_distillation_v1\"",
            "\"objective\": \"document_only_local_neighbour_margin_v1\", \"bias_policy\": \"recalibrate_document_median_each_epoch_v1\", \"local_neighbour\": {\"id\": \"in_batch_teacher_rank_margin_v1\", \"positive_rank\": 1, \"negative_rank\": 8, \"margin\": 0.05, \"queries_or_qrels_used\": false}"
        );
        replace_once(
            root / "nlb-retrieval-distilled-artifact.json",
            "\"row_orthogonality\": 0.0}",
            "\"row_orthogonality\": 0.0, \"local_neighbour\": 0.1}"
        );
        const auto local_geometry_artifact = agent_memory::load_autoencoder_binary_artifact(
            root / "nlb-retrieval-distilled-artifact.json"
        );
        if(local_geometry_artifact.encoder.info().encoder_id != "nlb_local_geometry") {
            return fail("local-geometry NLB artifact contract");
        }
        write_nlb_qrels_supervised_artifact(root / "nlb-qrels-supervised-artifact.json");
        const auto qrels_supervised_artifact = agent_memory::load_autoencoder_binary_artifact(
            root / "nlb-qrels-supervised-artifact.json"
        );
        if(qrels_supervised_artifact.encoder.info().encoder_id != "nlb_qrels_supervised") {
            return fail("qrels-supervised NLB artifact contract");
        }
        write_nlb_qrels_supervised_artifact(root / "nlb-qrels-supervised-artifact.json");
        replace_once(
            root / "nlb-qrels-supervised-artifact.json",
            "\"optimization_qrels_used\": true",
            "\"optimization_qrels_used\": false"
        );
        if(!throws_runtime_error([&] {
               (void)agent_memory::load_autoencoder_binary_artifact(
                   root / "nlb-qrels-supervised-artifact.json"
               );
           })) {
            return fail("qrels-supervised artifact accepted mismatched qrels provenance");
        }
        write_nlb_qrels_supervised_artifact(root / "nlb-qrels-supervised-artifact.json");
        replace_once(
            root / "nlb-qrels-supervised-artifact.json",
            "\"triplet\": 1.0",
            "\"triplet\": 0.0"
        );
        if(!throws_runtime_error([&] {
               (void)agent_memory::load_autoencoder_binary_artifact(
                   root / "nlb-qrels-supervised-artifact.json"
               );
           })) {
            return fail("qrels-supervised artifact accepted zero-triplet qrels provenance");
        }
        write_nlb_qrels_supervised_artifact(root / "nlb-qrels-supervised-artifact.json");
        replace_once(
            root / "nlb-qrels-supervised-artifact.json",
            "\"optimization_qrels_used\": true",
            "\"optimization_qrels_used\": false"
        );
        replace_once(
            root / "nlb-qrels-supervised-artifact.json",
            "\"triplet\": 1.0",
            "\"triplet\": 0.0"
        );
        if(agent_memory::load_autoencoder_binary_artifact(
               root / "nlb-qrels-supervised-artifact.json"
           ).encoder.info().encoder_id != "nlb_qrels_supervised") {
            return fail("qrels-supervised no-triplet artifact contract");
        }
        write_nlb_qrels_supervised_artifact(root / "nlb-qrels-supervised-artifact.json");
        replace_once(
            root / "nlb-qrels-supervised-artifact.json",
            "\"mode\": \"pca_median_document_only_v1\"",
            "\"mode\": \"itq_median_document_only_v1\""
        );
        replace_once(
            root / "nlb-qrels-supervised-artifact.json",
            "\"itq_iterations\": 0",
            "\"itq_iterations\": 50"
        );
        if(agent_memory::load_autoencoder_binary_artifact(
               root / "nlb-qrels-supervised-artifact.json"
           ).encoder.info().encoder_id != "nlb_qrels_supervised") {
            return fail("qrels-supervised ITQ initialization contract");
        }
        write_nlb_qrels_supervised_artifact(root / "nlb-qrels-supervised-artifact.json");
        replace_once(
            root / "nlb-qrels-supervised-artifact.json",
            "\"seed\": 42, \"epochs\": 1,",
            "\"seed\": 42, \"epochs\": 0,"
        );
        replace_once(
            root / "nlb-qrels-supervised-artifact.json",
            "\"consumed_negative_count_per_query\": 1",
            "\"consumed_negative_count_per_query\": 0"
        );
        replace_once(
            root / "nlb-qrels-supervised-artifact.json",
            "\"selected_epoch\": 0, \"metrics\"",
            "\"selected_epoch\": -1, \"metrics\""
        );
        replace_once(
            root / "nlb-qrels-supervised-artifact.json",
            "\"planned_epoch_count\": 1, \"completed_epoch_count\": 1, \"selected_epoch\": 0, \"selected_optimizer_step_count\": 1, \"selected_consumed_negative_count_per_query\": 1",
            "\"planned_epoch_count\": 0, \"completed_epoch_count\": 0, \"selected_epoch\": -1, \"selected_optimizer_step_count\": 0, \"selected_consumed_negative_count_per_query\": 0"
        );
        if(agent_memory::load_autoencoder_binary_artifact(
               root / "nlb-qrels-supervised-artifact.json"
           ).encoder.info().encoder_id != "nlb_qrels_supervised") {
            return fail("qrels-supervised zero-step artifact contract");
        }
        write_nlb_qrels_supervised_artifact(root / "nlb-qrels-supervised-artifact.json");
        replace_once(
            root / "nlb-qrels-supervised-artifact.json",
            "\"candidate_limit\": 512, \"margin\"",
            "\"candidate_limit\": 256, \"margin\""
        );
        if(!throws_runtime_error([&] {
               (void)agent_memory::load_autoencoder_binary_artifact(
                   root / "nlb-qrels-supervised-artifact.json"
               );
           })) {
            return fail("qrels-supervised artifact accepted a non-fixed candidate limit");
        }
        write_nlb_qrels_supervised_artifact(root / "nlb-qrels-supervised-artifact.json");
        replace_once(
            root / "nlb-qrels-supervised-artifact.json",
            "epoch_indexed_without_replacement_multi_negative_v1",
            "epoch_indexed_with_replacement_multi_negative_v1"
        );
        if(!throws_runtime_error([&] {
               (void)agent_memory::load_autoencoder_binary_artifact(
                   root / "nlb-qrels-supervised-artifact.json"
               );
           })) {
            return fail("qrels-supervised artifact accepted replacement negative sampling");
        }
        write_nlb_qrels_supervised_artifact(root / "nlb-qrels-supervised-artifact.json");
        replace_once(
            root / "nlb-qrels-supervised-artifact.json",
            "\"selected_consumed_negative_count_per_query\": 1",
            "\"selected_consumed_negative_count_per_query\": 0"
        );
        if(!throws_runtime_error([&] {
               (void)agent_memory::load_autoencoder_binary_artifact(
                   root / "nlb-qrels-supervised-artifact.json"
               );
           })) {
            return fail("qrels-supervised artifact accepted inconsistent selected provenance");
        }
        write_nlb_retrieval_distilled_artifact(root / "nlb-retrieval-distilled-artifact.json");
        replace_once(
            root / "nlb-retrieval-distilled-artifact.json",
            "\"source_encoder_artifact_sha256\": \"cccccccccccccccccccccccccccccccc"
            "cccccccccccccccccccccccccccccccc\"",
            "\"source_encoder_artifact_sha256\": \"dddddddddddddddddddddddddddddddd"
            "dddddddddddddddddddddddddddddddd\""
        );
        if(!throws_runtime_error([&] {
               (void)agent_memory::load_autoencoder_binary_artifact(
                   root / "nlb-retrieval-distilled-artifact.json"
               );
           })) {
            return fail("NLB retrieval artifact accepted mismatched initialization source");
        }
        write_nlb_retrieval_distilled_artifact(root / "nlb-retrieval-distilled-artifact.json");
        replace_once(
            root / "nlb-retrieval-distilled-artifact.json",
            "\"queries_or_qrels_used\": false",
            "\"queries_or_qrels_used\": true"
        );
        if(!throws_runtime_error([&] {
               (void)agent_memory::load_autoencoder_binary_artifact(
                   root / "nlb-retrieval-distilled-artifact.json"
               );
           })) {
            return fail("NLB retrieval artifact accepted query or qrels training");
        }
        write_nlb_retrieval_distilled_artifact(root / "nlb-retrieval-distilled-artifact.json");
        replace_once(
            root / "nlb-retrieval-distilled-artifact.json",
            "\"student\": \"soft_binary_cosine_v1\"",
            "\"student\": \"soft_binary_normalized_dot\""
        );
        if(!throws_runtime_error([&] {
               (void)agent_memory::load_autoencoder_binary_artifact(
                   root / "nlb-retrieval-distilled-artifact.json"
               );
           })) {
            return fail("NLB retrieval artifact accepted obsolete soft-code scorer metadata");
        }
        write_nlb_retrieval_distilled_artifact(root / "nlb-retrieval-distilled-artifact.json");
        const auto metrics = agent_memory::evaluate_autoencoder_binary_retrieval(
            {"d0", "d1"},
            {{{1.0F, 0.0F}}, {{0.0F, 1.0F}}},
            {{{1.0F, 0.0F}}},
            artifact.encoder,
            artifact.decoder,
            {1, 1}
        );
        if(metrics.exact_top_k_candidate_coverage != 1.0 ||
           metrics.reranked_recall_at_k_vs_exact != 1.0 ||
           metrics.decoder_recall_at_k_vs_exact != 1.0 ||
           metrics.random_candidate_coverage_expectation != 0.5 ||
           metrics.candidate_coverage_lift_vs_random != 2.0) {
            return fail("three-mode retrieval evaluation contract");
        }
        const auto asymmetric_metrics = agent_memory::evaluate_autoencoder_binary_retrieval(
            {"d0", "d1"},
            {{{1.0F, 0.0F}}, {{0.0F, 1.0F}}},
            {{{1.0F, 0.0F}}},
            artifact.encoder,
            artifact.decoder,
            {1, 1, agent_memory::AutoencoderBinaryCandidateScoring::AsymmetricAffineDot}
        );
        if(asymmetric_metrics.exact_top_k_candidate_coverage != 1.0 ||
           asymmetric_metrics.reranked_recall_at_k_vs_exact != 1.0) {
            return fail("asymmetric affine candidate evaluation contract");
        }
        const agent_memory::AutoencoderBinaryEncoder tie_encoder({
            2, 2, 0, std::string(64, 'a'), {1.0F, 0.0F, 0.0F, 1.0F}, {0.0F, 0.0F},
            "tie_test", "v1", agent_memory::AutoencoderBinaryInputTransform::Identity,
        });
        const auto zero_projection_signature = tie_encoder.encode({{0.0F, 0.0F}});
        if(!zero_projection_signature.bit(0) || !zero_projection_signature.bit(1)) {
            return fail("zero projection must set an autoencoder bit");
        }
        const agent_memory::AutoencoderBinaryDecoder tie_decoder({
            2, 2, {1.0F, 0.0F, 0.0F, 1.0F}, {0.0F, 0.0F},
            agent_memory::AutoencoderBinaryCodeValueEncoding::ZeroToOne,
            agent_memory::AutoencoderBinaryDecoderActivation::Identity,
        });
        const auto tie_first = agent_memory::evaluate_autoencoder_binary_retrieval(
            {"document-b", "document-a"},
            {{{0.2F, 1.0F}}, {{1.0F, 0.2F}}},
            {{{1.0F, 0.2F}}}, tie_encoder, tie_decoder, {1, 1}
        );
        const auto tie_permuted = agent_memory::evaluate_autoencoder_binary_retrieval(
            {"document-a", "document-b"},
            {{{1.0F, 0.2F}}, {{0.2F, 1.0F}}},
            {{{1.0F, 0.2F}}}, tie_encoder, tie_decoder, {1, 1}
        );
        if(tie_first.exact_top_k_candidate_coverage != 1.0 ||
           tie_first.exact_top_k_candidate_coverage !=
               tie_permuted.exact_top_k_candidate_coverage ||
           tie_first.reranked_recall_at_k_vs_exact !=
               tie_permuted.reranked_recall_at_k_vs_exact) {
            return fail("document-ID tie breaking must be permutation invariant");
        }
        const auto& diagnostics = metrics.code_diagnostics;
        if(diagnostics.unique_document_code_count != 2 ||
           diagnostics.unique_document_code_fraction != 1.0 ||
           diagnostics.document_code_health.constant_bit_fraction != 0.5 ||
           diagnostics.document_code_health.sampled_pair_count != 1 ||
           diagnostics.document_code_health.sampled_min_pairwise_hamming_distance != 1 ||
           diagnostics.document_code_health.sampled_max_pairwise_hamming_distance != 1 ||
           diagnostics.document_code_health.sampled_pairwise_hamming_distance_stddev != 0.0 ||
           diagnostics.query_document_hamming_distance.sample_count != 2 ||
           std::fabs(diagnostics.query_document_hamming_distance.mean - 0.5) > 1.0e-9 ||
           diagnostics.dense_nearest_hamming_distance.sample_count != 1 ||
           diagnostics.dense_rank_100_hamming_distance.sample_count != 1 ||
           diagnostics.dense_neighbour_hamming_margin.sample_count != 1 ||
           std::fabs(diagnostics.dense_neighbour_hamming_margin.mean - 1.0) > 1.0e-9 ||
           diagnostics.nonpositive_dense_neighbour_hamming_margin_fraction != 0.0 ||
           !diagnostics.cosine_negative_hamming_correlation_defined ||
           std::fabs(
               diagnostics.cosine_negative_hamming_pearson_correlation - 1.0
           ) > 1.0e-9 ||
           std::fabs(diagnostics.decoder_reconstruction_cosine.mean -
                     (1.0 / std::sqrt(2.0))) > 1.0e-6 ||
           std::fabs(diagnostics.decoded_document_norm.mean - std::sqrt(8.0)) > 1.0e-6 ||
           diagnostics.shuffled_decoder_cosine.sample_count != 2) {
            return fail("autoencoder code diagnostic contract");
        }
        const auto decoder_mismatch = agent_memory::evaluate_autoencoder_binary_retrieval(
            {"d0", "d1"},
            {{{1.0F, 0.0F}}, {{0.0F, 1.0F}}},
            {{{0.0F, 1.0F}}},
            artifact.encoder,
            artifact.decoder,
            {1, 1}
        );
        if(decoder_mismatch.exact_top_k_candidate_coverage != 1.0 ||
           decoder_mismatch.reranked_recall_at_k_vs_exact != 1.0 ||
           decoder_mismatch.decoder_recall_at_k_vs_exact != 0.0) {
            return fail("decoder agreement must use only its top-K results");
        }
        const auto materialization_root = root / "materialization";
        std::filesystem::create_directories(materialization_root);
        write_text(materialization_root / "train-ids.jsonl", "{\"id\":\"t0\"}\n{\"id\":\"t1\"}\n");
        write_floats(materialization_root / "train-vectors.f32", {1.0F, 0.0F, 0.0F, 1.0F});
        write_text(materialization_root / "evaluation-document-ids.jsonl", "{\"id\":\"d0\"}\n{\"id\":\"d1\"}\n");
        write_floats(materialization_root / "evaluation-document-vectors.f32", {1.0F, 0.0F, 0.0F, 1.0F});
        write_text(materialization_root / "evaluation-query-ids.jsonl", "{\"id\":\"q0\"}\n");
        write_floats(materialization_root / "evaluation-query-vectors.f32", {1.0F, 0.0F});
        write_text(materialization_root / "evaluation-qrels.tsv", "q0 Q0 d0 1\n");
        write_text(materialization_root / "prepared-study-manifest.json", "{}");
        write_materialization_manifest(materialization_root / "manifest.json");
        const auto materialization = agent_memory::load_materialized_autoencoder_evaluation_dataset(
            materialization_root
        );
        if(materialization.training_embeddings.size() != 2 ||
           materialization.document_embeddings.size() != 2 ||
           materialization.query_embeddings.size() != 1 ||
           materialization.judgments.size() != 1 ||
           materialization.judgments.front().item_id != "d0") {
            return fail("materialized E5 evaluation loader contract");
        }
        std::vector<std::string> document_ids;
        std::vector<agent_memory::Embedding> document_embeddings;
        for(const auto& record : materialization.document_embeddings) {
            document_ids.push_back(record.id);
            document_embeddings.push_back(record.embedding);
        }
        std::vector<std::string> query_ids;
        std::vector<agent_memory::Embedding> query_embeddings;
        for(const auto& record : materialization.query_embeddings) {
            query_ids.push_back(record.id);
            query_embeddings.push_back(record.embedding);
        }
        const auto qrels_evaluation =
            agent_memory::evaluate_autoencoder_binary_retrieval_with_qrels(
                document_ids,
                document_embeddings,
                query_ids,
                query_embeddings,
                materialization.judgments,
                artifact.encoder,
                artifact.decoder,
                {1, 1},
                {{1}, {1}}
            );
        const auto reranked_recall = agent_memory::metric_value_at(
            qrels_evaluation.binary_rerank_metrics.recall_at,
            1
        );
        if(!reranked_recall || *reranked_recall != 1.0) {
            return fail("qrels retrieval evaluation contract");
        }
        const auto cascade_evaluation =
            agent_memory::evaluate_autoencoder_binary_cascade_with_qrels(
                document_ids,
                document_embeddings,
                query_ids,
                query_embeddings,
                materialization.judgments,
                artifact.encoder,
                {1, 2, 1, 0, 1},
                {{1}, {1}}
            );
        if(cascade_evaluation.document_count != 2 ||
           cascade_evaluation.query_count != 1 ||
           cascade_evaluation.hamming_candidate_limit != 2 ||
           cascade_evaluation.asymmetric_candidate_limit != 1 ||
           cascade_evaluation.binary_payload_bytes != 16 ||
           cascade_evaluation.float_payload_bytes != 16 ||
           cascade_evaluation.qrels_positive_query_count != 1 ||
           cascade_evaluation.hamming_exact_top_k_candidate_coverage != 1.0 ||
           cascade_evaluation.asymmetric_exact_top_k_candidate_coverage != 1.0 ||
           cascade_evaluation.hamming_qrels_positive_candidate_coverage != 1.0 ||
           cascade_evaluation.asymmetric_qrels_positive_candidate_coverage != 1.0 ||
           cascade_evaluation.reranked_recall_at_k_vs_exact != 1.0 ||
           cascade_evaluation.timing.query_projection_ms.sample_count != 1 ||
           cascade_evaluation.timing.hamming_candidate_search_ms.sample_count != 1 ||
           cascade_evaluation.timing.asymmetric_lut_build_ms.sample_count != 1 ||
           cascade_evaluation.timing.asymmetric_candidate_search_ms.sample_count != 1 ||
           cascade_evaluation.timing.exact_rerank_ms.sample_count != 1 ||
           cascade_evaluation.timing.total_query_pipeline_ms.sample_count != 1) {
            return fail("three-stage binary cascade evaluation contract");
        }
        const std::vector<std::string> parity_query_ids{"q0", "q1"};
        const std::vector<agent_memory::Embedding> parity_query_embeddings{
            {{1.0F, 0.0F}}, {{0.0F, 1.0F}}
        };
        const std::vector<agent_memory::RelevanceJudgment> parity_judgments{
            {"q0", "d0", 1}, {"q1", "d0", 1}
        };
        const auto streamed_parity =
            agent_memory::evaluate_autoencoder_binary_retrieval_with_qrels(
                document_ids,
                document_embeddings,
                parity_query_ids,
                parity_query_embeddings,
                parity_judgments,
                artifact.encoder,
                artifact.decoder,
                {1, 1},
                {{1}, {1}}
            );
        agent_memory::RetrievalEvalDataset full_dataset;
        full_dataset.queries = {
            {"q0", "q0", {}, 1, {}, agent_memory::EvalQueryAnswerMode::JudgedRetrieval},
            {"q1", "q1", {}, 1, {}, agent_memory::EvalQueryAnswerMode::JudgedRetrieval},
        };
        full_dataset.judgments = parity_judgments;
        const agent_memory::RetrievalRun full_run{"original", {
            {"q0", {{"d0", 1.0F, 0, "original"}, {"d1", 0.0F, 0, "original"}}},
            {"q1", {{"d1", 1.0F, 0, "original"}, {"d0", 0.0F, 0, "original"}}},
        }};
        const auto retained_parity = agent_memory::evaluate_retrieval(
            full_dataset, full_run, {{1}, {1}}
        );
        const auto streamed_recall = agent_memory::metric_value_at(
            streamed_parity.original_float_metrics.recall_at, 1
        );
        const auto retained_recall = agent_memory::metric_value_at(
            retained_parity.recall_at, 1
        );
        const auto streamed_ndcg = agent_memory::metric_value_at(
            streamed_parity.original_float_metrics.ndcg_at, 1
        );
        const auto retained_ndcg = agent_memory::metric_value_at(retained_parity.ndcg_at, 1);
        if(!streamed_recall || !retained_recall || !streamed_ndcg || !retained_ndcg ||
           *streamed_recall != *retained_recall || *streamed_ndcg != *retained_ndcg ||
           streamed_parity.original_float_metrics.mrr != retained_parity.mrr ||
           streamed_parity.original_float_metrics.evaluated_query_count !=
               retained_parity.evaluated_query_count ||
           streamed_parity.original_float_metrics.empty_result_fraction !=
               retained_parity.empty_result_fraction) {
            return fail("streaming qrels metrics must match retained full-run metrics");
        }
        if(!throws_runtime_error([&] {
               auto bytes = std::fstream(
                   root / "encoder-weights.f32",
                   std::ios::in | std::ios::out | std::ios::binary
               );
               bytes.seekp(0);
               bytes.put('\x01');
               bytes.close();
               (void)agent_memory::load_autoencoder_binary_artifact(root / "artifact.json");
           })) {
            return fail("tampered weight file was accepted");
        }
    } catch(const std::exception& error) {
        std::cerr << error.what() << '\n';
        std::filesystem::remove_all(root);
        return 1;
    }
    std::filesystem::remove_all(root);
    return 0;
}
