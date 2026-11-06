#include <agent_memory/eval/DatasetLoader.hpp>

#include <string_view>

int main() {
    constexpr std::string_view fixture = R"json({
        "queries": [{"id": "q1", "text": "memory"}],
        "documents": [{"id": "d1", "text": "memory"}],
        "qrels": [{"query_id": "q1", "item_id": "d1", "relevance_grade": 1}]
    })json";
    const auto dataset = agent_memory::load_dataset_from_json_string(fixture);
    return dataset.queries.size() == 1 && dataset.documents.size() == 1
        && dataset.qrels.size() == 1 ? 0 : 1;
}
