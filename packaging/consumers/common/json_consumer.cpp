#include <agent_memory/eval/DatasetLoader.hpp>

#include <string_view>

int main() {
    constexpr std::string_view fixture = R"json({
        "name": "packaging-json-consumer",
        "queries": [{"id": "q1", "text": "memory", "query_type": "exact", "limit": 1}],
        "corpus": [{"id": "d1", "title": "Memory", "text": "memory"}],
        "judgments": [{"query_id": "q1", "item_id": "d1", "relevance_grade": 1}]
    })json";
    const auto dataset = agent_memory::load_dataset_from_json_string(fixture);
    return dataset.queries.size() == 1 && dataset.corpus.size() == 1
        && dataset.judgments.size() == 1 ? 0 : 1;
}
