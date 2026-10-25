#include <agent_memory/lexical/FlatLexicalIndex.hpp>

#include <array>
#include <iostream>
#include <stdexcept>
#include <string_view>
#include <utility>
#include <vector>

namespace {

    int fail(const std::string_view message) {
        std::cerr << message << '\n';
        return 1;
    }

    agent_memory::LexicalStatisticsEpoch make_epoch() {
        std::array<std::uint8_t, 16> bytes{};
        bytes.back() = 42;
        return agent_memory::LexicalStatisticsEpoch{bytes};
    }

    agent_memory::LexicalIndexDescriptor make_descriptor() {
        return agent_memory::LexicalIndexDescriptor{
            {
                agent_memory::ScopeId{"scope:flat"},
                agent_memory::ProjectionKind::Original,
                make_epoch()
            },
            "agent_memory.search_projection.original",
            1,
            "standard-tokenizer-v1",
            "lowercase-ascii-v1",
            {1}
        };
    }

    agent_memory::FlatLexicalUnitInput make_input(
        const std::uint64_t unit_id,
        const std::uint64_t unit_revision,
        std::vector<std::string> tokens
    ) {
        return agent_memory::FlatLexicalUnitInput{
            agent_memory::ScopeId{"scope:flat"},
            agent_memory::KnowledgeUnitId{unit_id},
            agent_memory::ProjectionKind::Original,
            agent_memory::ResourceId{"resource:flat"},
            3,
            unit_revision,
            agent_memory::ProjectionVersionRef{
                unit_revision,
                unit_revision,
                unit_revision,
                "original-v1"
            },
            std::move(tokens)
        };
    }

    agent_memory::FlatLexicalExpectedState expected_for(
        const agent_memory::FlatLexicalUnitInput& input
    ) {
        return agent_memory::FlatLexicalExpectedState{
            input.unit_id,
            input.resource_generation,
            input.unit_revision,
            input.projection_version
        };
    }

    bool same_flat_posting(
        const agent_memory::FlatLexicalPosting& lhs,
        const agent_memory::FlatLexicalPosting& rhs
    ) {
        return lhs.token_id == rhs.token_id
            && lhs.unit_id == rhs.unit_id
            && lhs.resource_id == rhs.resource_id
            && lhs.resource_generation == rhs.resource_generation
            && lhs.projection_version.unit_revision_at_build
                == rhs.projection_version.unit_revision_at_build
            && lhs.projection_version.projection_revision
                == rhs.projection_version.projection_revision
            && lhs.projection_version.derivation_generation
                == rhs.projection_version.derivation_generation
            && lhs.projection_version.derivation_fingerprint
                == rhs.projection_version.derivation_fingerprint
            && lhs.term_frequency == rhs.term_frequency;
    }

    int test_upsert_populates_flat_rows_and_stats() {
        agent_memory::FlatLexicalIndex index{make_descriptor()};
        index.upsert(make_input(1, 7, {"agent", "memory", "agent"}));
        index.upsert(make_input(2, 8, {"agent", "storage"}));

        if(index.size() != 2
            || index.collection_stats().unit_count != 2
            || index.collection_stats().total_token_count != 5) {
            return fail("flat index must maintain collection statistics");
        }

        const auto agent_id = index.find_token_id("agent");
        if(!agent_id) {
            return fail("flat index must allocate a token id");
        }
        const auto agent_stats = index.find_token_stats(*agent_id);
        if(!agent_stats
            || agent_stats->document_frequency != 2
            || agent_stats->collection_frequency != 3) {
            return fail("flat index must maintain token document and collection frequency");
        }

        const auto agent_postings = index.postings(*agent_id);
        if(agent_postings.size() != 2
            || agent_postings[0].unit_id != agent_memory::KnowledgeUnitId{1}
            || agent_postings[0].term_frequency != 2
            || agent_postings[1].unit_id != agent_memory::KnowledgeUnitId{2}
            || agent_postings[1].term_frequency != 1) {
            return fail("flat postings must be ordered and preserve term frequency");
        }

        const auto unit_stats = index.find_unit_stats(agent_memory::KnowledgeUnitId{1});
        if(!unit_stats
            || unit_stats->unit_revision != 7
            || unit_stats->token_count != 3
            || unit_stats->unique_token_count != 2) {
            return fail("flat unit statistics must preserve revision and token counts");
        }
        return 0;
    }

    int test_replace_updates_active_contribution_without_reusing_tokens() {
        agent_memory::FlatLexicalIndex index{make_descriptor()};
        const auto old = make_input(1, 7, {"old", "old"});
        index.upsert(old);
        const auto old_id = index.find_token_id("old");
        index.upsert(make_input(1, 8, {"new"}), expected_for(old));

        if(!old_id
            || index.find_token_stats(*old_id)
            || index.postings(*old_id).size() != 0
            || index.collection_stats().unit_count != 1
            || index.collection_stats().total_token_count != 1) {
            return fail("replacement must remove the prior active contribution");
        }

        const auto new_id = index.find_token_id("new");
        if(!new_id || new_id->value() <= old_id->value()) {
            return fail("token ids must remain monotonic and non-reused");
        }
        const auto stats = index.find_unit_stats(agent_memory::KnowledgeUnitId{1});
        if(!stats || stats->unit_revision != 8) {
            return fail("replacement must expose the new unit revision");
        }
        return 0;
    }

    int test_erase_clear_and_validation() {
        agent_memory::FlatLexicalIndex index{make_descriptor()};
        index.upsert(make_input(1, 7, {"agent"}));
        if(!index.erase(agent_memory::KnowledgeUnitId{1})
            || index.erase(agent_memory::KnowledgeUnitId{1})
            || index.size() != 0
            || index.collection_stats().unit_count != 0) {
            return fail("erase must be idempotent and update collection stats");
        }

        try {
            index.upsert(make_input(2, 9, {""}));
            return fail("empty normalized token must be rejected");
        } catch(const std::invalid_argument&) {
        }

        auto wrong_scope = make_input(2, 9, {"agent"});
        wrong_scope.scope_id = agent_memory::ScopeId{"scope:other"};
        try {
            index.upsert(std::move(wrong_scope));
            return fail("cross-scope contribution must be rejected");
        } catch(const std::invalid_argument&) {
        }

        index.upsert(make_input(2, 9, {"agent", "memory"}));
        index.clear();
        if(index.size() != 0
            || index.collection_stats().unit_count != 0
            || !index.find_token_id("agent")) {
            return fail("clear must remove active rows while retaining dictionary ids");
        }
        return 0;
    }

    int test_revision_admission_and_idempotency() {
        agent_memory::FlatLexicalIndex index{make_descriptor()};
        const auto first = make_input(1, 7, {"stable"});
        index.upsert(first);
        const auto generation = index.active_snapshot().statistics_generation;
        index.upsert(first);
        if(index.active_snapshot().statistics_generation != generation
            || index.collection_stats().total_token_count != 1) {
            return fail("identical revision replay must be an idempotent no-op");
        }

        auto conflict = first;
        conflict.normalized_tokens = {"different"};
        try {
            index.upsert(std::move(conflict));
            return fail("same revision with different content must be rejected");
        } catch(const std::runtime_error&) {
        }

        auto stale = make_input(1, 6, {"stale"});
        try {
            index.upsert(std::move(stale));
            return fail("stale revision must be rejected");
        } catch(const std::runtime_error&) {
        }

        auto fingerprint_conflict = first;
        fingerprint_conflict.projection_version.derivation_fingerprint = "zzz";
        try {
            index.upsert(std::move(fingerprint_conflict), expected_for(first));
            return fail("fingerprint change without numeric advancement must be rejected");
        } catch(const std::runtime_error&) {
        }

        auto stale_generation = make_input(1, 8, {"stale-generation"});
        stale_generation.resource_generation = 2;
        try {
            index.upsert(stale_generation, expected_for(first));
            return fail("resource generation regression must be rejected");
        } catch(const std::runtime_error&) {
        }

        auto newer = make_input(1, 8, {"newer"});
        try {
            index.upsert(newer);
            return fail("replacement without an expected current state must be rejected");
        } catch(const std::runtime_error&) {
        }
        index.upsert(newer, expected_for(first));
        return 0;
    }

    int test_failure_injection_preserves_state() {
        agent_memory::FlatLexicalIndex index{make_descriptor()};
        const auto original = make_input(1, 7, {"old", "term"});
        index.upsert(original);
        const auto generation = index.active_snapshot().statistics_generation;

        index.set_failure_injection_for_testing(
            agent_memory::FlatLexicalUpsertFailurePoint::AfterTokenPreparation
        );
        try {
            index.upsert(make_input(1, 8, {"prepared"}), expected_for(original));
            return fail("token preparation failure must be injected");
        } catch(const std::runtime_error&) {
        }
        auto old_stats = index.find_unit_stats(agent_memory::KnowledgeUnitId{1});
        if(!old_stats || old_stats->unit_revision != 7
            || index.active_snapshot().statistics_generation != generation
            || !index.find_token_id("old")) {
            return fail("failed preparation must leave the old state unchanged");
        }

        index.set_failure_injection_for_testing(
            agent_memory::FlatLexicalUpsertFailurePoint::AfterDictionaryApplication
        );
        try {
            index.upsert(
                make_input(1, 8, {"dictionary-a", "dictionary-b"}),
                expected_for(original)
            );
            return fail("dictionary application failure must be injected");
        } catch(const std::runtime_error&) {
        }
        const auto dictionary_old_id = index.find_token_id("old");
        if(index.find_token_id("dictionary-a")
            || index.active_snapshot().statistics_generation != generation
            || !index.find_unit_stats(agent_memory::KnowledgeUnitId{1})
            || !dictionary_old_id
            || !index.find_token_stats(*dictionary_old_id)
            || index.find_token_stats(*dictionary_old_id)->document_frequency != 1
            || index.postings(*dictionary_old_id).size() != 1
            || index.collection_stats().total_token_count != 2) {
            return fail("failed dictionary application must restore the old state");
        }

        index.set_failure_injection_for_testing(
            agent_memory::FlatLexicalUpsertFailurePoint::AfterPostingApplication
        );
        try {
            index.upsert(
                make_input(1, 8, {"new-a", "new-b"}),
                expected_for(original)
            );
            return fail("posting application failure must be injected");
        } catch(const std::runtime_error&) {
        }
        old_stats = index.find_unit_stats(agent_memory::KnowledgeUnitId{1});
        const auto old_id = index.find_token_id("old");
        if(!old_stats || old_stats->unit_revision != 7
            || index.active_snapshot().statistics_generation != generation
            || !old_id
            || !index.find_token_stats(*old_id)
            || index.find_token_stats(*old_id)->document_frequency != 1
            || index.postings(*old_id).size() != 1
            || index.find_token_id("new-a")
            || index.collection_stats().total_token_count != 2) {
            return fail("failed posting application must roll back the complete state");
        }

        const auto next = make_input(2, 9, {"new-a"});
        index.upsert(next);
        const auto next_id = index.find_token_id("new-a");
        if(!next_id || next_id->value() != 3) {
            return fail("rollback must restore the next token id without reuse");
        }
        return 0;
    }

    int test_shared_token_statistics_rollback() {
        agent_memory::FlatLexicalIndex index{make_descriptor()};
        const auto unit_a = make_input(1, 7, {"shared", "old"});
        const auto unit_b = make_input(2, 7, {"shared"});
        index.upsert(unit_a);
        index.upsert(unit_b);
        const auto generation = index.active_snapshot().statistics_generation;
        const auto shared_id = index.find_token_id("shared");
        if(!shared_id
            || !index.find_token_stats(*shared_id)
            || index.find_token_stats(*shared_id)->document_frequency != 2
            || index.find_token_stats(*shared_id)->collection_frequency != 2
            || index.postings(*shared_id).size() != 2) {
            return fail("shared-token fixture must start with DF/CF two");
        }

        const auto failure_points = {
            agent_memory::FlatLexicalUpsertFailurePoint::AfterDictionaryApplication,
            agent_memory::FlatLexicalUpsertFailurePoint::AfterPostingApplication
        };
        for(const auto point : failure_points) {
            index.set_failure_injection_for_testing(point);
            try {
                index.upsert(
                    make_input(1, 8, {"new", "replacement"}),
                    expected_for(unit_a)
                );
                return fail("shared-token replacement failure must be injected");
            } catch(const std::runtime_error&) {
            }

            const auto restored_shared = index.find_token_stats(*shared_id);
            const auto restored_a = index.find_unit_stats(agent_memory::KnowledgeUnitId{1});
            const auto restored_b = index.find_unit_stats(agent_memory::KnowledgeUnitId{2});
            if(!restored_shared
                || restored_shared->document_frequency != 2
                || restored_shared->collection_frequency != 2
                || index.postings(*shared_id).size() != 2
                || !restored_a || restored_a->unit_revision != 7
                || !restored_b || restored_b->unit_revision != 7
                || index.collection_stats().unit_count != 2
                || index.collection_stats().total_token_count != 3
                || index.active_snapshot().statistics_generation != generation
                || index.find_token_id("new")) {
                return fail("shared-token rollback must restore all old state");
            }
        }

        index.upsert(
            make_input(1, 8, {"new", "replacement"}),
            expected_for(unit_a)
        );
        const auto final_shared = index.find_token_stats(*shared_id);
        const auto new_id = index.find_token_id("new");
        const auto new_stats = new_id
            ? index.find_token_stats(*new_id)
            : std::nullopt;
        if(!final_shared
            || final_shared->document_frequency != 1
            || final_shared->collection_frequency != 1
            || index.postings(*shared_id).size() != 1
            || !new_id
            || !new_stats
            || new_stats->document_frequency != 1
            || index.collection_stats().unit_count != 2
            || index.collection_stats().total_token_count != 3) {
            return fail("successful replacement must update shared-token statistics");
        }
        return 0;
    }

    int test_posting_segments_are_bounded_and_generation_bound() {
        agent_memory::FlatLexicalIndex index{make_descriptor()};
        const auto unit_one = make_input(1, 7, {"shared", "shared"});
        const auto unit_two = make_input(2, 7, {"shared"});
        const auto unit_three = make_input(3, 7, {"shared"});
        const auto unit_four = make_input(4, 7, {"shared"});
        const auto unit_five = make_input(5, 7, {"shared"});
        index.upsert(unit_one);
        index.upsert(unit_two);
        index.upsert(unit_three);
        index.upsert(unit_four);
        index.upsert(unit_five);
        const auto token_id = index.find_token_id("shared");
        if(!token_id) {
            return fail("segment fixture must allocate the shared token");
        }

        const auto segments = index.posting_segments(*token_id, 2);
        if(segments.size() != 3
            || segments[0].segment_id != 0
            || segments[1].segment_id != 1
            || segments[2].segment_id != 2
            || segments[0].postings.size() != 2
            || segments[1].postings.size() != 2
            || segments[2].postings.size() != 1) {
            return fail("posting segments must use deterministic bounded partitions");
        }

        const auto original_postings = index.postings(*token_id);
        std::size_t posting_count = 0;
        std::uint64_t previous_unit_id = 0;
        std::vector<agent_memory::FlatLexicalPosting> reconstructed;
        for(const auto& segment : segments) {
            if(!agent_memory::is_valid_flat_lexical_posting_segment(segment)
                || segment.statistics_epoch != index.active_snapshot().statistics_epoch
                || segment.statistics_generation
                    != index.active_snapshot().statistics_generation
                || segment.posting_layout_version
                    != index.active_snapshot().posting_layout_version) {
                return fail("posting segments must bind to the active generation");
            }
            posting_count += segment.postings.size();
            for(const auto& posting : segment.postings) {
                if(posting.unit_id.value() <= previous_unit_id) {
                    return fail("posting segments must preserve global unit ordering");
                }
                previous_unit_id = posting.unit_id.value();
                reconstructed.push_back(posting);
            }
        }
        if(posting_count != original_postings.size()
            || reconstructed.size() != original_postings.size()
            || segments[0].postings[0].unit_id
                != agent_memory::KnowledgeUnitId{1}
            || segments[1].postings[0].unit_id
                != agent_memory::KnowledgeUnitId{3}
            || segments[2].postings[0].unit_id
                != agent_memory::KnowledgeUnitId{5}) {
            return fail("posting segments must preserve complete ordered membership");
        }
        for(std::size_t index_position = 0;
            index_position < original_postings.size();
            ++index_position) {
            const auto& expected = original_postings[index_position];
            const auto& actual = reconstructed[index_position];
            if(!same_flat_posting(actual, expected)) {
                return fail("segment reconstruction must preserve every posting field");
            }
        }

        const auto singleton_segments = index.posting_segments(*token_id, 1);
        if(singleton_segments.size() != original_postings.size()
            || singleton_segments.front().postings.size() != 1
            || singleton_segments.back().postings.size() != 1) {
            return fail("unit posting segment limit must produce singleton segments");
        }
        const auto oversized_segment = index.posting_segments(*token_id, 99);
        if(oversized_segment.size() != 1
            || oversized_segment.front().segment_id != 0
            || oversized_segment.front().postings.size() != original_postings.size()) {
            return fail("oversized posting segment limit must preserve one complete segment");
        }

        if(!index.posting_segments(agent_memory::TokenId{999}, 2).empty()) {
            return fail("unknown token segments must be empty");
        }

        try {
            static_cast<void>(index.posting_segments(*token_id, 0));
            return fail("zero posting segment limit must be rejected");
        } catch(const std::invalid_argument&) {
        }

        auto invalid = segments.front();
        invalid.postings.front().token_id = agent_memory::TokenId{};
        if(agent_memory::is_valid_flat_lexical_posting_segment(invalid)) {
            return fail("segment validator must reject a mismatched token id");
        }
        invalid = segments.front();
        invalid.postings[1].unit_id = invalid.postings.front().unit_id;
        if(agent_memory::is_valid_flat_lexical_posting_segment(invalid)) {
            return fail("segment validator must reject duplicate unit ids");
        }
        invalid = segments.front();
        invalid.postings.front().term_frequency = 0;
        if(agent_memory::is_valid_flat_lexical_posting_segment(invalid)) {
            return fail("segment validator must reject zero term frequency");
        }
        invalid = segments.front();
        invalid.postings.front().resource_id = agent_memory::ResourceId{};
        if(agent_memory::is_valid_flat_lexical_posting_segment(invalid)) {
            return fail("segment validator must reject empty resource identity");
        }
        invalid = segments.front();
        invalid.postings.front().projection_version = {};
        if(agent_memory::is_valid_flat_lexical_posting_segment(invalid)) {
            return fail("segment validator must reject invalid projection version");
        }

        const auto initial_generation = index.active_snapshot().statistics_generation;
        index.upsert(
            make_input(3, 8, {"replacement"}),
            expected_for(unit_three)
        );
        const auto replacement_token = index.find_token_id("shared");
        if(!replacement_token
            || index.active_snapshot().statistics_generation <= initial_generation) {
            return fail("revision replacement must advance the statistics generation");
        }
        const auto after_replacement = index.posting_segments(*replacement_token, 2);
        if(after_replacement.size() != 2
            || after_replacement[0].postings[0].unit_id
                != agent_memory::KnowledgeUnitId{1}
            || after_replacement[1].postings[0].unit_id
                != agent_memory::KnowledgeUnitId{4}) {
            return fail("replacement must rebuild complete active posting membership");
        }
        const auto repeated_after_replacement =
            index.posting_segments(*replacement_token, 2);
        if(repeated_after_replacement.size() != after_replacement.size()
            || repeated_after_replacement[0].postings.size()
                != after_replacement[0].postings.size()
            || repeated_after_replacement[1].postings.size()
                != after_replacement[1].postings.size()) {
            return fail("repeated segmentation must be deterministic for one snapshot");
        }
        for(std::size_t segment_index = 0;
            segment_index < after_replacement.size();
            ++segment_index) {
            for(std::size_t posting_index = 0;
                posting_index < after_replacement[segment_index].postings.size();
                ++posting_index) {
                if(!same_flat_posting(
                    repeated_after_replacement[segment_index].postings[posting_index],
                    after_replacement[segment_index].postings[posting_index]
                )) {
                    return fail("repeated segmentation must preserve every posting field");
                }
            }
        }

        const auto generation_before_erase = index.active_snapshot().statistics_generation;
        if(!index.erase(agent_memory::KnowledgeUnitId{5})) {
            return fail("erase fixture must remove one active posting");
        }
        const auto after_erase = index.posting_segments(*replacement_token, 2);
        if(index.active_snapshot().statistics_generation <= generation_before_erase
            || after_erase.size() != 2
            || after_erase[0].postings.size() != 2
            || after_erase[1].postings.size() != 1
            || after_erase[1].postings.front().unit_id
                != agent_memory::KnowledgeUnitId{4}) {
            return fail("erase must update generation and segment membership");
        }
        return 0;
    }

    agent_memory::FlatLexicalPostingPartitionExpectation make_partition_expectation(
        const agent_memory::FlatLexicalIndex& index,
        const agent_memory::TokenId& token_id,
        const std::size_t max_postings_per_segment
    ) {
        const auto postings = index.postings(token_id);
        const auto segments = index.posting_segments(
            token_id,
            max_postings_per_segment
        );
        return agent_memory::FlatLexicalPostingPartitionExpectation{
            index.descriptor().ref.statistics_epoch,
            index.active_snapshot().statistics_generation,
            index.active_snapshot().posting_layout_version,
            token_id,
            segments.size(),
            postings.size(),
            max_postings_per_segment,
            postings
        };
    }

    int test_posting_partition_reconstruction_is_fail_closed() {
        agent_memory::FlatLexicalIndex index{make_descriptor()};
        index.upsert(make_input(1, 7, {"partition", "partition"}));
        index.upsert(make_input(2, 7, {"partition"}));
        index.upsert(make_input(3, 7, {"partition"}));
        index.upsert(make_input(4, 7, {"partition"}));
        index.upsert(make_input(5, 7, {"partition"}));
        const auto token_id = index.find_token_id("partition");
        if(!token_id) {
            return fail("partition fixture must allocate a token");
        }

        const auto segments = index.posting_segments(*token_id, 2);
        const auto expectation = make_partition_expectation(index, *token_id, 2);
        const auto complete =
            agent_memory::reconstruct_flat_lexical_posting_partition(
                segments,
                expectation
            );
        if(!complete.complete()
            || complete.postings.size() != expectation.expected_posting_count) {
            return fail("complete partition must be admitted");
        }
        for(std::size_t index_position = 0;
            index_position < complete.postings.size();
            ++index_position) {
            if(!same_flat_posting(
                complete.postings[index_position],
                expectation.expected_postings[index_position]
            )) {
                return fail("complete partition must preserve posting identity");
            }
        }

        const auto assert_status = [](
            const std::vector<agent_memory::FlatLexicalPostingSegment>& candidate,
            const agent_memory::FlatLexicalPostingPartitionExpectation& expected,
            const agent_memory::FlatLexicalPostingPartitionReadStatus status
        ) {
            const auto result =
                agent_memory::reconstruct_flat_lexical_posting_partition(
                    candidate,
                    expected
                );
            return result.status == status && result.postings.empty();
        };

        auto missing_first = segments;
        missing_first.erase(missing_first.begin());
        if(!assert_status(
            missing_first,
            expectation,
            agent_memory::FlatLexicalPostingPartitionReadStatus::SegmentCountMismatch
        )) {
            return fail("missing first segment must not be admitted");
        }
        auto missing_middle = segments;
        missing_middle.erase(missing_middle.begin() + 1);
        if(!assert_status(
            missing_middle,
            expectation,
            agent_memory::FlatLexicalPostingPartitionReadStatus::SegmentCountMismatch
        )) {
            return fail("missing middle segment must not be admitted");
        }
        auto missing_last = segments;
        missing_last.pop_back();
        if(!assert_status(
            missing_last,
            expectation,
            agent_memory::FlatLexicalPostingPartitionReadStatus::SegmentCountMismatch
        )) {
            return fail("missing last segment must not be admitted");
        }

        auto duplicate_segment = segments;
        duplicate_segment[1].segment_id = duplicate_segment[0].segment_id;
        if(!assert_status(
            duplicate_segment,
            expectation,
            agent_memory::FlatLexicalPostingPartitionReadStatus::DuplicateSegmentId
        )) {
            return fail("duplicate segment id must not be admitted");
        }
        auto out_of_order_segment = segments;
        out_of_order_segment[1].segment_id = out_of_order_segment[2].segment_id;
        if(!assert_status(
            out_of_order_segment,
            expectation,
            agent_memory::FlatLexicalPostingPartitionReadStatus::OutOfOrderSegmentId
        )) {
            return fail("out-of-order segment id must not be admitted");
        }

        auto wrong_epoch = segments;
        wrong_epoch[0].statistics_epoch = agent_memory::LexicalStatisticsEpoch{};
        if(!assert_status(
            wrong_epoch,
            expectation,
            agent_memory::FlatLexicalPostingPartitionReadStatus::MetadataMismatch
        )) {
            return fail("wrong epoch must not be admitted");
        }
        auto wrong_generation = segments;
        ++wrong_generation[0].statistics_generation;
        if(!assert_status(
            wrong_generation,
            expectation,
            agent_memory::FlatLexicalPostingPartitionReadStatus::MetadataMismatch
        )) {
            return fail("wrong generation must not be admitted");
        }
        auto wrong_layout = segments;
        ++wrong_layout[0].posting_layout_version;
        if(!assert_status(
            wrong_layout,
            expectation,
            agent_memory::FlatLexicalPostingPartitionReadStatus::MetadataMismatch
        )) {
            return fail("wrong posting layout must not be admitted");
        }
        auto wrong_token = segments;
        wrong_token[0].token_id = agent_memory::TokenId{999};
        if(!assert_status(
            wrong_token,
            expectation,
            agent_memory::FlatLexicalPostingPartitionReadStatus::MetadataMismatch
        )) {
            return fail("wrong token id must not be admitted");
        }

        auto duplicate_unit = segments;
        duplicate_unit[1].postings[0].unit_id =
            duplicate_unit[0].postings.back().unit_id;
        if(!assert_status(
            duplicate_unit,
            expectation,
            agent_memory::FlatLexicalPostingPartitionReadStatus::PostingOrderMismatch
        )) {
            return fail("duplicate unit id across segments must not be admitted");
        }
        auto truncated = segments;
        truncated[1].postings.pop_back();
        if(!assert_status(
            truncated,
            expectation,
            agent_memory::FlatLexicalPostingPartitionReadStatus::PostingCountMismatch
        )) {
            return fail("truncated posting list must not be admitted");
        }

        auto membership_mismatch = expectation;
        membership_mismatch.expected_postings.back().unit_id =
            agent_memory::KnowledgeUnitId{999};
        if(!assert_status(
            segments,
            membership_mismatch,
            agent_memory::FlatLexicalPostingPartitionReadStatus::MembershipMismatch
        )) {
            return fail("membership witness mismatch must be rejected");
        }
        auto wrong_expected_count = expectation;
        wrong_expected_count.expected_posting_count = 4;
        wrong_expected_count.expected_postings.resize(4);
        if(!assert_status(
            segments,
            wrong_expected_count,
            agent_memory::FlatLexicalPostingPartitionReadStatus::InvalidExpectation
        )) {
            return fail("inconsistent expected posting count must be rejected");
        }
        auto zero_limit = expectation;
        zero_limit.max_postings_per_segment = 0;
        if(!assert_status(
            segments,
            zero_limit,
            agent_memory::FlatLexicalPostingPartitionReadStatus::InvalidExpectation
        )) {
            return fail("zero reconstruction limit must be rejected");
        }

        const auto empty_expectation =
            agent_memory::FlatLexicalPostingPartitionExpectation{
                expectation.statistics_epoch,
                expectation.statistics_generation,
                expectation.posting_layout_version,
                expectation.token_id,
                0,
                0,
                2,
                {}
            };
        const auto confirmed_empty =
            agent_memory::reconstruct_flat_lexical_posting_partition(
                {},
                empty_expectation
            );
        if(!confirmed_empty.confirmed_empty()
            || !confirmed_empty.postings.empty()) {
            return fail("empty partition must be distinguishable as confirmed empty");
        }
        if(!assert_status(
            {},
            expectation,
            agent_memory::FlatLexicalPostingPartitionReadStatus::SegmentCountMismatch
        )) {
            return fail("absent non-empty partition must remain unverified");
        }
        return 0;
    }

    int test_original_projection_only() {
        auto descriptor = make_descriptor();
        descriptor.ref.projection_kind = agent_memory::ProjectionKind::Summary;
        try {
            agent_memory::FlatLexicalIndex index{std::move(descriptor)};
            static_cast<void>(index);
            return fail("flat lexical backend must reject unsupported projections");
        } catch(const std::invalid_argument&) {
        }
        return 0;
    }

} // namespace

int main() {
    int failures = 0;
    failures += test_upsert_populates_flat_rows_and_stats();
    failures += test_replace_updates_active_contribution_without_reusing_tokens();
    failures += test_erase_clear_and_validation();
    failures += test_revision_admission_and_idempotency();
    failures += test_failure_injection_preserves_state();
    failures += test_shared_token_statistics_rollback();
    failures += test_posting_segments_are_bounded_and_generation_bound();
    failures += test_posting_partition_reconstruction_is_fail_closed();
    failures += test_original_projection_only();
    return failures == 0 ? 0 : 1;
}
