#include <agent_memory/infrastructure/mdbx/MdbxPersistentLexicalIndex.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxSegmentedLexicalIndex.hpp>
#include <agent_memory/lexical/ExactLexicalIndex.hpp>
#include <agent_memory/lexical/StandardTokenizer.hpp>

#if AGENT_MEMORY_HAS_MDBX

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iomanip>
#include <iostream>
#include <map>
#include <memory>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <type_traits>
#include <utility>
#include <vector>

namespace {

using namespace agent_memory;
using Clock = std::chrono::steady_clock;
constexpr double kScoreTolerance = 1e-5;

struct Document final {
    std::uint64_t id = 0;
    std::string resource;
    std::uint64_t generation = 1;
    std::uint64_t revision = 1;
    std::vector<std::string> terms;
};

struct Query final {
    std::string id;
    std::string mode;
    std::vector<std::string> terms;
    std::map<std::uint64_t, int> qrels;
};

std::vector<std::string> tokenize(const std::string& text) {
    const auto result = StandardTokenizer{}.tokenize(text);
    std::vector<std::string> output;
    output.reserve(result.tokens.size());
    for(const auto& token : result.tokens) output.push_back(token.text);
    return output;
}

std::vector<std::string> split(std::string_view value, const char separator) {
    std::vector<std::string> result;
    std::size_t begin = 0;
    while(begin <= value.size()) {
        const auto end = value.find(separator, begin);
        result.emplace_back(value.substr(begin, end == std::string_view::npos
            ? value.size() - begin : end - begin));
        if(end == std::string_view::npos) break;
        begin = end + 1U;
    }
    return result;
}

std::vector<Document> load_documents(const std::filesystem::path& path) {
    std::ifstream input(path, std::ios::binary);
    if(!input) throw std::runtime_error("cannot open R2 corpus: " + path.string());
    std::vector<Document> documents;
    std::set<std::uint64_t> ids;
    std::string line;
    while(std::getline(input, line)) {
        if(line.empty() || line[0] == '#') continue;
        const auto fields = split(line, '\t');
        if(fields.size() != 5 || fields[4].rfind("raw:", 0) != 0) {
            throw std::runtime_error("R2 corpus rows require id/resource/generation/revision/raw:text");
        }
        Document document;
        document.id = std::stoull(fields[0]);
        document.resource = fields[1];
        document.generation = std::stoull(fields[2]);
        document.revision = std::stoull(fields[3]);
        document.terms = tokenize(fields[4].substr(4));
        if(document.id == 0 || document.resource.empty() || document.generation == 0
            || document.revision == 0 || document.terms.empty() || !ids.insert(document.id).second) {
            throw std::runtime_error("invalid or duplicate R2 corpus row");
        }
        documents.push_back(std::move(document));
    }
    if(documents.empty()) throw std::runtime_error("R2 corpus is empty");
    return documents;
}

std::vector<Query> load_queries(const std::filesystem::path& path) {
    std::ifstream input(path, std::ios::binary);
    if(!input) throw std::runtime_error("cannot open R2 queries: " + path.string());
    std::vector<Query> queries;
    std::set<std::string> ids;
    std::string line;
    while(std::getline(input, line)) {
        if(line.empty() || line[0] == '#') continue;
        const auto fields = split(line, '\t');
        if(fields.size() != 4 || fields[2].rfind("raw:", 0) != 0) {
            throw std::runtime_error("R2 query rows require id/mode/raw:text/qrels");
        }
        Query query;
        query.id = fields[0];
        query.mode = fields[1];
        query.terms = tokenize(fields[2].substr(4));
        if(query.id.empty() || !ids.insert(query.id).second
            || (query.mode != "judged" && query.mode != "no_answer")) {
            throw std::runtime_error("invalid or duplicate R2 query");
        }
        if(fields[3] != "-") {
            for(const auto& judgment : split(fields[3], ',')) {
                if(judgment.empty()) continue;
                const auto separator = judgment.find(':');
                if(separator == std::string::npos) throw std::runtime_error("malformed R2 qrel");
                const auto id = std::stoull(judgment.substr(0, separator));
                const auto grade = std::stoi(judgment.substr(separator + 1U));
                if(id == 0 || grade < 0 || !query.qrels.emplace(id, grade).second) {
                    throw std::runtime_error("invalid or duplicate R2 qrel");
                }
            }
        }
        if((query.mode == "judged" && query.qrels.empty())
            || (query.mode == "no_answer" && !query.qrels.empty())) {
            throw std::runtime_error("R2 qrel completeness violation");
        }
        queries.push_back(std::move(query));
    }
    if(queries.empty()) throw std::runtime_error("R2 query set is empty");
    return queries;
}

LexicalIndexDescriptor descriptor(const std::uint32_t layout = kLexicalSegmentLayoutVersion) {
    std::array<std::uint8_t, 16> epoch{};
    epoch[0] = 91;
    epoch[15] = 92;
    return LexicalIndexDescriptor{
        LexicalIndexDescriptorRef{ScopeId{"r2-scope"}, ProjectionKind::Original,
            LexicalStatisticsEpoch{epoch}}, "original", 1, "tokenizer-v1",
        "normalization-v1", {layout}};
}

FlatLexicalUnitInput unit(const Document& document) {
    return FlatLexicalUnitInput{
        ScopeId{"r2-scope"}, KnowledgeUnitId{document.id}, ProjectionKind::Original,
        ResourceId{document.resource}, document.generation, document.revision,
        ProjectionVersionRef{document.revision, document.revision, 1, "r2-v1"},
        document.terms};
}

LexicalDocumentRecord record(const Document& document) {
    std::vector<Token> tokens;
    tokens.reserve(document.terms.size());
    for(std::size_t position = 0; position < document.terms.size(); ++position) {
        tokens.push_back(Token{document.terms[position], TextRange{position, 1},
            position, TokenKind::Word});
    }
    return LexicalDocumentRecord{
        ChunkId{"r2-chunk-" + std::to_string(document.id)},
        ResourceRevision{ResourceId{document.resource}, document.generation,
            document.revision, 1}, std::move(tokens), Metadata{}};
}

struct Hit final {
    std::uint64_t id = 0;
    double score = 0.0;
};

bool same_ranking(const std::vector<Hit>& lhs, const std::vector<Hit>& rhs) {
    if(lhs.size() != rhs.size()) return false;
    for(std::size_t i = 0; i < lhs.size(); ++i) {
        if(lhs[i].id != rhs[i].id) return false;
    }
    return true;
}

bool same_scores(const std::vector<Hit>& lhs, const std::vector<Hit>& rhs) {
    if(lhs.size() != rhs.size()) return false;
    for(std::size_t i = 0; i < lhs.size(); ++i) {
        if(std::abs(lhs[i].score - rhs[i].score) > kScoreTolerance) return false;
    }
    return true;
}

std::vector<Hit> score(const FlatLexicalIndex& index, const Query& query,
    const std::size_t limit) {
    const auto stats = index.collection_stats();
    const double average = stats.unit_count == 0 ? 1.0
        : static_cast<double>(stats.total_token_count) / stats.unit_count;
    std::map<std::uint64_t, double> scores;
    for(const auto& term : query.terms) {
        const auto token = index.find_token_id(term);
        if(!token) continue;
        const auto token_stats = index.find_token_stats(*token);
        if(!token_stats || token_stats->document_frequency == 0) continue;
        const double n = static_cast<double>(stats.unit_count);
        const double df = static_cast<double>(token_stats->document_frequency);
        const double idf = std::log(1.0 + (n - df + 0.5) / (df + 0.5));
        for(const auto& posting : index.postings(*token)) {
            const auto unit_stats = index.find_unit_stats(posting.unit_id);
            if(!unit_stats) continue;
            const double length = static_cast<double>(unit_stats->token_count);
            const double denominator = posting.term_frequency + 1.5 *
                (1.0 - 0.75 + 0.75 * length / average);
            scores[posting.unit_id.value()] += idf *
                ((posting.term_frequency * 2.5) / denominator);
        }
    }
    std::vector<Hit> result;
    for(const auto& item : scores) if(item.second > 0.0) result.push_back(Hit{item.first, item.second});
    std::sort(result.begin(), result.end(), [](const Hit& lhs, const Hit& rhs) {
        return lhs.score == rhs.score ? lhs.id < rhs.id : lhs.score > rhs.score;
    });
    if(result.size() > limit) result.resize(limit);
    return result;
}

std::vector<Hit> score_exact(const ExactLexicalIndex& index, const Query& query,
    const std::size_t limit) {
    LexicalSearchQuery search;
    search.terms = query.terms;
    search.limit = limit;
    const auto results = index.search(search);
    std::vector<Hit> hits;
    for(const auto& result : results) {
        const auto prefix = std::string{"r2-chunk-"};
        hits.push_back(Hit{std::stoull(result.chunk_id.value().substr(prefix.size())), result.score});
    }
    return hits;
}

double percentile(std::vector<double> values, const double fraction) {
    if(values.empty()) return 0.0;
    std::sort(values.begin(), values.end());
    const auto index = std::min(values.size() - 1U,
        static_cast<std::size_t>(std::ceil(fraction * values.size())) - 1U);
    return values[index];
}

double recall(const std::vector<Hit>& hits, const Query& query, const std::size_t limit) {
    std::size_t positives = 0;
    std::size_t found = 0;
    for(const auto& item : query.qrels) if(item.second > 0) {
        ++positives;
        if(std::find_if(hits.begin(), hits.begin() + std::min(limit, hits.size()),
            [&](const Hit& hit) { return hit.id == item.first; }) != hits.begin() + std::min(limit, hits.size())) ++found;
    }
    return positives == 0 ? 0.0 : static_cast<double>(found) / positives;
}

struct BackendResult final {
    std::string name;
    bool ranking_parity = true;
    bool score_parity = true;
    double build_ms = 0.0;
    double update_ms = 0.0;
    double reopen_ms = 0.0;
    std::uintmax_t index_bytes = 0;
    std::vector<double> query_ms;
    double recall_at_1 = 0.0;
    double recall_at_5 = 0.0;
    double ndcg_at_10 = 0.0;
    double mrr = 0.0;
    double no_answer_accuracy = 0.0;
};

void add_quality(BackendResult& result, const std::vector<std::vector<Hit>>& hits,
    const std::vector<Query>& queries) {
    std::size_t judged = 0;
    std::size_t no_answer = 0;
    for(std::size_t query_index = 0; query_index < queries.size(); ++query_index) {
        const auto& query = queries[query_index];
        const auto& ranking = hits[query_index];
        if(query.mode == "no_answer") {
            ++no_answer;
            if(ranking.empty()) ++result.no_answer_accuracy;
            continue;
        }
        ++judged;
        result.recall_at_1 += recall(ranking, query, 1);
        result.recall_at_5 += recall(ranking, query, 5);
        for(std::size_t rank = 0; rank < ranking.size(); ++rank) {
            const auto found = query.qrels.find(ranking[rank].id);
            if(found != query.qrels.end() && found->second > 0) {
                result.mrr += 1.0 / static_cast<double>(rank + 1U);
                break;
            }
        }
        double dcg = 0.0;
        for(std::size_t rank = 0; rank < std::min<std::size_t>(10, ranking.size()); ++rank) {
            const auto found = query.qrels.find(ranking[rank].id);
            const auto grade = found == query.qrels.end() ? 0 : found->second;
            dcg += (std::pow(2.0, grade) - 1.0) / std::log2(static_cast<double>(rank + 2U));
        }
        std::vector<int> ideal;
        for(const auto& item : query.qrels) if(item.second > 0) ideal.push_back(item.second);
        std::sort(ideal.begin(), ideal.end(), std::greater<>());
        double idcg = 0.0;
        for(std::size_t rank = 0; rank < std::min<std::size_t>(10, ideal.size()); ++rank) {
            idcg += (std::pow(2.0, ideal[rank]) - 1.0) / std::log2(static_cast<double>(rank + 2U));
        }
        result.ndcg_at_10 += idcg == 0.0 ? 0.0 : dcg / idcg;
    }
    if(judged != 0) {
        result.recall_at_1 /= judged;
        result.recall_at_5 /= judged;
        result.ndcg_at_10 /= judged;
        result.mrr /= judged;
    }
    if(no_answer != 0) result.no_answer_accuracy /= no_answer;
}

template<typename IndexT, typename OptionsT>
BackendResult run_durable(const std::string& name, const std::filesystem::path& path,
    const std::vector<Document>& documents, const std::vector<Query>& queries,
    const std::vector<std::vector<Hit>>& baseline, const std::size_t limit) {
    std::error_code error;
    std::filesystem::remove(path, error);
    std::filesystem::remove(path.string() + "-lck", error);
    BackendResult result{name};
    const auto make_options = [&](const std::shared_ptr<MdbxStorageContext>& context) {
        OptionsT options;
        options.path = path.string();
        options.context = context;
        options.descriptor = descriptor();
        if constexpr(std::is_same_v<OptionsT, MdbxSegmentedLexicalIndexOptions>) {
            options.descriptor = descriptor(kLexicalIncrementalSegmentLayoutVersion);
            options.encoding_limits.max_postings_per_segment = 128;
        }
        return options;
    };
    {
        auto context = MdbxStorageContext::open(path.string());
        auto options = make_options(context);
        const auto build_start = Clock::now();
        IndexT index{options};
        const auto adapter_start = Clock::now();
        for(const auto& document : documents) index.upsert(unit(document));
        const auto update_end = Clock::now();
        result.build_ms = std::chrono::duration<double, std::milli>(
            adapter_start - build_start).count();
        result.update_ms = std::chrono::duration<double, std::milli>(
            update_end - adapter_start).count();
        {
            const auto snapshot = index.read_snapshot();
            std::vector<std::vector<Hit>> actual;
            for(std::size_t query_index = 0; query_index < queries.size(); ++query_index) {
                const auto started = Clock::now();
                const auto hits = score(snapshot.index, queries[query_index], limit);
                actual.push_back(hits);
                result.query_ms.push_back(std::chrono::duration<double, std::milli>(
                    Clock::now() - started).count());
                result.ranking_parity = result.ranking_parity
                    && same_ranking(hits, baseline[query_index]);
                result.score_parity = result.score_parity
                    && same_scores(hits, baseline[query_index]);
            }
            add_quality(result, actual, queries);
        }
    }
    const auto reopen_start = Clock::now();
    auto reopened_context = MdbxStorageContext::open(path.string());
    auto reopened_options = make_options(reopened_context);
    IndexT reopened{reopened_options};
    const auto reopened_snapshot = reopened.read_snapshot();
    result.reopen_ms = std::chrono::duration<double, std::milli>(Clock::now() - reopen_start).count();
    for(std::size_t query_index = 0; query_index < queries.size(); ++query_index) {
        const auto hits = score(reopened_snapshot.index, queries[query_index], limit);
        result.ranking_parity = result.ranking_parity
            && same_ranking(hits, baseline[query_index]);
        result.score_parity = result.score_parity
            && same_scores(hits, baseline[query_index]);
    }
    result.index_bytes = std::filesystem::file_size(path, error);
    return result;
}

std::string json_escape(const std::string& value) {
    std::string result;
    for(const char character : value) {
        if(character == '\\' || character == '"') result.push_back('\\');
        result.push_back(character);
    }
    return result;
}

void write_receipt(const std::filesystem::path& output, const std::string& corpus_name,
    const std::vector<BackendResult>& results, const std::size_t query_count,
    const std::size_t limit) {
    std::ofstream stream(output, std::ios::binary);
    if(!stream) throw std::runtime_error("cannot write R2 receipt");
    stream << "{\n  \"schema_version\": 1,\n  \"protocol\": \"lexical_retrieval_qualification_r2\",\n"
        << "  \"corpus\": \"" << json_escape(corpus_name) << "\",\n"
        << "  \"candidate_depth\": " << limit << ",\n  \"score_tolerance\": "
        << kScoreTolerance << ",\n  \"query_count\": " << query_count
        << ",\n  \"backends\": [\n";
    for(std::size_t index = 0; index < results.size(); ++index) {
        const auto& result = results[index];
        stream << "    {\"id\": \"" << result.name << "\", \"ranking_parity\": "
            << (result.ranking_parity ? "true" : "false") << ", \"score_parity\": "
            << (result.score_parity ? "true" : "false") << ", \"index_build_ms\": "
            << result.build_ms << ", \"update_ms\": " << result.update_ms
            << ", \"reopen_ms\": " << result.reopen_ms << ", \"index_bytes\": "
            << result.index_bytes << ", \"quality\": {\"recall_at_1\": "
            << result.recall_at_1 << ", \"recall_at_5\": " << result.recall_at_5
            << ", \"ndcg_at_10\": " << result.ndcg_at_10 << ", \"mrr\": "
            << result.mrr << ", \"no_answer_accuracy\": " << result.no_answer_accuracy
            << "}, \"latency_ms\": {\"p50\": "
            << percentile(result.query_ms, .50) << ", \"p95\": "
            << percentile(result.query_ms, .95) << ", \"p99\": "
            << percentile(result.query_ms, .99) << ", \"sample_count\": "
            << result.query_ms.size() << ", \"observation_unit\": \"one_query\", \"statistical_status\": \"directional\"}}"
            << (index + 1U == results.size() ? "\n" : ",\n");
    }
    stream << "  ],\n  \"quality_status\": \"QUALITY_INCONCLUSIVE\",\n"
        << "  \"hydration_status\": \"NOT_MEASURED_NO_CANONICAL_READ_PATH\"\n}\n";
}

int run(const std::filesystem::path& corpus_path, const std::filesystem::path& queries_path,
    const std::filesystem::path& output, const std::filesystem::path& database_root) {
    const auto documents = load_documents(corpus_path);
    const auto queries = load_queries(queries_path);
    const std::size_t limit = 10;
    ExactLexicalIndex exact;
    const auto exact_start = Clock::now();
    for(const auto& document : documents) exact.upsert(record(document));
    const auto exact_build_ms = std::chrono::duration<double, std::milli>(Clock::now() - exact_start).count();
    static_cast<void>(exact_build_ms);
    BackendResult baseline_result{"bm25"};
    std::vector<std::vector<Hit>> baseline;
    for(const auto& query : queries) {
        const auto started = Clock::now();
        baseline.push_back(score_exact(exact, query, limit));
        baseline_result.query_ms.push_back(std::chrono::duration<double, std::milli>(Clock::now() - started).count());
    }
    add_quality(baseline_result, baseline, queries);
    baseline_result.build_ms = exact_build_ms;
    std::vector<BackendResult> results;
    results.push_back(std::move(baseline_result));
    results.push_back(run_durable<MdbxPersistentLexicalIndex, MdbxPersistentLexicalIndexOptions>(
        "flat-v2", database_root.string() + "-flat-v2", documents, queries, baseline, limit));
    results.push_back(run_durable<MdbxSegmentedLexicalIndex, MdbxSegmentedLexicalIndexOptions>(
        "segmented-v2", database_root.string() + "-segmented-v2", documents, queries, baseline, limit));
    write_receipt(output, corpus_path.filename().string(), results, queries.size(), limit);
    for(const auto& result : results) {
        if(!result.ranking_parity || !result.score_parity) return 2;
    }
    return 0;
}

} // namespace

int main(int argc, char** argv) {
    try {
        if(argc != 5) {
            std::cerr << "usage: lexical-retrieval-qualification-r2 corpus.tsv queries.tsv receipt.json database-root\n";
            return 64;
        }
        return run(argv[1], argv[2], argv[3], argv[4]);
    } catch(const std::exception& error) {
        std::cerr << "R2 qualification failed: " << error.what() << '\n';
        return 1;
    }
}

#else
int main() { return 0; }
#endif
