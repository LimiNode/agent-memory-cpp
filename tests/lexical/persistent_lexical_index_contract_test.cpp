#include <agent_memory/lexical/PersistentLexicalIndex.hpp>

#include <iostream>
#include <string_view>
#include <utility>

namespace {

    int fail(const std::string_view message) {
        std::cerr << message << '\n';
        return 1;
    }

    agent_memory::LexicalStatisticsEpoch make_epoch(const std::uint8_t discriminator) {
        std::array<std::uint8_t, 16> bytes{};
        bytes.back() = discriminator;
        return agent_memory::LexicalStatisticsEpoch{bytes};
    }

    agent_memory::LexicalIndexDescriptor make_descriptor() {
        return agent_memory::LexicalIndexDescriptor{
            {
                agent_memory::ScopeId{"scope:alpha"},
                agent_memory::ProjectionKind::Original,
                make_epoch(1)
            },
            "agent_memory.search_projection.original",
            1,
            "standard-tokenizer-v1",
            "lowercase-ascii-v1",
            {1, 2}
        };
    }

    agent_memory::LexicalActiveSnapshot make_snapshot() {
        return agent_memory::LexicalActiveSnapshot{
            {
                agent_memory::ScopeId{"scope:alpha"},
                agent_memory::ProjectionKind::Original,
                make_epoch(1)
            },
            7,
            1
        };
    }

    agent_memory::LexicalIndexReaderProfile make_reader_profile() {
        return agent_memory::LexicalIndexReaderProfile{
            "agent_memory.search_projection.original",
            1,
            "standard-tokenizer-v1",
            "lowercase-ascii-v1",
            {1}
        };
    }

    agent_memory::LexicalUnitRevisionBinding make_binding() {
        return agent_memory::LexicalUnitRevisionBinding{
            agent_memory::KnowledgeUnitId{11},
            agent_memory::ResourceId{"resource:alpha"},
            3,
            agent_memory::ProjectionVersionRef{4, 5, 6, "original-v1"},
            agent_memory::ResourceManifestState::Active
        };
    }

    agent_memory::LexicalReadFrontier make_frontier(
        const agent_memory::LexicalReadConsistency consistency
    ) {
        return agent_memory::LexicalReadFrontier{
            agent_memory::LexicalPublicationSequence{19},
            make_snapshot(),
            make_descriptor(),
            consistency,
            {make_binding()}
        };
    }

    class ReadFrontierTestDouble final {
    public:
        explicit ReadFrontierTestDouble(agent_memory::LexicalReadFrontier initial)
            : m_current(std::move(initial)) {}

        [[nodiscard]] agent_memory::LexicalReadFrontier open() const {
            return m_current;
        }

        void publish(agent_memory::LexicalReadFrontier replacement) {
            m_current = std::move(replacement);
        }

    private:
        agent_memory::LexicalReadFrontier m_current;
    };

    int test_descriptor_and_snapshot_admission() {
        const auto descriptor = make_descriptor();
        const auto snapshot = make_snapshot();
        const auto profile = make_reader_profile();

        if(!agent_memory::is_valid_lexical_index_descriptor(descriptor)
            || !agent_memory::is_valid_lexical_active_snapshot(snapshot)
            || agent_memory::check_lexical_index_admission(descriptor, snapshot, profile)
                != agent_memory::LexicalIndexAdmission::Compatible) {
            return fail("matching descriptor and active snapshot must be admitted");
        }

        auto wrong_epoch = snapshot;
        wrong_epoch.descriptor_ref.statistics_epoch = make_epoch(2);
        if(agent_memory::check_lexical_index_admission(descriptor, wrong_epoch, profile)
            != agent_memory::LexicalIndexAdmission::DescriptorIdentityMismatch) {
            return fail("active snapshot with another epoch must fail closed");
        }

        auto wrong_tokenizer = profile;
        wrong_tokenizer.tokenizer_fingerprint = "another-tokenizer";
        if(agent_memory::check_lexical_index_admission(descriptor, snapshot, wrong_tokenizer)
            != agent_memory::LexicalIndexAdmission::TokenizerFingerprintMismatch) {
            return fail("tokenizer fingerprint mismatch must be explicit");
        }

        auto wrong_schema = profile;
        wrong_schema.projection_schema_version = 2;
        if(agent_memory::check_lexical_index_admission(descriptor, snapshot, wrong_schema)
            != agent_memory::LexicalIndexAdmission::ProjectionSchemaMismatch) {
            return fail("projection schema mismatch must be explicit");
        }

        auto wrong_normalization = profile;
        wrong_normalization.normalization_fingerprint = "another-normalization";
        if(agent_memory::check_lexical_index_admission(descriptor, snapshot, wrong_normalization)
            != agent_memory::LexicalIndexAdmission::NormalizationFingerprintMismatch) {
            return fail("normalization fingerprint mismatch must be explicit");
        }

        auto unsupported_layout = snapshot;
        unsupported_layout.posting_layout_version = 2;
        if(agent_memory::check_lexical_index_admission(descriptor, unsupported_layout, profile)
            != agent_memory::LexicalIndexAdmission::UnsupportedPostingLayout) {
            return fail("reader must reject a posting layout it cannot decode");
        }

        auto invalid_profile = profile;
        invalid_profile.supported_posting_layout_versions.clear();
        if(agent_memory::check_lexical_index_admission(descriptor, snapshot, invalid_profile)
            != agent_memory::LexicalIndexAdmission::InvalidReaderProfile) {
            return fail("invalid reader capabilities must not be reported as descriptor corruption");
        }

        return 0;
    }

    int test_invalid_identity_values_fail_closed() {
        if(!agent_memory::LexicalStatisticsEpoch{}.empty()) {
            return fail("zero lexical epoch must be empty");
        }

        auto descriptor = make_descriptor();
        descriptor.supported_posting_layout_versions = {1, 1};
        if(agent_memory::is_valid_lexical_index_descriptor(descriptor)) {
            return fail("descriptor must reject duplicate posting layout versions");
        }

        auto frontier = make_frontier(agent_memory::LexicalReadConsistency::PinnedSnapshot);
        frontier.publication_sequence = agent_memory::LexicalPublicationSequence{};
        if(agent_memory::is_valid_lexical_read_frontier(frontier)) {
            return fail("frontier must require a collection publication sequence");
        }

        frontier = make_frontier(agent_memory::LexicalReadConsistency::PinnedSnapshot);
        frontier.observed_unit_bindings.front().manifest_state =
            agent_memory::ResourceManifestState::ErasePending;
        if(agent_memory::is_valid_lexical_read_frontier(frontier)) {
            return fail("frontier must reject a unit from a non-active manifest");
        }

        frontier = make_frontier(agent_memory::LexicalReadConsistency::PinnedSnapshot);
        frontier.observed_unit_bindings.push_back(make_binding());
        if(agent_memory::is_valid_lexical_read_frontier(frontier)) {
            return fail("frontier must reject duplicate unit revision evidence");
        }

        return 0;
    }

    int test_only_pinned_frontier_supports_exact_read() {
        const auto pinned = make_frontier(agent_memory::LexicalReadConsistency::PinnedSnapshot);
        const auto rereads = make_frontier(agent_memory::LexicalReadConsistency::NonLinearizable);
        const auto profile = make_reader_profile();

        if(!agent_memory::is_valid_lexical_read_frontier(pinned)
            || !agent_memory::is_valid_lexical_read_frontier(rereads)) {
            return fail("both explicit frontier modes must retain coherent evidence");
        }
        if(!agent_memory::supports_exact_lexical_read(pinned, profile)) {
            return fail("pinned frontier must be eligible for exact membership checks");
        }
        if(agent_memory::supports_exact_lexical_read(rereads, profile)) {
            return fail("independent authoritative rereads must not claim an exact snapshot");
        }

        auto incompatible_profile = profile;
        incompatible_profile.tokenizer_fingerprint = "another-tokenizer";
        if(agent_memory::supports_exact_lexical_read(pinned, incompatible_profile)) {
            return fail("pinned storage must not override incompatible reader identity");
        }
        return 0;
    }

    int test_open_frontier_does_not_follow_later_publication() {
        ReadFrontierTestDouble storage{
            make_frontier(agent_memory::LexicalReadConsistency::PinnedSnapshot)
        };
        const auto pinned = storage.open();

        auto replacement = make_frontier(agent_memory::LexicalReadConsistency::PinnedSnapshot);
        replacement.publication_sequence = agent_memory::LexicalPublicationSequence{20};
        replacement.active_snapshot.statistics_generation = 8;
        storage.publish(std::move(replacement));

        const auto current = storage.open();
        if(pinned.publication_sequence.value() != 19
            || pinned.active_snapshot.statistics_generation != 7
            || current.publication_sequence.value() != 20
            || current.active_snapshot.statistics_generation != 8) {
            return fail("opened frontier must remain bound to its original publication");
        }
        return 0;
    }

} // namespace

int main() {
    int failures = 0;
    failures += test_descriptor_and_snapshot_admission();
    failures += test_invalid_identity_values_fail_closed();
    failures += test_only_pinned_frontier_supports_exact_read();
    failures += test_open_frontier_does_not_follow_later_publication();
    return failures == 0 ? 0 : 1;
}
