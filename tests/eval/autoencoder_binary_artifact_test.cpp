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
  "training": {"seed": 42},
  "weights": {
    "encoder_weights": {"path": "encoder-weights.f32", "sha256": "a666c95f0822c64e01580063e9bb27c629d4d0534e3163a9611738599f97df2a", "shape": [2, 2], "layout": "row_major_out_by_in", "dtype": "float32_le"},
    "encoder_bias": {"path": "encoder-bias.f32", "sha256": "1dc7fbfac33e9a09c59d17f9ff8c27e3de8d248f2b7488fbee7768e307abdd33", "shape": [2], "dtype": "float32_le"},
    "decoder_weights": {"path": "decoder-weights.f32", "sha256": "00fde0d04d1701de053663248300b0cb3e09542cc3e9413005a992ea57665a06", "shape": [2, 2], "layout": "row_major_out_by_in", "dtype": "float32_le"},
    "decoder_bias": {"path": "decoder-bias.f32", "sha256": "af5570f5a1810b7af78caf4bc70a660f0df51e42baf91d4de5b2328de0e83dfc", "shape": [2], "dtype": "float32_le"}
  }
})";
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
