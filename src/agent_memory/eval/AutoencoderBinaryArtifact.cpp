#include "AutoencoderBinaryArtifact.hpp"

#include <nlohmann/json.hpp>

#include <array>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <fstream>
#include <iterator>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <string_view>
#include <utility>
#include <vector>

namespace agent_memory {
    namespace {

        class Sha256 final {
        public:
            void update(const std::uint8_t* data, std::size_t size) {
                for(std::size_t index = 0; index < size; ++index) {
                    m_buffer[m_buffer_size++] = data[index];
                    m_bit_count += 8U;
                    if(m_buffer_size == m_buffer.size()) {
                        transform(m_buffer.data());
                        m_buffer_size = 0;
                    }
                }
            }

            [[nodiscard]] std::array<std::uint8_t, 32> digest() {
                const auto total_bits = m_bit_count;
                m_buffer[m_buffer_size++] = 0x80U;
                if(m_buffer_size > 56U) {
                    while(m_buffer_size < m_buffer.size()) {
                        m_buffer[m_buffer_size++] = 0U;
                    }
                    transform(m_buffer.data());
                    m_buffer_size = 0;
                }
                while(m_buffer_size < 56U) {
                    m_buffer[m_buffer_size++] = 0U;
                }
                for(int shift = 56; shift >= 0; shift -= 8) {
                    m_buffer[m_buffer_size++] =
                        static_cast<std::uint8_t>((total_bits >> shift) & 0xFFU);
                }
                transform(m_buffer.data());

                std::array<std::uint8_t, 32> output{};
                for(std::size_t index = 0; index < m_state.size(); ++index) {
                    output[index * 4] = static_cast<std::uint8_t>(m_state[index] >> 24U);
                    output[index * 4 + 1] = static_cast<std::uint8_t>(m_state[index] >> 16U);
                    output[index * 4 + 2] = static_cast<std::uint8_t>(m_state[index] >> 8U);
                    output[index * 4 + 3] = static_cast<std::uint8_t>(m_state[index]);
                }
                return output;
            }

        private:
            static constexpr std::array<std::uint32_t, 64> kRoundConstants{{
                0x428A2F98U, 0x71374491U, 0xB5C0FBCFU, 0xE9B5DBA5U,
                0x3956C25BU, 0x59F111F1U, 0x923F82A4U, 0xAB1C5ED5U,
                0xD807AA98U, 0x12835B01U, 0x243185BEU, 0x550C7DC3U,
                0x72BE5D74U, 0x80DEB1FEU, 0x9BDC06A7U, 0xC19BF174U,
                0xE49B69C1U, 0xEFBE4786U, 0x0FC19DC6U, 0x240CA1CCU,
                0x2DE92C6FU, 0x4A7484AAU, 0x5CB0A9DCU, 0x76F988DAU,
                0x983E5152U, 0xA831C66DU, 0xB00327C8U, 0xBF597FC7U,
                0xC6E00BF3U, 0xD5A79147U, 0x06CA6351U, 0x14292967U,
                0x27B70A85U, 0x2E1B2138U, 0x4D2C6DFCU, 0x53380D13U,
                0x650A7354U, 0x766A0ABBU, 0x81C2C92EU, 0x92722C85U,
                0xA2BFE8A1U, 0xA81A664BU, 0xC24B8B70U, 0xC76C51A3U,
                0xD192E819U, 0xD6990624U, 0xF40E3585U, 0x106AA070U,
                0x19A4C116U, 0x1E376C08U, 0x2748774CU, 0x34B0BCB5U,
                0x391C0CB3U, 0x4ED8AA4AU, 0x5B9CCA4FU, 0x682E6FF3U,
                0x748F82EEU, 0x78A5636FU, 0x84C87814U, 0x8CC70208U,
                0x90BEFFFAU, 0xA4506CEBU, 0xBEF9A3F7U, 0xC67178F2U,
            }};

            [[nodiscard]] static std::uint32_t rotate_right(std::uint32_t value, int bits) {
                return (value >> bits) | (value << (32 - bits));
            }

            [[nodiscard]] static std::uint32_t read_be32(const std::uint8_t* data) {
                return (static_cast<std::uint32_t>(data[0]) << 24U) |
                    (static_cast<std::uint32_t>(data[1]) << 16U) |
                    (static_cast<std::uint32_t>(data[2]) << 8U) |
                    static_cast<std::uint32_t>(data[3]);
            }

            void transform(const std::uint8_t* chunk) {
                std::array<std::uint32_t, 64> words{};
                for(std::size_t index = 0; index < 16U; ++index) {
                    words[index] = read_be32(chunk + index * 4U);
                }
                for(std::size_t index = 16U; index < words.size(); ++index) {
                    const auto sigma0 = rotate_right(words[index - 15U], 7) ^
                        rotate_right(words[index - 15U], 18) ^ (words[index - 15U] >> 3U);
                    const auto sigma1 = rotate_right(words[index - 2U], 17) ^
                        rotate_right(words[index - 2U], 19) ^ (words[index - 2U] >> 10U);
                    words[index] = sigma1 + words[index - 7U] + sigma0 + words[index - 16U];
                }

                auto a = m_state[0]; auto b = m_state[1]; auto c = m_state[2]; auto d = m_state[3];
                auto e = m_state[4]; auto f = m_state[5]; auto g = m_state[6]; auto h = m_state[7];
                for(std::size_t index = 0; index < words.size(); ++index) {
                    const auto sigma1 = rotate_right(e, 6) ^ rotate_right(e, 11) ^ rotate_right(e, 25);
                    const auto choose = (e & f) ^ (~e & g);
                    const auto temp1 = h + sigma1 + choose + kRoundConstants[index] + words[index];
                    const auto sigma0 = rotate_right(a, 2) ^ rotate_right(a, 13) ^ rotate_right(a, 22);
                    const auto majority = (a & b) ^ (a & c) ^ (b & c);
                    const auto temp2 = sigma0 + majority;
                    h = g; g = f; f = e; e = d + temp1; d = c; c = b; b = a; a = temp1 + temp2;
                }
                m_state[0] += a; m_state[1] += b; m_state[2] += c; m_state[3] += d;
                m_state[4] += e; m_state[5] += f; m_state[6] += g; m_state[7] += h;
            }

            std::array<std::uint32_t, 8> m_state{{
                0x6A09E667U, 0xBB67AE85U, 0x3C6EF372U, 0xA54FF53AU,
                0x510E527FU, 0x9B05688CU, 0x1F83D9ABU, 0x5BE0CD19U,
            }};
            std::array<std::uint8_t, 64> m_buffer{};
            std::size_t m_buffer_size = 0;
            std::uint64_t m_bit_count = 0;
        };

        [[nodiscard]] std::string sha256_hex(const std::vector<std::uint8_t>& bytes) {
            Sha256 sha;
            sha.update(bytes.data(), bytes.size());
            constexpr char kHex[] = "0123456789abcdef";
            std::string output;
            output.reserve(64);
            for(const auto byte : sha.digest()) {
                output.push_back(kHex[(byte >> 4U) & 0x0FU]);
                output.push_back(kHex[byte & 0x0FU]);
            }
            return output;
        }

        [[nodiscard]] std::vector<std::uint8_t> read_file_bytes(
            const std::filesystem::path& path
        ) {
            std::ifstream input(path, std::ios::binary);
            if(!input) {
                throw std::runtime_error("cannot read autoencoder artifact file: " + path.string());
            }
            return {
                std::istreambuf_iterator<char>{input},
                std::istreambuf_iterator<char>{}
            };
        }

        [[nodiscard]] const nlohmann::json& require_field(
            const nlohmann::json& object,
            const char* name
        ) {
            if(!object.is_object()) {
                throw std::runtime_error("autoencoder artifact field owner must be an object");
            }
            const auto iterator = object.find(name);
            if(iterator == object.end()) {
                throw std::runtime_error(std::string{"autoencoder artifact missing field: "} + name);
            }
            return *iterator;
        }

        [[nodiscard]] std::string require_string(
            const nlohmann::json& object,
            const char* name
        ) {
            const auto& value = require_field(object, name);
            if(!value.is_string() || value.get_ref<const std::string&>().empty()) {
                throw std::runtime_error(std::string{"autoencoder artifact field must be non-empty string: "} + name);
            }
            return value.get<std::string>();
        }

        [[nodiscard]] std::string require_sha256(
            const nlohmann::json& object,
            const char* name
        ) {
            const auto value = require_string(object, name);
            if(value.size() != 64U) {
                throw std::runtime_error(std::string{"autoencoder artifact field must be SHA-256: "} + name);
            }
            for(const auto character : value) {
                if((character < '0' || character > '9') &&
                   (character < 'a' || character > 'f')) {
                    throw std::runtime_error(
                        std::string{"autoencoder artifact field must be SHA-256: "} + name
                    );
                }
            }
            return value;
        }

        [[nodiscard]] std::size_t require_positive_size(
            const nlohmann::json& object,
            const char* name
        ) {
            const auto& value = require_field(object, name);
            if(!value.is_number_unsigned()) {
                throw std::runtime_error(std::string{"autoencoder artifact field must be positive integer: "} + name);
            }
            const auto parsed = value.get<std::uint64_t>();
            if(parsed == 0 || parsed > std::numeric_limits<std::size_t>::max()) {
                throw std::runtime_error(std::string{"autoencoder artifact field must be positive integer: "} + name);
            }
            return static_cast<std::size_t>(parsed);
        }

        [[nodiscard]] std::uint64_t require_u64(const nlohmann::json& object, const char* name) {
            const auto& value = require_field(object, name);
            if(!value.is_number_unsigned()) {
                throw std::runtime_error(std::string{"autoencoder artifact field must be unsigned integer: "} + name);
            }
            return value.get<std::uint64_t>();
        }

        [[nodiscard]] std::size_t checked_product(
            std::size_t lhs,
            std::size_t rhs,
            const char* description
        ) {
            if(lhs > std::numeric_limits<std::size_t>::max() / rhs) {
                throw std::runtime_error(std::string{description} + " size overflows size_t");
            }
            return lhs * rhs;
        }

        [[nodiscard]] std::vector<float> load_weight_file(
            const std::filesystem::path& artifact_directory,
            const nlohmann::json& entry,
            const std::vector<std::size_t>& expected_shape,
            const char* expected_layout
        ) {
            const auto relative = std::filesystem::path{require_string(entry, "path")};
            if(relative.is_absolute() || relative.filename() != relative || relative.string() == ".") {
                throw std::runtime_error("autoencoder weight path must be a plain file name");
            }
            if(require_string(entry, "dtype") != "float32_le") {
                throw std::runtime_error("autoencoder weights must use float32_le");
            }
            if(expected_layout != nullptr && require_string(entry, "layout") != expected_layout) {
                throw std::runtime_error("autoencoder weight layout mismatch");
            }
            const auto& shape = require_field(entry, "shape");
            if(!shape.is_array() || shape.size() != expected_shape.size()) {
                throw std::runtime_error("autoencoder weight shape mismatch");
            }
            for(std::size_t index = 0; index < expected_shape.size(); ++index) {
                if(!shape[index].is_number_unsigned() ||
                   shape[index].get<std::uint64_t>() != expected_shape[index]) {
                    throw std::runtime_error("autoencoder weight shape mismatch");
                }
            }
            const auto bytes = read_file_bytes(artifact_directory / relative);
            if(sha256_hex(bytes) != require_sha256(entry, "sha256")) {
                throw std::runtime_error("autoencoder weight SHA-256 mismatch: " + relative.string());
            }
            std::size_t element_count = 1;
            for(const auto dimension : expected_shape) {
                element_count = checked_product(element_count, dimension, "autoencoder weight");
            }
            if(bytes.size() != checked_product(element_count, sizeof(float), "autoencoder weight")) {
                throw std::runtime_error("autoencoder weight byte size mismatch: " + relative.string());
            }
            std::vector<float> output(element_count);
            for(std::size_t index = 0; index < element_count; ++index) {
                const auto offset = index * sizeof(float);
                const auto bits = static_cast<std::uint32_t>(bytes[offset]) |
                    (static_cast<std::uint32_t>(bytes[offset + 1U]) << 8U) |
                    (static_cast<std::uint32_t>(bytes[offset + 2U]) << 16U) |
                    (static_cast<std::uint32_t>(bytes[offset + 3U]) << 24U);
                std::memcpy(&output[index], &bits, sizeof(bits));
                if(!std::isfinite(output[index])) {
                    throw std::runtime_error("autoencoder weight must be finite: " + relative.string());
                }
            }
            return output;
        }

        [[nodiscard]] std::filesystem::path resolve_plain_file(
            const std::filesystem::path& root,
            const nlohmann::json& entry,
            const char* field_name
        ) {
            const auto relative = std::filesystem::path{require_string(entry, "path")};
            if(relative.is_absolute() || relative.filename() != relative || relative.string() == ".") {
                throw std::runtime_error(std::string{field_name} + " must be a plain file name");
            }
            return root / relative;
        }

        void require_output_hash(
            const std::filesystem::path& path,
            const nlohmann::json& entry,
            const char* name
        ) {
            const auto bytes = read_file_bytes(path);
            if(sha256_hex(bytes) != require_sha256(entry, "sha256")) {
                throw std::runtime_error(std::string{"materialized output SHA-256 mismatch: "} + name);
            }
        }

        [[nodiscard]] std::vector<std::string> load_record_ids(
            const std::filesystem::path& root,
            const nlohmann::json& entry,
            const char* name
        ) {
            const auto path = resolve_plain_file(root, entry, name);
            require_output_hash(path, entry, name);
            const auto bytes = read_file_bytes(path);
            std::vector<std::string> ids;
            std::string line;
            for(const auto byte : bytes) {
                if(byte == '\n') {
                    if(line.empty()) {
                        throw std::runtime_error(std::string{"empty materialized ID row: "} + name);
                    }
                    try {
                        const auto row = nlohmann::json::parse(line);
                        ids.push_back(require_string(row, "id"));
                    } catch(const nlohmann::json::exception& error) {
                        throw std::runtime_error(
                            std::string{"invalid materialized ID row: "} + error.what()
                        );
                    }
                    line.clear();
                } else if(byte != '\r') {
                    line.push_back(static_cast<char>(byte));
                }
            }
            if(!line.empty()) {
                throw std::runtime_error(std::string{"materialized ID file must end with newline: "} + name);
            }
            if(!entry.contains("count") || !entry.at("count").is_number_unsigned() ||
               entry.at("count").get<std::uint64_t>() != ids.size()) {
                throw std::runtime_error(std::string{"materialized ID count mismatch: "} + name);
            }
            auto sorted_ids = ids;
            std::sort(sorted_ids.begin(), sorted_ids.end());
            if(std::adjacent_find(sorted_ids.begin(), sorted_ids.end()) != sorted_ids.end()) {
                throw std::runtime_error(std::string{"duplicate materialized ID: "} + name);
            }
            return ids;
        }

        [[nodiscard]] std::vector<Embedding> load_embedding_rows(
            const std::filesystem::path& root,
            const nlohmann::json& entry,
            std::size_t expected_count,
            std::size_t dimension,
            const char* name
        ) {
            if(require_string(entry, "dtype") != "float32_le" ||
               !entry.contains("count") || !entry.at("count").is_number_unsigned() ||
               entry.at("count").get<std::uint64_t>() != expected_count ||
               !entry.contains("dimension") || !entry.at("dimension").is_number_unsigned() ||
               entry.at("dimension").get<std::uint64_t>() != dimension) {
                throw std::runtime_error(std::string{"materialized vector descriptor mismatch: "} + name);
            }
            const auto path = resolve_plain_file(root, entry, name);
            const auto bytes = read_file_bytes(path);
            if(sha256_hex(bytes) != require_sha256(entry, "sha256")) {
                throw std::runtime_error(std::string{"materialized vector SHA-256 mismatch: "} + name);
            }
            const auto expected_bytes = checked_product(
                checked_product(expected_count, dimension, "materialized vector"),
                sizeof(float),
                "materialized vector"
            );
            if(bytes.size() != expected_bytes) {
                throw std::runtime_error(std::string{"materialized vector byte size mismatch: "} + name);
            }
            std::vector<Embedding> output(expected_count);
            for(std::size_t row = 0; row < expected_count; ++row) {
                auto& vector = output[row].values;
                vector.resize(dimension);
                for(std::size_t column = 0; column < dimension; ++column) {
                    const auto offset = (row * dimension + column) * sizeof(float);
                    const auto bits = static_cast<std::uint32_t>(bytes[offset]) |
                        (static_cast<std::uint32_t>(bytes[offset + 1U]) << 8U) |
                        (static_cast<std::uint32_t>(bytes[offset + 2U]) << 16U) |
                        (static_cast<std::uint32_t>(bytes[offset + 3U]) << 24U);
                    std::memcpy(&vector[column], &bits, sizeof(bits));
                    if(!std::isfinite(vector[column])) {
                        throw std::runtime_error(std::string{"non-finite materialized vector value: "} + name);
                    }
                }
            }
            return output;
        }

        [[nodiscard]] std::vector<RelevanceJudgment> load_qrels(
            const std::filesystem::path& root,
            const nlohmann::json& entry,
            const std::vector<std::string>& query_ids,
            const std::vector<std::string>& document_ids
        ) {
            const auto path = resolve_plain_file(root, entry, "evaluation_qrels");
            require_output_hash(path, entry, "evaluation_qrels");
            const auto bytes = read_file_bytes(path);
            std::string line;
            std::vector<RelevanceJudgment> output;
            for(const auto byte : bytes) {
                if(byte != '\n') {
                    if(byte != '\r') {
                        line.push_back(static_cast<char>(byte));
                    }
                    continue;
                }
                std::string query_id;
                std::string document_id;
                std::string grade_text;
                const auto first_tab = line.find('\t');
                if(first_tab != std::string::npos) {
                    const auto second_tab = line.find('\t', first_tab + 1U);
                    if(second_tab == std::string::npos ||
                       line.find('\t', second_tab + 1U) != std::string::npos) {
                        throw std::runtime_error("materialized qrels row must have three tab-separated fields");
                    }
                    query_id = line.substr(0, first_tab);
                    document_id = line.substr(first_tab + 1U, second_tab - first_tab - 1U);
                    grade_text = line.substr(second_tab + 1U);
                } else {
                    std::istringstream fields(line);
                    std::string iteration;
                    if(!(fields >> query_id >> iteration >> document_id >> grade_text) ||
                       iteration != "Q0" || (fields >> iteration)) {
                        throw std::runtime_error(
                            "materialized qrels row must have query, Q0, document, and grade fields"
                        );
                    }
                }
                std::size_t parsed = 0;
                std::int32_t grade = 0;
                try {
                    grade = static_cast<std::int32_t>(std::stol(grade_text, &parsed));
                } catch(const std::exception&) {
                    throw std::runtime_error("materialized qrels grade must be an integer");
                }
                if(parsed != grade_text.size() || grade < 0) {
                    throw std::runtime_error("materialized qrels grade must be non-negative");
                }
                output.push_back({query_id, document_id, grade});
                line.clear();
            }
            if(!line.empty()) {
                throw std::runtime_error("materialized qrels file must end with newline");
            }
            if(!entry.contains("count") || !entry.at("count").is_number_unsigned() ||
               entry.at("count").get<std::uint64_t>() != output.size()) {
                throw std::runtime_error("materialized qrels count mismatch");
            }
            for(const auto& judgment : output) {
                if(!std::binary_search(query_ids.begin(), query_ids.end(), judgment.query_id) ||
                   !std::binary_search(document_ids.begin(), document_ids.end(), judgment.item_id)) {
                    throw std::runtime_error("materialized qrels is not closed over evaluation IDs");
                }
            }
            return output;
        }

    } // namespace

    AutoencoderBinaryArtifact load_autoencoder_binary_artifact(
        const std::filesystem::path& artifact_path
    ) {
        const auto artifact_bytes = read_file_bytes(artifact_path);
        nlohmann::json root;
        try {
            root = nlohmann::json::parse(artifact_bytes.begin(), artifact_bytes.end());
        } catch(const nlohmann::json::exception& error) {
            throw std::runtime_error(std::string{"cannot parse autoencoder artifact JSON: "} + error.what());
        }
        if(!root.is_object() || require_field(root, "schema_version") != 1) {
            throw std::runtime_error("autoencoder artifact schema_version must equal 1");
        }
        const auto& trainer = require_field(root, "trainer");
        const auto trainer_id = require_string(trainer, "id");
        const auto trainer_version = require_string(trainer, "version");
        if(trainer_id != "agent-memory-cpp:linear-binary-autoencoder-trainer" ||
           trainer_version != "v1") {
            throw std::runtime_error("unsupported autoencoder artifact trainer identity");
        }
        (void)require_sha256(trainer, "source_hash");
        const auto& architecture = require_field(root, "architecture");
        if(require_string(architecture, "family") != "linear_binary_autoencoder_ste" ||
           require_string(architecture, "encoder_activation") != "tanh_sign_ste_v1" ||
           require_string(architecture, "decoder") != "linear") {
            throw std::runtime_error("unsupported autoencoder artifact architecture");
        }
        const auto input_dimension = require_positive_size(architecture, "input_dimension");
        const auto bit_count = require_positive_size(architecture, "bit_count");
        const auto& training = require_field(root, "training");
        const auto seed = require_u64(training, "seed");
        const auto& shuffle_recipe = require_field(training, "shuffle_recipe");
        if(require_string(shuffle_recipe, "id") != "python_fisher_yates_sha256_seed_v1" ||
           require_field(shuffle_recipe, "per_epoch") != true) {
            throw std::runtime_error("unsupported autoencoder artifact shuffle recipe");
        }
        const auto& weights = require_field(root, "weights");
        const auto artifact_directory = artifact_path.parent_path();
        const auto encoder_weights = load_weight_file(
            artifact_directory,
            require_field(weights, "encoder_weights"),
            {bit_count, input_dimension},
            "row_major_out_by_in"
        );
        const auto encoder_bias = load_weight_file(
            artifact_directory,
            require_field(weights, "encoder_bias"),
            {bit_count},
            nullptr
        );
        const auto decoder_weights = load_weight_file(
            artifact_directory,
            require_field(weights, "decoder_weights"),
            {input_dimension, bit_count},
            "row_major_out_by_in"
        );
        const auto decoder_bias = load_weight_file(
            artifact_directory,
            require_field(weights, "decoder_bias"),
            {input_dimension},
            nullptr
        );
        const auto artifact_sha256 = sha256_hex(artifact_bytes);
        return {
            artifact_sha256,
            trainer_id,
            trainer_version,
            require_sha256(root, "input_materialization_manifest_sha256"),
            require_sha256(root, "prepared_study_manifest_sha256"),
            AutoencoderBinaryEncoder({
                input_dimension,
                bit_count,
                seed,
                artifact_sha256,
                encoder_weights,
                encoder_bias,
            }),
            AutoencoderBinaryDecoder({
                input_dimension,
                bit_count,
                decoder_weights,
                decoder_bias,
            }),
        };
    }

    MaterializedAutoencoderEvaluationDataset
    load_materialized_autoencoder_evaluation_dataset(
        const std::filesystem::path& materialization_root
    ) {
        const auto manifest_path = materialization_root / "manifest.json";
        const auto manifest_bytes = read_file_bytes(manifest_path);
        nlohmann::json manifest;
        try {
            manifest = nlohmann::json::parse(manifest_bytes.begin(), manifest_bytes.end());
        } catch(const nlohmann::json::exception& error) {
            throw std::runtime_error(
                std::string{"cannot parse materialization manifest JSON: "} + error.what()
            );
        }
        if(!manifest.is_object() || require_field(manifest, "schema_version") != 1) {
            throw std::runtime_error("materialization manifest schema_version must equal 1");
        }
        const auto& materializer = require_field(manifest, "materializer");
        if(require_string(materializer, "id") != "agent-memory-cpp:multilingual-e5-materializer" ||
           require_string(materializer, "version") != "v1") {
            throw std::runtime_error("unsupported materialization producer identity");
        }
        (void)require_sha256(materializer, "source_hash");
        const auto& vector_format = require_field(manifest, "vector_format");
        if(require_string(vector_format, "dtype") != "float32_le" ||
           require_string(vector_format, "endianness") != "little") {
            throw std::runtime_error("materialization vector format must be float32_le little-endian");
        }
        const auto dimension = require_positive_size(vector_format, "dimension");
        const auto& outputs = require_field(manifest, "outputs");
        const auto document_ids = load_record_ids(
            materialization_root,
            require_field(outputs, "evaluation_document_ids"),
            "evaluation_document_ids"
        );
        const auto document_vectors = load_embedding_rows(
            materialization_root,
            require_field(outputs, "evaluation_document_vectors"),
            document_ids.size(),
            dimension,
            "evaluation_document_vectors"
        );
        const auto query_ids = load_record_ids(
            materialization_root,
            require_field(outputs, "evaluation_query_ids"),
            "evaluation_query_ids"
        );
        const auto query_vectors = load_embedding_rows(
            materialization_root,
            require_field(outputs, "evaluation_query_vectors"),
            query_ids.size(),
            dimension,
            "evaluation_query_vectors"
        );
        auto sorted_document_ids = document_ids;
        auto sorted_query_ids = query_ids;
        std::sort(sorted_document_ids.begin(), sorted_document_ids.end());
        std::sort(sorted_query_ids.begin(), sorted_query_ids.end());
        const auto judgments = load_qrels(
            materialization_root,
            require_field(outputs, "evaluation_qrels"),
            sorted_query_ids,
            sorted_document_ids
        );
        const auto& copied_prepared_manifest = require_field(outputs, "prepared_study_manifest");
        const auto copied_prepared_manifest_path = resolve_plain_file(
            materialization_root,
            copied_prepared_manifest,
            "prepared_study_manifest"
        );
        require_output_hash(
            copied_prepared_manifest_path,
            copied_prepared_manifest,
            "prepared_study_manifest"
        );
        const auto prepared_study_manifest_sha256 = require_sha256(
            manifest,
            "prepared_study_manifest_sha256"
        );
        if(sha256_hex(read_file_bytes(copied_prepared_manifest_path)) !=
           prepared_study_manifest_sha256) {
            throw std::runtime_error("materialization copied prepared manifest hash mismatch");
        }

        MaterializedAutoencoderEvaluationDataset output;
        output.materialization_manifest_sha256 = sha256_hex(manifest_bytes);
        output.prepared_study_manifest_sha256 = prepared_study_manifest_sha256;
        output.judgments = judgments;
        output.document_embeddings.reserve(document_ids.size());
        for(std::size_t index = 0; index < document_ids.size(); ++index) {
            output.document_embeddings.push_back({document_ids[index], document_vectors[index]});
        }
        output.query_embeddings.reserve(query_ids.size());
        for(std::size_t index = 0; index < query_ids.size(); ++index) {
            output.query_embeddings.push_back({query_ids[index], query_vectors[index]});
        }
        return output;
    }

} // namespace agent_memory
