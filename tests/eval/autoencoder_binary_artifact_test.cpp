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
    "evaluation_document_ids": {"path": "evaluation-document-ids.jsonl", "sha256": "5cf5c698207e8b94589039eb93110df0e6a02fcbdba751c2318d66ded450103c", "count": 2},
    "evaluation_document_vectors": {"path": "evaluation-document-vectors.f32", "sha256": "a666c95f0822c64e01580063e9bb27c629d4d0534e3163a9611738599f97df2a", "count": 2, "dimension": 2, "dtype": "float32_le"},
    "evaluation_query_ids": {"path": "evaluation-query-ids.jsonl", "sha256": "8549672b4c462771e4d8447ada71e66a04caf49137b453f84ce513b2b2b9c522", "count": 1},
    "evaluation_query_vectors": {"path": "evaluation-query-vectors.f32", "sha256": "434b26042aff3fb844a4c4c6be0d81a079b0ce84cfb8190679024404e5dc4822", "count": 1, "dimension": 2, "dtype": "float32_le"},
    "evaluation_qrels": {"path": "evaluation-qrels.tsv", "sha256": "429585cc3fb7af7520536fab26be456312198ba88c62aeeb481461acf0fed71e", "count": 1},
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
        const auto metrics = agent_memory::evaluate_autoencoder_binary_retrieval(
            {{{1.0F, 0.0F}}, {{0.0F, 1.0F}}},
            {{{1.0F, 0.0F}}},
            artifact.encoder,
            artifact.decoder,
            {1, 1}
        );
        if(metrics.exact_top_k_candidate_coverage != 1.0 ||
           metrics.reranked_recall_at_k_vs_exact != 1.0 ||
           metrics.decoder_recall_at_k_vs_exact != 1.0) {
            return fail("three-mode retrieval evaluation contract");
        }
        const auto materialization_root = root / "materialization";
        std::filesystem::create_directories(materialization_root);
        write_text(materialization_root / "evaluation-document-ids.jsonl", "{\"id\":\"d0\"}\n{\"id\":\"d1\"}\n");
        write_floats(materialization_root / "evaluation-document-vectors.f32", {1.0F, 0.0F, 0.0F, 1.0F});
        write_text(materialization_root / "evaluation-query-ids.jsonl", "{\"id\":\"q0\"}\n");
        write_floats(materialization_root / "evaluation-query-vectors.f32", {1.0F, 0.0F});
        write_text(materialization_root / "evaluation-qrels.tsv", "q0\td0\t1\n");
        write_text(materialization_root / "prepared-study-manifest.json", "{}");
        write_materialization_manifest(materialization_root / "manifest.json");
        const auto materialization = agent_memory::load_materialized_autoencoder_evaluation_dataset(
            materialization_root
        );
        if(materialization.document_embeddings.size() != 2 ||
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
