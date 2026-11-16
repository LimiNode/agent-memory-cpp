#include <agent_memory.hpp>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <iostream>
#include <limits>
#include <memory>
#include <optional>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace {

    int fail(std::string_view message) {
        std::cerr << message << '\n';
        return 1;
    }

    agent_memory::Metadata metadata(std::string scope) {
        agent_memory::Metadata result;
        result.set("scope", std::move(scope));
        result.set("owner", "fixture");
        return result;
    }

    agent_memory::LexicalSearchResult hit(
        std::string chunk,
        std::string scope,
        std::string resource,
        agent_memory::SectionId section,
        std::uint8_t tier,
        std::uint32_t enrichment,
        float score
    ) {
        agent_memory::LexicalSearchResult result;
        result.chunk_id = agent_memory::ChunkId{std::move(chunk)};
        result.score = score;
        result.metadata = metadata(std::move(scope));
        result.resource_id = agent_memory::ResourceId{std::move(resource)};
        result.section_id = section;
        result.result_tier = tier;
        result.enrichment_level = enrichment;
        return result;
    }

    class FixtureIndex final : public agent_memory::ILexicalIndex {
    public:
        explicit FixtureIndex(std::vector<agent_memory::LexicalSearchResult> hits)
            : m_hits(std::move(hits)) {}

        [[nodiscard]] std::size_t size() const noexcept override {
            return m_hits.size();
        }

        void upsert(agent_memory::LexicalDocumentRecord) override {}

        [[nodiscard]] std::optional<agent_memory::LexicalDocumentStats> find_stats(
            const agent_memory::ChunkId&
        ) const override {
            return std::nullopt;
        }

        [[nodiscard]] std::vector<agent_memory::LexicalSearchResult> search(
            const agent_memory::LexicalSearchQuery& query
        ) const override {
            last_query = query;
            return m_hits;
        }

        [[nodiscard]] bool erase(const agent_memory::ChunkId&) override {
            return false;
        }

        [[nodiscard]] std::size_t erase_resource(
            const agent_memory::ResourceId&
        ) override {
            return 0;
        }

        void clear() override {}

        mutable std::optional<agent_memory::LexicalSearchQuery> last_query;

    private:
        std::vector<agent_memory::LexicalSearchResult> m_hits;
    };

    enum class Mutation {
        ReorderAndScore,
        ForeignChunk,
        Metadata,
        Resource,
        Section,
        Tier,
        Enrichment,
        Duplicate,
        TooMany,
        NonFinite,
        PositiveInfinity,
        NegativeInfinity,
        Subset,
        Empty
    };

    class FakeReranker final : public agent_memory::IReranker {
    public:
        explicit FakeReranker(const Mutation mutation)
            : m_mutation(mutation) {}

        [[nodiscard]] std::vector<agent_memory::LexicalSearchResult> rerank(
            const std::string&,
            std::vector<agent_memory::LexicalSearchResult> candidates
        ) const override {
            switch(m_mutation) {
                case Mutation::ReorderAndScore:
                    std::reverse(candidates.begin(), candidates.end());
                    for(auto& candidate : candidates) {
                        candidate.score += 10.0F;
                    }
                    break;
                case Mutation::ForeignChunk:
                    candidates.front().chunk_id = agent_memory::ChunkId{"chunk:foreign"};
                    break;
                case Mutation::Metadata:
                    candidates.front().metadata.set("scope", "other-scope");
                    break;
                case Mutation::Resource:
                    candidates.front().resource_id = agent_memory::ResourceId{"resource:foreign"};
                    break;
                case Mutation::Section:
                    candidates.front().section_id = 999;
                    break;
                case Mutation::Tier:
                    candidates.front().result_tier = 2;
                    break;
                case Mutation::Enrichment:
                    candidates.front().enrichment_level = 99;
                    break;
                case Mutation::Duplicate:
                    candidates[1].chunk_id = candidates[0].chunk_id;
                    candidates[1].resource_id = candidates[0].resource_id;
                    candidates[1].section_id = candidates[0].section_id;
                    candidates[1].result_tier = candidates[0].result_tier;
                    break;
                case Mutation::TooMany:
                    candidates.push_back(candidates.back());
                    break;
                case Mutation::NonFinite:
                    candidates.front().score = std::numeric_limits<float>::quiet_NaN();
                    break;
                case Mutation::PositiveInfinity:
                    candidates.front().score = std::numeric_limits<float>::infinity();
                    break;
                case Mutation::NegativeInfinity:
                    candidates.front().score = -std::numeric_limits<float>::infinity();
                    break;
                case Mutation::Subset:
                    candidates.resize(1);
                    break;
                case Mutation::Empty:
                    candidates.clear();
                    break;
            }
            return candidates;
        }

    private:
        Mutation m_mutation;
    };

    agent_memory::RetrievalResponse run(
        FixtureIndex& index,
        const Mutation mutation,
        std::size_t limit = 2
    ) {
        agent_memory::HybridRetrievalEngine engine(index);
        engine.set_reranker(std::make_unique<FakeReranker>(mutation));
        agent_memory::RetrievalRequest request;
        request.query = "memory";
        request.limit = limit;
        request.metadata_filters.push_back({"scope", "documents"});
        return engine.retrieve(request);
    }

    int benchmark_admission() {
        using Clock = std::chrono::steady_clock;
        constexpr std::size_t kRepeats = 12;
        std::cout << "candidate_count,mean_ms,min_ms,max_ms\n";
        for(const auto candidate_count : {10U, 50U, 100U, 500U, 1000U}) {
            std::vector<agent_memory::LexicalSearchResult> hits;
            hits.reserve(candidate_count);
            for(std::size_t index = 0; index < candidate_count; ++index) {
                hits.push_back(hit(
                    "chunk:" + std::to_string(index),
                    "documents",
                    "resource:" + std::to_string(index),
                    static_cast<agent_memory::SectionId>(index),
                    static_cast<std::uint8_t>(index % 2U),
                    2,
                    static_cast<float>(index)
                ));
            }
            FixtureIndex index(std::move(hits));
            (void)run(index, Mutation::ReorderAndScore, candidate_count);
            double total_ms = 0.0;
            double min_ms = std::numeric_limits<double>::max();
            double max_ms = 0.0;
            for(std::size_t repeat = 0; repeat < kRepeats; ++repeat) {
                const auto started = Clock::now();
                const auto response = run(index, Mutation::ReorderAndScore, candidate_count);
                const auto elapsed = std::chrono::duration<double, std::milli>(
                    Clock::now() - started
                ).count();
                if(response.size() != candidate_count) {
                    return fail("admission benchmark produced an unexpected result size");
                }
                total_ms += elapsed;
                min_ms = std::min(min_ms, elapsed);
                max_ms = std::max(max_ms, elapsed);
            }
            std::cout << candidate_count << ','
                      << (total_ms / static_cast<double>(kRepeats)) << ','
                      << min_ms << ',' << max_ms << '\n';
        }
        return 0;
    }

} // namespace

int main(int argc, char** argv) {
    if(argc > 1 && std::string_view(argv[1]) == "--benchmark") {
        return benchmark_admission();
    }
    FixtureIndex index({
        hit("chunk:alpha", "documents", "resource:alpha", 5, 0, 2, 0.9F),
        hit("chunk:alpha", "documents", "resource:alpha", 5, 1, 2, 0.8F)
    });

    const auto valid = run(index, Mutation::ReorderAndScore);
    if(valid.size() != 2
       || valid.items[0].lexical.result_tier != 1
       || valid.items[0].lexical.score != 10.8F
       || valid.items[1].lexical.result_tier != 0
       || valid.items[1].lexical.score != 10.9F
       || valid.items[0].lexical.resource_id != agent_memory::ResourceId{"resource:alpha"}
       || valid.items[0].lexical.section_id != 5
       || valid.items[0].lexical.enrichment_level != 2
       || valid.items[0].lexical.metadata.get("scope") != std::optional<std::string>{"documents"}) {
        return fail("valid reranking must reorder and adjust scores without changing provenance");
    }

    if(!index.last_query || index.last_query->metadata_filters.size() != 1
       || index.last_query->metadata_filters.front().key != "scope"
       || index.last_query->metadata_filters.front().value != "documents") {
        return fail("mandatory metadata filters must reach the lexical admission query");
    }

    for(const auto mutation : {
            Mutation::ForeignChunk,
            Mutation::Metadata,
            Mutation::Resource,
            Mutation::Section,
            Mutation::Tier,
            Mutation::Enrichment,
            Mutation::Duplicate,
            Mutation::TooMany,
            Mutation::NonFinite,
            Mutation::PositiveInfinity,
            Mutation::NegativeInfinity
        }) {
        if(!run(index, mutation).empty()) {
            return fail("invalid reranker output must fail closed with an empty response");
        }
    }

    if(!run(index, Mutation::Empty).empty()) {
        return fail("an explicitly empty reranker result must remain an empty response");
    }

    const auto subset = run(index, Mutation::Subset, 2);
    if(subset.size() != 1
       || subset.items.front().lexical.chunk_id != agent_memory::ChunkId{"chunk:alpha"}
       || subset.items.front().lexical.resource_id != agent_memory::ResourceId{"resource:alpha"}
       || subset.items.front().lexical.section_id != 5
       || subset.items.front().lexical.result_tier != 0
       || subset.items.front().lexical.enrichment_level != 2
       || subset.items.front().lexical.score != 0.9F
       || subset.items.front().lexical.metadata.get("scope")
            != std::optional<std::string>{"documents"}) {
        return fail("a reranker may return a valid admitted subset without changing fields");
    }

    agent_memory::HybridRetrievalEngine identity_engine(index);
    agent_memory::RetrievalRequest identity_request;
    identity_request.query = "memory";
    identity_request.limit = 2;
    const auto identity = identity_engine.retrieve(identity_request);
    if(identity.size() != 2) {
        return fail("IdentityReranker must preserve the admitted result count");
    }
    const auto& identity_first = identity.items[0].lexical;
    const auto& identity_second = identity.items[1].lexical;
    if(identity_first.chunk_id != agent_memory::ChunkId{"chunk:alpha"}
       || identity_first.resource_id != agent_memory::ResourceId{"resource:alpha"}
       || identity_first.section_id != 5
       || identity_first.result_tier != 0
       || identity_first.enrichment_level != 2
       || identity_first.score != 0.9F
       || identity_first.metadata.get("scope") != std::optional<std::string>{"documents"}
       || identity_second.chunk_id != agent_memory::ChunkId{"chunk:alpha"}
       || identity_second.resource_id != agent_memory::ResourceId{"resource:alpha"}
       || identity_second.section_id != 5
       || identity_second.result_tier != 1
       || identity_second.enrichment_level != 2
       || identity_second.score != 0.8F
       || identity_second.metadata.get("scope") != std::optional<std::string>{"documents"}
       || !std::isfinite(identity_first.score)
       || !std::isfinite(identity_second.score)) {
        return fail("IdentityReranker must preserve all admitted lexical fields and order");
    }

    return 0;
}
