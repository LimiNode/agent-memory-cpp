#include <agent_memory.hpp>

#include <algorithm>
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
                    candidates.push_back(candidates.front());
                    break;
                case Mutation::TooMany:
                    candidates.push_back(candidates.back());
                    break;
                case Mutation::NonFinite:
                    candidates.front().score = std::numeric_limits<float>::quiet_NaN();
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

} // namespace

int main() {
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
            Mutation::NonFinite
        }) {
        if(!run(index, mutation).empty()) {
            return fail("invalid reranker output must fail closed with an empty response");
        }
    }

    if(!run(index, Mutation::Empty).empty()) {
        return fail("an explicitly empty reranker result must remain an empty response");
    }

    agent_memory::HybridRetrievalEngine identity_engine(index);
    agent_memory::RetrievalRequest identity_request;
    identity_request.query = "memory";
    identity_request.limit = 2;
    const auto identity = identity_engine.retrieve(identity_request);
    if(identity.size() != 2
       || identity.items[0].lexical.result_tier != 0
       || identity.items[1].lexical.result_tier != 1
       || !std::isfinite(identity.items[0].lexical.score)) {
        return fail("IdentityReranker must preserve the admitted lexical results");
    }

    return 0;
}
