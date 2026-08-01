#include <agent_memory/eval/AutoencoderBinaryArtifact.hpp>
#include <agent_memory/eval/AutoencoderBinaryEvaluation.hpp>

#include <cmath>
#include <cstdint>
#include <cstring>
#include <exception>
#include <filesystem>
#include <fstream>
#include <iostream>
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
  "architecture": {
    "family": "linear_binary_autoencoder_ste",
    "input_dimension": 2,
    "bit_count": 2,
    "encoder_activation": "tanh_sign_ste_v1",
    "decoder": "linear"
  },
  "training": {"seed": 42, "shuffle_recipe": {"id": "python_fisher_yates_sha256_seed_v1", "per_epoch": true}},
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

    void write_text(const std::filesystem::path& path, const char* text) {
        std::ofstream output(path, std::ios::binary);
        output << text;
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
        const auto signature = artifact.encoder.encode({{0.25F, 0.25F}});
        if(signature.bit_count() != 2 || signature.bit(0) || !signature.bit(1)) {
            return fail("encoder hard-sign contract");
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
        const auto metrics = agent_memory::evaluate_autoencoder_binary_retrieval(
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
