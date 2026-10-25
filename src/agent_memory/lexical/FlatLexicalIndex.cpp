#include "FlatLexicalIndex.hpp"

#include <algorithm>
#include <limits>
#include <stdexcept>
#include <utility>

namespace {

bool same_projection_version(
    const agent_memory::ProjectionVersionRef& lhs,
    const agent_memory::ProjectionVersionRef& rhs
) noexcept {
    return lhs.unit_revision_at_build == rhs.unit_revision_at_build
        && lhs.projection_revision == rhs.projection_revision
        && lhs.derivation_generation == rhs.derivation_generation
        && lhs.derivation_fingerprint == rhs.derivation_fingerprint;
}

bool same_identity(
    const agent_memory::FlatLexicalUnitStats& lhs,
    const agent_memory::FlatLexicalUnitInput& rhs
) noexcept {
    return lhs.resource_generation == rhs.resource_generation
        && lhs.unit_revision == rhs.unit_revision
        && same_projection_version(lhs.projection_version, rhs.projection_version);
}

bool is_newer_identity(
    const agent_memory::FlatLexicalUnitStats& lhs,
    const agent_memory::FlatLexicalUnitInput& rhs
) noexcept {
    if(rhs.unit_revision < lhs.unit_revision
        || rhs.resource_generation < lhs.resource_generation
        || rhs.projection_version.projection_revision
            < lhs.projection_version.projection_revision
        || rhs.projection_version.derivation_generation
            < lhs.projection_version.derivation_generation) {
        return false;
    }

    const auto numeric_advance = rhs.unit_revision > lhs.unit_revision
        || rhs.resource_generation > lhs.resource_generation
        || rhs.projection_version.projection_revision
            > lhs.projection_version.projection_revision
        || rhs.projection_version.derivation_generation
            > lhs.projection_version.derivation_generation;
    // A fingerprint is an opaque identity, not an ordering relation. If all
    // numeric freshness fields are unchanged, a different fingerprint is a
    // same-revision conflict rather than a newer contribution.
    return numeric_advance;
}

bool matches_expected(
    const agent_memory::FlatLexicalUnitStats& actual,
    const agent_memory::FlatLexicalExpectedState& expected
) noexcept {
    return actual.unit_id == expected.unit_id
        && actual.resource_generation == expected.resource_generation
        && actual.unit_revision == expected.unit_revision
        && same_projection_version(actual.projection_version, expected.projection_version);
}

bool same_flat_posting(
    const agent_memory::FlatLexicalPosting& lhs,
    const agent_memory::FlatLexicalPosting& rhs
) noexcept {
    return lhs.token_id == rhs.token_id
        && lhs.unit_id == rhs.unit_id
        && lhs.resource_id == rhs.resource_id
        && lhs.resource_generation == rhs.resource_generation
        && same_projection_version(lhs.projection_version, rhs.projection_version)
        && lhs.term_frequency == rhs.term_frequency;
}

bool is_valid_partition_posting(
    const agent_memory::FlatLexicalPosting& posting,
    const agent_memory::TokenId& token_id,
    std::uint64_t& previous_unit_id
) noexcept {
    if(posting.token_id != token_id
        || posting.unit_id.empty()
        || posting.resource_id.empty()
        || posting.resource_generation == 0
        || posting.term_frequency == 0
        || !agent_memory::is_valid_projection_version(posting.projection_version)
        || posting.unit_id.value() <= previous_unit_id) {
        return false;
    }
    previous_unit_id = posting.unit_id.value();
    return true;
}

} // namespace

namespace agent_memory {

bool is_valid_flat_lexical_unit_input(
    const FlatLexicalUnitInput& input
) noexcept {
    if(input.scope_id.empty()
        || input.unit_id.empty()
        || !is_valid_projection_kind(input.projection_kind)
        || input.resource_id.empty()
        || input.resource_generation == 0
        || input.unit_revision == 0
        || !is_valid_projection_version(input.projection_version)
        || input.projection_version.unit_revision_at_build != input.unit_revision
        || input.normalized_tokens.size() > std::numeric_limits<std::uint32_t>::max()
        || input.normalized_tokens.empty()) {
        return false;
    }

    for(const auto& token : input.normalized_tokens) {
        if(token.empty()) {
            return false;
        }
    }
    return true;
}

bool is_valid_flat_lexical_posting_segment(
    const FlatLexicalPostingSegment& segment
) noexcept {
    if(segment.statistics_epoch.empty()
        || segment.statistics_generation == 0
        || segment.posting_layout_version == 0
        || segment.token_id.empty()
        || segment.postings.empty()) {
        return false;
    }

    std::uint64_t previous_unit_id = 0;
    for(const auto& posting : segment.postings) {
        if(posting.token_id != segment.token_id
            || posting.unit_id.empty()
            || posting.resource_id.empty()
            || posting.resource_generation == 0
            || posting.term_frequency == 0
            || !is_valid_projection_version(posting.projection_version)
            || posting.unit_id.value() <= previous_unit_id) {
            return false;
        }
        previous_unit_id = posting.unit_id.value();
    }
    return true;
}

FlatLexicalPostingPartitionRead reconstruct_flat_lexical_posting_partition(
    const std::vector<FlatLexicalPostingSegment>& segments,
    const FlatLexicalPostingPartitionExpectation& expectation
) {
    const auto invalid_expectation = FlatLexicalPostingPartitionRead{
        FlatLexicalPostingPartitionReadStatus::InvalidExpectation,
        {}
    };
    if(expectation.statistics_epoch.empty()
        || expectation.statistics_generation == 0
        || expectation.posting_layout_version == 0
        || expectation.token_id.empty()
        || expectation.max_postings_per_segment == 0
        || expectation.expected_postings.size()
            != expectation.expected_posting_count
        || expectation.expected_segment_count
            > std::numeric_limits<std::uint32_t>::max()) {
        return invalid_expectation;
    }

    if(expectation.expected_posting_count == 0) {
        if(expectation.expected_segment_count != 0
            || !expectation.expected_postings.empty()) {
            return invalid_expectation;
        }
        return segments.empty()
            ? FlatLexicalPostingPartitionRead{
                FlatLexicalPostingPartitionReadStatus::ConfirmedEmpty,
                {}
            }
            : FlatLexicalPostingPartitionRead{
                FlatLexicalPostingPartitionReadStatus::SegmentCountMismatch,
                {}
            };
    }

    const auto expected_segment_count =
        expectation.expected_posting_count / expectation.max_postings_per_segment
        + (expectation.expected_posting_count
                % expectation.max_postings_per_segment == 0 ? 0U : 1U);
    if(expected_segment_count != expectation.expected_segment_count) {
        return invalid_expectation;
    }

    std::uint64_t previous_expected_unit_id = 0;
    for(const auto& expected_posting : expectation.expected_postings) {
        if(!is_valid_partition_posting(
            expected_posting,
            expectation.token_id,
            previous_expected_unit_id
        )) {
            return invalid_expectation;
        }
    }

    if(segments.size() != expectation.expected_segment_count) {
        return {
            FlatLexicalPostingPartitionReadStatus::SegmentCountMismatch,
            {}
        };
    }

    std::size_t received_posting_count = 0;
    for(std::size_t segment_index = 0;
        segment_index < segments.size();
        ++segment_index) {
        const auto& segment = segments[segment_index];
        if(segment.segment_id < segment_index) {
            return {
                FlatLexicalPostingPartitionReadStatus::DuplicateSegmentId,
                {}
            };
        }
        if(segment.segment_id > segment_index) {
            return {
                FlatLexicalPostingPartitionReadStatus::OutOfOrderSegmentId,
                {}
            };
        }
        if(segment.statistics_epoch != expectation.statistics_epoch
            || segment.statistics_generation != expectation.statistics_generation
            || segment.posting_layout_version
                != expectation.posting_layout_version
            || segment.token_id != expectation.token_id) {
            return {
                FlatLexicalPostingPartitionReadStatus::MetadataMismatch,
                {}
            };
        }
        if(segment.postings.size() > expectation.max_postings_per_segment) {
            return {
                FlatLexicalPostingPartitionReadStatus::SegmentLimitExceeded,
                {}
            };
        }
        if(!is_valid_flat_lexical_posting_segment(segment)) {
            return {
                FlatLexicalPostingPartitionReadStatus::InvalidSegment,
                {}
            };
        }
        if(received_posting_count > std::numeric_limits<std::size_t>::max()
                - segment.postings.size()) {
            return {
                FlatLexicalPostingPartitionReadStatus::PostingCountMismatch,
                {}
            };
        }
        received_posting_count += segment.postings.size();
    }

    if(received_posting_count != expectation.expected_posting_count) {
        return {
            FlatLexicalPostingPartitionReadStatus::PostingCountMismatch,
            {}
        };
    }

    // Layout version 1 uses deterministic contiguous packing: every segment
    // except the last is full, and the last contains the remaining postings
    // (one through the configured limit). Membership and aggregate counts
    // alone are not sufficient to establish that a segment set was produced
    // by the trusted writer; accepting arbitrary bounded repartitioning would
    // make the physical layout ambiguous.
    for(std::size_t segment_index = 0;
        segment_index < segments.size();
        ++segment_index) {
        const auto expected_segment_size =
            segment_index + 1 < segments.size()
                ? expectation.max_postings_per_segment
                : (expectation.expected_posting_count
                        % expectation.max_postings_per_segment == 0
                    ? expectation.max_postings_per_segment
                    : expectation.expected_posting_count
                        % expectation.max_postings_per_segment);
        if(segments[segment_index].postings.size() != expected_segment_size) {
            return {
                FlatLexicalPostingPartitionReadStatus::SegmentLayoutMismatch,
                {}
            };
        }
    }

    FlatLexicalPostingPartitionRead result{
        FlatLexicalPostingPartitionReadStatus::Complete,
        {}
    };
    result.postings.reserve(received_posting_count);
    std::uint64_t previous_unit_id = 0;
    std::size_t posting_index = 0;
    for(const auto& segment : segments) {
        for(const auto& posting : segment.postings) {
            if(posting.unit_id.value() <= previous_unit_id) {
                return {
                    FlatLexicalPostingPartitionReadStatus::PostingOrderMismatch,
                    {}
                };
            }
            if(!same_flat_posting(
                posting,
                expectation.expected_postings[posting_index]
            )) {
                return {
                    FlatLexicalPostingPartitionReadStatus::MembershipMismatch,
                    {}
                };
            }
            previous_unit_id = posting.unit_id.value();
            result.postings.push_back(posting);
            ++posting_index;
        }
    }
    return result;
}

FlatLexicalIndex::FlatLexicalIndex(
    LexicalIndexDescriptor descriptor,
    const std::uint32_t posting_layout_version
) : m_descriptor(std::move(descriptor)) {
    if(!is_valid_lexical_index_descriptor(m_descriptor)) {
        throw std::invalid_argument("invalid lexical index descriptor");
    }
    if(m_descriptor.ref.projection_kind != ProjectionKind::Original) {
        throw std::invalid_argument(
            "flat lexical index supports only the Original projection"
        );
    }

    if(posting_layout_version == 0) {
        m_active_snapshot.posting_layout_version =
            m_descriptor.supported_posting_layout_versions.front();
    } else {
        m_active_snapshot.posting_layout_version = posting_layout_version;
    }

    if(!supports_exact_lexical_read(
        LexicalReadFrontier{
            LexicalPublicationSequence{1},
            LexicalActiveSnapshot{
                m_descriptor.ref.statistics_epoch,
                1,
                m_active_snapshot.posting_layout_version
            },
            m_descriptor,
            LexicalReadConsistency::PinnedSnapshot,
            {}
        },
        LexicalIndexReaderProfile{
            m_descriptor.projection_schema_id,
            m_descriptor.projection_schema_version,
            m_descriptor.tokenizer_fingerprint,
            m_descriptor.normalization_fingerprint,
            m_descriptor.supported_posting_layout_versions
        }
    )) {
        throw std::invalid_argument("unsupported lexical posting layout");
    }
    m_active_snapshot.statistics_epoch = m_descriptor.ref.statistics_epoch;
    m_active_snapshot.statistics_generation = 1;
}

const LexicalIndexDescriptor& FlatLexicalIndex::descriptor() const noexcept {
    return m_descriptor;
}

const LexicalActiveSnapshot& FlatLexicalIndex::active_snapshot() const noexcept {
    return m_active_snapshot;
}

std::size_t FlatLexicalIndex::size() const noexcept {
    return m_units.size();
}

void FlatLexicalIndex::restore_active_snapshot_for_storage(
    LexicalActiveSnapshot snapshot
) {
    if(!is_valid_lexical_active_snapshot(snapshot)
        || snapshot.statistics_epoch != m_descriptor.ref.statistics_epoch
        || std::find(
            m_descriptor.supported_posting_layout_versions.begin(),
            m_descriptor.supported_posting_layout_versions.end(),
            snapshot.posting_layout_version
        ) == m_descriptor.supported_posting_layout_versions.end()) {
        throw std::invalid_argument("incompatible durable lexical snapshot");
    }
    m_active_snapshot = snapshot;
}

void FlatLexicalIndex::restore_token_dictionary_for_storage(
    std::vector<std::pair<std::string, TokenId>> entries,
    const TokenId next_token_id
) {
    if(!m_token_by_text.empty() || !m_text_by_token.empty()
        || next_token_id.empty()) {
        throw std::invalid_argument("invalid durable lexical token dictionary restore");
    }

    std::uint64_t maximum = 0;
    for(const auto& entry : entries) {
        if(entry.first.empty() || entry.second.empty()
            || m_token_by_text.find(entry.first) != m_token_by_text.end()
            || m_text_by_token.find(entry.second) != m_text_by_token.end()
            || entry.second.value() >= next_token_id.value()) {
            throw std::invalid_argument("invalid durable lexical token dictionary entry");
        }
        maximum = std::max(maximum, entry.second.value());
        m_token_by_text.emplace(entry.first, entry.second);
        m_text_by_token.emplace(entry.second, entry.first);
    }
    if(maximum >= next_token_id.value()) {
        throw std::invalid_argument("durable lexical token allocator frontier is behind dictionary");
    }
    m_next_token_id = next_token_id.value();
}

std::vector<std::pair<std::string, TokenId>>
FlatLexicalIndex::token_dictionary_for_storage() const {
    std::vector<std::pair<std::string, TokenId>> result;
    result.reserve(m_token_by_text.size());
    for(const auto& entry : m_token_by_text) {
        result.emplace_back(entry.first, entry.second);
    }
    return result;
}

TokenId FlatLexicalIndex::next_token_id_for_storage() const noexcept {
    return TokenId{m_next_token_id};
}

void FlatLexicalIndex::remove_unit(const KnowledgeUnitId& unit_id) {
    const auto unit = m_units.find(unit_id);
    if(unit == m_units.end()) {
        return;
    }

    for(const auto& term : unit->second.term_frequencies) {
        const auto posting_bucket = m_postings.find(term.first);
        if(posting_bucket != m_postings.end()) {
            posting_bucket->second.erase(unit_id);
            if(posting_bucket->second.empty()) {
                m_postings.erase(posting_bucket);
            }
        }

        const auto stats = m_token_stats.find(term.first);
        if(stats != m_token_stats.end()) {
            --stats->second.document_frequency;
            stats->second.collection_frequency -= term.second;
            if(stats->second.document_frequency == 0
                && stats->second.collection_frequency == 0) {
                m_token_stats.erase(stats);
            }
        }
    }

    --m_collection_stats.unit_count;
    m_collection_stats.total_token_count -= unit->second.stats.token_count;
    m_units.erase(unit);
}

void FlatLexicalIndex::upsert(
    FlatLexicalUnitInput input,
    const std::optional<FlatLexicalExpectedState> expected_current
) {
    if(!is_valid_flat_lexical_unit_input(input)
        || input.scope_id != m_descriptor.ref.scope_id
        || input.projection_kind != m_descriptor.ref.projection_kind) {
        throw std::invalid_argument("invalid flat lexical unit input");
    }

    const auto existing = m_units.find(input.unit_id);
    if(expected_current.has_value() && expected_current->unit_id != input.unit_id) {
        throw std::invalid_argument("expected lexical unit identity mismatch");
    }
    if(existing != m_units.end()) {
        if(expected_current.has_value()
            && !matches_expected(existing->second.stats, *expected_current)) {
            throw std::runtime_error("flat lexical replacement lost its expected current state");
        }

        if(same_identity(existing->second.stats, input)) {
            if(existing->second.normalized_tokens == input.normalized_tokens
                && existing->second.stats.resource_id == input.resource_id) {
                return;
            }
            throw std::runtime_error("conflicting flat lexical content for the same revision");
        }
        if(!is_newer_identity(existing->second.stats, input)) {
            throw std::runtime_error("stale flat lexical unit revision");
        }
        if(!expected_current.has_value()) {
            throw std::runtime_error(
                "flat lexical replacement requires an expected current state"
            );
        }
    } else if(expected_current.has_value()) {
        throw std::runtime_error("expected flat lexical unit is not present");
    }

    if(m_active_snapshot.statistics_generation == std::numeric_limits<std::uint64_t>::max()) {
        throw std::overflow_error("lexical statistics generation exhausted");
    }

    // Prepare every allocation before touching the active state. New token ids
    // are only prospective until the commit below succeeds.
    std::map<std::string, std::uint32_t> frequencies_by_text;
    for(const auto& token : input.normalized_tokens) {
        ++frequencies_by_text[token];
    }

    std::map<std::string, TokenId> token_ids_by_text;
    std::map<std::string, TokenId> new_token_ids;
    std::map<TokenId, std::string> new_text_by_token;
    auto next_token_id = m_next_token_id;
    for(const auto& item : frequencies_by_text) {
        const auto found = m_token_by_text.find(item.first);
        if(found != m_token_by_text.end()) {
            token_ids_by_text.emplace(item.first, found->second);
            continue;
        }
        if(next_token_id == std::numeric_limits<std::uint64_t>::max()) {
            throw std::overflow_error("lexical token id space exhausted");
        }
        const TokenId token_id{next_token_id++};
        token_ids_by_text.emplace(item.first, token_id);
        new_token_ids.emplace(item.first, token_id);
        new_text_by_token.emplace(token_id, item.first);
    }

    std::map<TokenId, std::uint32_t> term_frequencies;
    for(const auto& item : frequencies_by_text) {
        term_frequencies.emplace(token_ids_by_text.at(item.first), item.second);
    }

    UnitState state{
        FlatLexicalUnitStats{
            input.unit_id,
            input.resource_id,
            input.resource_generation,
            input.unit_revision,
            input.projection_version,
            static_cast<std::uint32_t>(input.normalized_tokens.size()),
            static_cast<std::uint32_t>(term_frequencies.size())
        },
        term_frequencies,
        input.normalized_tokens
    };

    std::optional<UnitState> old_state;
    if(existing != m_units.end()) {
        old_state = existing->second;
    }

    std::map<TokenId, std::map<KnowledgeUnitId, FlatLexicalPosting>> staged_postings;
    for(const auto& term : term_frequencies) {
        staged_postings[term.first].emplace(
            input.unit_id,
            FlatLexicalPosting{
                term.first,
                input.unit_id,
                input.resource_id,
                input.resource_generation,
                input.projection_version,
                term.second
            }
        );
    }

    std::map<TokenId, FlatLexicalTokenStats> staged_token_stats;
    for(const auto& term : term_frequencies) {
        const auto current_stats = m_token_stats.find(term.first);
        const auto old_term = !old_state.has_value()
            ? false
            : old_state->term_frequencies.find(term.first)
                != old_state->term_frequencies.end();
        const auto will_be_removed = old_term
            && current_stats != m_token_stats.end()
            && current_stats->second.document_frequency == 1;
        if(current_stats == m_token_stats.end() || will_be_removed) {
            staged_token_stats.emplace(
                term.first,
                FlatLexicalTokenStats{term.first, 1, term.second}
            );
        }
    }
    std::map<KnowledgeUnitId, UnitState> staged_unit;
    staged_unit.emplace(input.unit_id, state);

    if(m_failure_injection == FlatLexicalUpsertFailurePoint::AfterTokenPreparation) {
        m_failure_injection = FlatLexicalUpsertFailurePoint::None;
        throw std::runtime_error("injected failure after token preparation");
    }

    const auto old_collection = m_collection_stats;
    const auto old_generation = m_active_snapshot.statistics_generation;
    const auto old_next_token_id = m_next_token_id;

    using PostingBucket = std::map<KnowledgeUnitId, FlatLexicalPosting>;
    using PostingNode = PostingBucket::node_type;
    using PostingBucketNode = std::map<TokenId, PostingBucket>::node_type;
    using UnitNode = std::map<KnowledgeUnitId, UnitState>::node_type;
    using StatsNode = std::map<TokenId, FlatLexicalTokenStats>::node_type;
    struct OldPostingJournal final {
        TokenId token_id;
        PostingNode posting;
        PostingBucketNode bucket;
        bool present = false;
        bool bucket_removed = false;
    };
    struct OldStatsJournal final {
        TokenId token_id;
        StatsNode node;
        FlatLexicalTokenStats value;
        bool present = false;
        bool extracted = false;
    };

    // All journal containers and string copies are prepared before the first
    // active-state mutation. The commit and rollback phases only transfer or
    // erase existing nodes; neither phase is allowed to allocate.
    std::vector<std::string> inserted_dictionary_keys(new_token_ids.size());
    {
        std::size_t index = 0;
        for(const auto& item : new_token_ids) {
            inserted_dictionary_keys[index++] = item.first;
        }
    }
    std::vector<TokenId> inserted_posting_tokens(staged_postings.size());
    {
        std::size_t index = 0;
        for(const auto& item : staged_postings) {
            inserted_posting_tokens[index++] = item.first;
        }
    }
    std::vector<TokenId> inserted_stat_tokens(staged_token_stats.size());
    {
        std::size_t index = 0;
        for(const auto& item : staged_token_stats) {
            inserted_stat_tokens[index++] = item.first;
        }
    }
    std::vector<OldPostingJournal> old_postings(
        old_state ? old_state->term_frequencies.size() : 0
    );
    std::vector<OldStatsJournal> old_stats(
        old_state ? old_state->term_frequencies.size() : 0
    );
    std::vector<OldStatsJournal> changed_stats(term_frequencies.size());
    {
        std::size_t index = 0;
        for(const auto& term : term_frequencies) {
            changed_stats[index].token_id = term.first;
            const auto stats = m_token_stats.find(term.first);
            if(stats != m_token_stats.end()) {
                changed_stats[index].present = true;
                changed_stats[index].value = stats->second;
            }
            ++index;
        }
    }

    std::optional<UnitNode> old_unit_node;
    try {
        if(existing != m_units.end()) {
            old_unit_node.emplace(m_units.extract(input.unit_id));

            std::size_t index = 0;
            for(const auto& term : old_state->term_frequencies) {
                old_postings[index].token_id = term.first;
                auto bucket = m_postings.find(term.first);
                if(bucket != m_postings.end()) {
                    old_postings[index].posting = bucket->second.extract(input.unit_id);
                    old_postings[index].present = !old_postings[index].posting.empty();
                    if(bucket->second.empty()) {
                        old_postings[index].bucket = m_postings.extract(bucket);
                        old_postings[index].bucket_removed = true;
                    }
                }

                old_stats[index].token_id = term.first;
                const auto stats = m_token_stats.find(term.first);
                if(stats != m_token_stats.end()) {
                    old_stats[index].present = true;
                    old_stats[index].value = stats->second;
                    if(stats->second.document_frequency == 1) {
                        old_stats[index].node = m_token_stats.extract(stats);
                        old_stats[index].extracted = true;
                    } else {
                        --stats->second.document_frequency;
                        stats->second.collection_frequency -= term.second;
                    }
                }
                ++index;
            }
            m_collection_stats.unit_count--;
            m_collection_stats.total_token_count -= old_state->stats.token_count;
        }

        for(auto it = new_token_ids.begin(); it != new_token_ids.end();) {
            auto node = new_token_ids.extract(it++);
            m_token_by_text.insert(std::move(node));
        }
        for(auto it = new_text_by_token.begin(); it != new_text_by_token.end();) {
            auto node = new_text_by_token.extract(it++);
            m_text_by_token.insert(std::move(node));
        }
        if(m_failure_injection == FlatLexicalUpsertFailurePoint::AfterDictionaryApplication) {
            m_failure_injection = FlatLexicalUpsertFailurePoint::None;
            throw std::runtime_error("injected failure after dictionary application");
        }

        std::size_t applied_postings = 0;
        for(auto it = staged_postings.begin(); it != staged_postings.end();) {
            const auto token_id = it->first;
            auto staged_bucket = staged_postings.extract(it++);
            auto bucket = m_postings.find(token_id);
            if(bucket == m_postings.end()) {
                m_postings.insert(std::move(staged_bucket));
            } else {
                auto posting = staged_bucket.mapped().extract(input.unit_id);
                bucket->second.insert(std::move(posting));
            }
            ++applied_postings;
            if(applied_postings == 1
                && m_failure_injection == FlatLexicalUpsertFailurePoint::AfterPostingApplication) {
                m_failure_injection = FlatLexicalUpsertFailurePoint::None;
                throw std::runtime_error("injected failure after posting application");
            }
        }

        for(const auto& term : term_frequencies) {
            auto stats = m_token_stats.find(term.first);
            if(stats == m_token_stats.end()) {
                auto node = staged_token_stats.extract(term.first);
                m_token_stats.insert(std::move(node));
            } else {
                ++stats->second.document_frequency;
                stats->second.collection_frequency += term.second;
            }
        }

        m_collection_stats.unit_count = old_collection.unit_count
            + (old_state ? 0U : 1U);
        m_collection_stats.total_token_count = old_collection.total_token_count
            - (old_state ? old_state->stats.token_count : 0U)
            + state.stats.token_count;
        auto unit_node = staged_unit.extract(input.unit_id);
        m_units.insert(std::move(unit_node));
        m_next_token_id = next_token_id;
        ++m_active_snapshot.statistics_generation;
    } catch(...) {
        // Restore only by node transfer, assignment, or erase. No allocation is
        // permitted while undoing a partially applied publication.
        m_units.erase(input.unit_id);
        for(const auto token_id : inserted_posting_tokens) {
            auto bucket = m_postings.find(token_id);
            if(bucket != m_postings.end()) {
                bucket->second.erase(input.unit_id);
                if(bucket->second.empty()) {
                    m_postings.erase(bucket);
                }
            }
        }
        for(const auto token_id : inserted_stat_tokens) {
            m_token_stats.erase(token_id);
        }
        for(const auto& changed : changed_stats) {
            if(changed.present) {
                const auto stats = m_token_stats.find(changed.token_id);
                if(stats != m_token_stats.end()) {
                    stats->second = changed.value;
                }
            }
        }
        for(const auto& key : inserted_dictionary_keys) {
            const auto id = m_token_by_text.find(key);
            if(id != m_token_by_text.end()) {
                m_text_by_token.erase(id->second);
                m_token_by_text.erase(id);
            }
        }
        for(auto& old : old_stats) {
            if(old.extracted) {
                m_token_stats.insert(std::move(old.node));
            } else if(old.present) {
                const auto stats = m_token_stats.find(old.token_id);
                if(stats != m_token_stats.end()) {
                    stats->second = old.value;
                }
            }
        }
        if(old_state) {
            for(auto& old : old_postings) {
                if(!old.present) {
                    continue;
                }
                if(old.bucket_removed) {
                    m_postings.insert(std::move(old.bucket));
                }
                const auto bucket = m_postings.find(old.token_id);
                if(bucket != m_postings.end()) {
                    bucket->second.insert(std::move(old.posting));
                }
            }
            m_units.insert(std::move(*old_unit_node));
        }
        m_next_token_id = old_next_token_id;
        m_active_snapshot.statistics_generation = old_generation;
        m_collection_stats = old_collection;
        throw;
    }
}

void FlatLexicalIndex::set_failure_injection_for_testing(
    const FlatLexicalUpsertFailurePoint point
) noexcept {
    m_failure_injection = point;
}

bool FlatLexicalIndex::erase(const KnowledgeUnitId& unit_id) {
    if(m_units.find(unit_id) == m_units.end()) {
        return false;
    }
    if(m_active_snapshot.statistics_generation == std::numeric_limits<std::uint64_t>::max()) {
        throw std::overflow_error("lexical statistics generation exhausted");
    }
    remove_unit(unit_id);
    advance_generation();
    return true;
}

void FlatLexicalIndex::clear() {
    if(m_units.empty()) {
        return;
    }
    if(m_active_snapshot.statistics_generation == std::numeric_limits<std::uint64_t>::max()) {
        throw std::overflow_error("lexical statistics generation exhausted");
    }
    m_units.clear();
    m_postings.clear();
    m_token_stats.clear();
    m_collection_stats = {};
    advance_generation();
}

void FlatLexicalIndex::advance_generation() {
    if(m_active_snapshot.statistics_generation == std::numeric_limits<std::uint64_t>::max()) {
        throw std::overflow_error("lexical statistics generation exhausted");
    }
    ++m_active_snapshot.statistics_generation;
}

std::optional<FlatLexicalUnitStats> FlatLexicalIndex::find_unit_stats(
    const KnowledgeUnitId& unit_id
) const {
    const auto found = m_units.find(unit_id);
    if(found == m_units.end()) {
        return std::nullopt;
    }
    return found->second.stats;
}

std::vector<FlatLexicalUnitStats> FlatLexicalIndex::active_unit_stats() const {
    std::vector<FlatLexicalUnitStats> result;
    result.reserve(m_units.size());
    for(const auto& entry : m_units) {
        result.push_back(entry.second.stats);
    }
    return result;
}

std::optional<TokenId> FlatLexicalIndex::find_token_id(
    const std::string& normalized_token
) const {
    const auto found = m_token_by_text.find(normalized_token);
    if(found == m_token_by_text.end()) {
        return std::nullopt;
    }
    return found->second;
}

std::optional<FlatLexicalTokenStats> FlatLexicalIndex::find_token_stats(
    const TokenId& token_id
) const {
    const auto found = m_token_stats.find(token_id);
    if(found == m_token_stats.end()) {
        return std::nullopt;
    }
    return found->second;
}

std::vector<FlatLexicalPosting> FlatLexicalIndex::postings(
    const TokenId& token_id
) const {
    std::vector<FlatLexicalPosting> result;
    const auto found = m_postings.find(token_id);
    if(found == m_postings.end()) {
        return result;
    }

    result.reserve(found->second.size());
    for(const auto& entry : found->second) {
        result.push_back(entry.second);
    }
    return result;
}

std::vector<FlatLexicalPostingSegment> FlatLexicalIndex::posting_segments(
    const TokenId& token_id,
    const std::size_t max_postings_per_segment
) const {
    if(max_postings_per_segment == 0) {
        throw std::invalid_argument(
            "flat lexical posting segment limit must be positive"
        );
    }

    const auto posting_bucket = m_postings.find(token_id);
    if(posting_bucket == m_postings.end()) {
        return {};
    }

    const auto posting_count = posting_bucket->second.size();
    const auto segment_count = posting_count / max_postings_per_segment
        + (posting_count % max_postings_per_segment == 0 ? 0U : 1U);
    if(segment_count > std::numeric_limits<std::uint32_t>::max()) {
        throw std::overflow_error(
            "flat lexical posting segment id space is exhausted"
        );
    }

    std::vector<FlatLexicalPostingSegment> result;
    result.reserve(segment_count);
    auto posting = posting_bucket->second.begin();
    std::uint32_t segment_id = 0;
    while(posting != posting_bucket->second.end()) {
        FlatLexicalPostingSegment segment;
        segment.statistics_epoch = m_active_snapshot.statistics_epoch;
        segment.statistics_generation = m_active_snapshot.statistics_generation;
        segment.posting_layout_version = m_active_snapshot.posting_layout_version;
        segment.token_id = token_id;
        segment.segment_id = segment_id++;
        segment.postings.reserve(std::min(
            max_postings_per_segment,
            posting_count
        ));

        for(std::size_t count = 0;
            count < max_postings_per_segment
                && posting != posting_bucket->second.end();
            ++count, ++posting) {
            segment.postings.push_back(posting->second);
        }
        result.push_back(std::move(segment));
    }
    return result;
}

FlatLexicalCollectionStats FlatLexicalIndex::collection_stats() const noexcept {
    return m_collection_stats;
}

} // namespace agent_memory
