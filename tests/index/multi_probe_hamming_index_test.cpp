#include <agent_memory.hpp>

#include <cstddef>
#include <iostream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace {

    int fail(std::string_view message) {
        std::cerr << message << '\n';
        return 1;
    }

    agent_memory::BinarySignature make_signature(std::vector<std::size_t> bits) {
        agent_memory::BinarySignature signature(16);
        for(const auto bit : bits) {
            signature.set_bit(bit);
        }
        return signature;
    }

    agent_memory::BinarySignatureInfo make_info() {
        agent_memory::BinarySignatureInfo info;
        info.encoder_id = "test";
        info.encoder_version = "v1";
        info.encoder_config_fingerprint = "test:16";
        info.source_model_id = "model:test";
        info.projection_kind = "symmetric";
        info.source_dimension = 4;
        info.bit_count = 16;
        info.source_similarity_metric = agent_memory::SimilarityMetric::Cosine;
        info.source_normalized = true;
        return info;
    }

    agent_memory::BinarySignatureRecord make_record(
        std::string id,
        std::vector<std::size_t> bits,
        std::string scope
    ) {
        agent_memory::Metadata metadata;
        metadata.set("scope", std::move(scope));
        return {
            agent_memory::ChunkId{std::move(id)},
            make_signature(std::move(bits)),
            make_info(),
            std::move(metadata)
        };
    }

    agent_memory::BinarySignatureSearchQuery make_query(std::size_t limit) {
        return {make_signature({}), make_info(), limit, {}};
    }

    template <typename Function>
    bool throws_invalid_argument(Function&& function) {
        try {
            function();
        } catch(const std::invalid_argument&) {
            return true;
        }
        return false;
    }

    template <typename Function>
    bool throws_runtime_error(Function&& function) {
        try {
            function();
        } catch(const std::runtime_error&) {
            return true;
        }
        return false;
    }

} // namespace

int main() {
    agent_memory::MultiProbeHammingIndexOptions options;
    options.signature_info = make_info();
    options.table_count = 2;
    options.bits_per_table = 4;
    options.max_probe_radius = 0;
    options.candidate_multiplier = 1;
    options.minimum_candidate_count = 0;
    agent_memory::MultiProbeHammingIndex index(options);

    index.upsert(make_record("chunk:exact", {}, "keep"));
    index.upsert(make_record("chunk:near", {1}, "keep"));
    index.upsert(make_record("chunk:miss-a", {0, 8}, "keep"));
    index.upsert(make_record("chunk:miss-b", {2, 10}, "drop"));

    const auto diagnostic = index.search_with_diagnostics(make_query(2));
    if(diagnostic.candidate_count != 2 || diagnostic.candidate_count >= index.size()) {
        return fail("multi-probe search must rank a strict sub-linear candidate subset");
    }
    if(diagnostic.results.size() != 2
       || diagnostic.results[0].chunk_id.value() != "chunk:exact"
       || diagnostic.results[0].hamming_distance != 0
       || diagnostic.results[1].chunk_id.value() != "chunk:near"
       || diagnostic.results[1].hamming_distance != 1) {
        return fail("multi-probe search must exact-rank the candidates it discovers");
    }

    index.upsert(make_record("chunk:near", {3}, "updated"));
    const auto updated = index.find(agent_memory::ChunkId{"chunk:near"});
    if(!updated || !updated->metadata.get("scope")
       || *updated->metadata.get("scope") != "updated") {
        return fail("multi-probe upsert must replace signature and metadata in place");
    }

    if(!index.erase(agent_memory::ChunkId{"chunk:exact"})
       || index.find(agent_memory::ChunkId{"chunk:exact"})) {
        return fail("multi-probe erase must remove records and bucket postings");
    }
    if(index.size() != 3 || index.search(make_query(2)).empty()) {
        return fail("multi-probe erase must preserve swapped record positions");
    }

    agent_memory::MultiProbeHammingIndex churn_index(options);
    for(std::size_t record = 0; record < 6; ++record) {
        churn_index.upsert(make_record(
            "chunk:churn-" + std::to_string(record),
            {1, 3},
            "keep"
        ));
    }
    if(!churn_index.erase(agent_memory::ChunkId{"chunk:churn-0"})
       || !churn_index.erase(agent_memory::ChunkId{"chunk:churn-1"})) {
        return fail("multi-probe churn fixture must erase non-final records");
    }
    const auto after_churn = churn_index.search_with_diagnostics(make_query(10));
    if(after_churn.results.size() != churn_index.size()
       || after_churn.visited_posting_count != churn_index.size() * options.table_count) {
        return fail("multi-probe erase must not duplicate moved-record postings");
    }

    agent_memory::MultiProbeHammingIndexOptions filtered_options;
    filtered_options.signature_info = make_info();
    filtered_options.table_count = 1;
    filtered_options.bits_per_table = 4;
    filtered_options.max_probe_radius = 1;
    filtered_options.candidate_multiplier = 1;
    filtered_options.minimum_candidate_count = 0;
    agent_memory::MultiProbeHammingIndex filtered_index(filtered_options);
    filtered_index.upsert(make_record("chunk:drop-a", {}, "drop"));
    filtered_index.upsert(make_record("chunk:drop-b", {1}, "drop"));
    filtered_index.upsert(make_record("chunk:keep-a", {0}, "keep"));
    filtered_index.upsert(make_record("chunk:keep-b", {0, 1}, "keep"));

    auto filtered_query = make_query(2);
    filtered_query.metadata_filters.push_back({"scope", "keep"});
    const auto filtered = filtered_index.search_with_diagnostics(filtered_query);
    if(filtered.results.size() != 2 || filtered.candidate_count != 2
       || filtered.probed_bucket_count <= 1) {
        return fail(
            "multi-probe search must continue probing until the filtered target is reached"
        );
    }

    if(!throws_invalid_argument([] {
           agent_memory::MultiProbeHammingIndexOptions invalid;
           invalid.signature_info = make_info();
           invalid.table_count = 5;
           invalid.bits_per_table = 4;
           (void)agent_memory::MultiProbeHammingIndex(invalid);
       })) {
        return fail("multi-probe index must reject projections wider than signatures");
    }

    // Every insertion phase must roll back without consulting uncommitted
    // signature storage, and a failed replacement must retain the old record.
    agent_memory::MultiProbeHammingIndex failure_index(options);
    failure_index.set_failure_injection_for_testing(
        agent_memory::MultiProbeHammingIndexFaultPoint::AfterRecordAppendBeforeSignatureStorage
    );
    if(!throws_runtime_error([&] {
           failure_index.upsert(make_record("chunk:first-failure", {}, "keep"));
       })) {
        return fail("record-before-signature fault did not interrupt insertion");
    }
    if(failure_index.size() != 0
       || failure_index.find(agent_memory::ChunkId{"chunk:first-failure"})) {
        return fail("record-before-signature fault left partial record state");
    }
    failure_index.upsert(make_record("chunk:first-failure", {}, "retry"));
    if(failure_index.size() != 1) {
        return fail("first-record identity was not reusable after failed insertion");
    }

    auto dynamic_options = options;
    dynamic_options.signature_info.reset();
    agent_memory::MultiProbeHammingIndex adopted_identity_index(dynamic_options);
    adopted_identity_index.set_failure_injection_for_testing(
        agent_memory::MultiProbeHammingIndexFaultPoint::AfterRecordAppendBeforeSignatureStorage
    );
    if(!throws_runtime_error([&] {
           adopted_identity_index.upsert(make_record("chunk:adopted-a", {}, "failed"));
       })) {
        return fail("dynamic identity insertion fault did not interrupt the first write");
    }
    if(adopted_identity_index.size() != 0 || adopted_identity_index.bit_count() != 0) {
        return fail("failed dynamic identity insertion must roll back identity adoption");
    }
    auto distinct_identity = make_record("chunk:adopted-b", {}, "retry");
    distinct_identity.signature_info.encoder_version = "v2";
    distinct_identity.signature_info.encoder_config_fingerprint = "test:16:v2";
    adopted_identity_index.upsert(std::move(distinct_identity));
    if(adopted_identity_index.size() != 1 || adopted_identity_index.bit_count() != 16) {
        return fail("a distinct identity must be accepted after failed first insertion");
    }

    failure_index.set_failure_injection_for_testing(
        agent_memory::MultiProbeHammingIndexFaultPoint::AfterSignatureStorageBeforeTables
    );
    if(!throws_runtime_error([&] {
           failure_index.upsert(make_record("chunk:signature-failure", {1}, "keep"));
       })) {
        return fail("signature-storage fault did not interrupt insertion");
    }
    if(failure_index.size() != 1
       || failure_index.find(agent_memory::ChunkId{"chunk:signature-failure"})) {
        return fail("signature-storage fault left partial record state");
    }

    failure_index.set_failure_injection_for_testing(
        agent_memory::MultiProbeHammingIndexFaultPoint::AfterFirstTableInsert
    );
    if(!throws_runtime_error([&] {
           failure_index.upsert(make_record("chunk:table-failure", {2}, "keep"));
       })) {
        return fail("partial-table fault did not interrupt insertion");
    }
    if(failure_index.size() != 1
       || failure_index.find(agent_memory::ChunkId{"chunk:table-failure"})) {
        return fail("partial-table fault left a record or posting behind");
    }

    const auto before_replacement = failure_index.find(
        agent_memory::ChunkId{"chunk:first-failure"}
    );
    failure_index.set_failure_injection_for_testing(
        agent_memory::MultiProbeHammingIndexFaultPoint::AfterFirstTableInsert
    );
    if(!throws_runtime_error([&] {
           failure_index.upsert(make_record("chunk:first-failure", {3}, "replacement"));
       })) {
        return fail("replacement table fault did not interrupt update");
    }
    const auto after_replacement = failure_index.find(
        agent_memory::ChunkId{"chunk:first-failure"}
    );
    if(!before_replacement || !after_replacement
       || after_replacement->signature.words() != before_replacement->signature.words()
       || !after_replacement->metadata.get("scope")
       || *after_replacement->metadata.get("scope") != "retry") {
        return fail("failed replacement did not restore the prior record");
    }

    agent_memory::MultiProbeHammingIndex same_bucket_index(options);
    same_bucket_index.upsert(make_record("chunk:same-bucket", {}, "original"));
    same_bucket_index.set_failure_injection_for_testing(
        agent_memory::MultiProbeHammingIndexFaultPoint::AfterFirstTableInsert
    );
    if(!throws_runtime_error([&] {
           same_bucket_index.upsert(make_record("chunk:same-bucket", {}, "failed"));
       })) {
        return fail("same-bucket replacement fault did not interrupt update");
    }
    const auto same_bucket_after_failure = same_bucket_index.find(
        agent_memory::ChunkId{"chunk:same-bucket"}
    );
    if(!same_bucket_after_failure || !same_bucket_after_failure->metadata.get("scope")
       || *same_bucket_after_failure->metadata.get("scope") != "original"
       || same_bucket_index.search(make_query(1)).size() != 1) {
        return fail("same-bucket replacement rollback removed the original posting");
    }
    const auto failure_search = failure_index.search_with_diagnostics(make_query(10));
    if(failure_search.results.size() != failure_index.size()) {
        return fail("search diagnostics lost consistency after injected failures");
    }
    if(!failure_index.erase(agent_memory::ChunkId{"chunk:first-failure"})
       || failure_index.size() != 0) {
        return fail("erase failed after injected insertion and replacement faults");
    }
    failure_index.clear();
    if(failure_index.size() != 0 || failure_index.bit_count() != 16) {
        return fail("clear failed after injected insertion and replacement faults");
    }
    failure_index.upsert(make_record("chunk:after-clear", {4}, "clear"));
    if(failure_index.search(make_query(1)).empty()) {
        return fail("retry after clear failed following injected insertion faults");
    }

    index.clear();
    if(index.size() != 0 || index.bit_count() != 16) {
        return fail("multi-probe clear must preserve configured identity");
    }

    return 0;
}
