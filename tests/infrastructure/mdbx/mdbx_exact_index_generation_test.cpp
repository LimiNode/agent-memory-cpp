#include <agent_memory/infrastructure/mdbx/MdbxExactIndexGenerationStore.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxResourceManifestStorage.hpp>
#include "exact_generation_fixture.hpp"

#include <chrono>
#include <cstdlib>
#include <limits>
#include <system_error>

namespace {
using namespace agent_memory;
using namespace exact_fixture;
std::size_t controls = 0;
template<class F> void rejects(IndexGenerationFailure code, F&& fn) {
    ++controls;
    try { fn(); } catch(const IndexGenerationError& error) {
        expect(error.code() == code, "unexpected typed generation failure"); return;
    }
    throw std::runtime_error("negative generation control accepted invalid state");
}
ExactIndexSpaceDescriptor descriptor() {
    ExactIndexSpaceDescriptor d; d.model_id = "fixture-exact-v1";
    d.recipe_id = "fixture-explicit-fp32-v1"; d.dimensions = 2; d.scope_id = ScopeId{"alpha"}; return d;
}
void frame(std::string& out, const std::string& value) {
    for(unsigned i = 0; i < 8; ++i) out.push_back(static_cast<char>((value.size() >> (8U * i)) & 255U));
    out += value;
}
std::string row_key(const std::string& generation) {
    std::string out; frame(out, "test"); frame(out, generation); out.push_back('r'); out.append(8, '\0'); return out;
}
} // namespace

int main() try {
    const auto path = std::filesystem::current_path() /
        ("exact-generation-" + std::to_string(std::chrono::high_resolution_clock::now().time_since_epoch().count()));
    expect(std::filesystem::create_directory(path), "test directory is not exclusively owned");
    const auto db = path / "knowledge.mdbx", cas = path / "cas";
    const auto allow = [](const KnowledgeUnitEnvelope&) { return true; };
    const auto d = descriptor();
    std::optional<ExactIndexGenerationSnapshot> pinned;
    KnowledgeUnitId a, b;
    std::string canonical;
    {
        auto context = MdbxStorageContext::open(db.string()); create(context, cas);
        const auto all = ids(context, d.scope_id); expect(all.size() == 2, "expected two alpha units");
        a = all[0]; b = all[1]; canonical = snapshot(context, cas);
        MdbxExactIndexGenerationStore store(context, "test");
        rejects(IndexGenerationFailure::NotFound, [&] { (void)store.pin(d); });
        rejects(IndexGenerationFailure::InvalidInput, [&] { store.begin("oversized", d, 1025); });
        store.begin("first", d, 2); store.begin("first", d, 2);
        rejects(IndexGenerationFailure::Conflict, [&] { store.begin("first", d, 3); });
        auto other_space = d; other_space.model_id = "other-space";
        rejects(IndexGenerationFailure::Conflict, [&] { store.begin("first", other_space, 2); });
        rejects(IndexGenerationFailure::Incomplete, [&] { store.activate("first", d); });
        for(float value : {std::numeric_limits<float>::quiet_NaN(),
                           std::numeric_limits<float>::infinity(),
                           -std::numeric_limits<float>::infinity()}) {
            rejects(IndexGenerationFailure::InvalidInput, [&] {
                store.append("first", {{a, 1, {value, 0}}});
            });
        }
        store.append("first", {{a, 1, {10, 0}}});
        rejects(IndexGenerationFailure::Incomplete, [&] { store.complete("first"); });
        rejects(IndexGenerationFailure::RebuildUnavailable, [&] { store.append("first", {{b, 1, {}}}); });
        rejects(IndexGenerationFailure::Conflict, [&] { store.append("first", {{a, 1, {1, 0}}}); });
        store.append("first", {{b, 1, {1, 0}}}); store.complete("first"); store.complete("first");
        store.activate("first", d); pinned = store.pin(d);
        for(float value : {std::numeric_limits<float>::quiet_NaN(),
                           std::numeric_limits<float>::infinity(),
                           -std::numeric_limits<float>::infinity()}) {
            for(std::size_t limit : {std::size_t{0}, std::size_t{1}}) {
                rejects(IndexGenerationFailure::InvalidInput, [&] {
                    (void)store.query(*pinned, d.scope_id, {value, 0}, limit, allow);
                });
            }
        }
        MdbxExactIndexGenerationStore other_index(context, "other-index");
        rejects(IndexGenerationFailure::Incompatible, [&] {
            (void)other_index.query(*pinned, d.scope_id, {1, 0}, 8, allow);
        });
        other_index.begin("other-first", d, 2);
        other_index.append("other-first", {{a, 1, {10, 0}}, {b, 1, {1, 0}}});
        other_index.complete("other-first"); other_index.activate("other-first", d);
        const auto other_pinned = other_index.pin(d);
        rejects(IndexGenerationFailure::Incompatible, [&] {
            (void)other_index.query(*pinned, d.scope_id, {1, 0}, 8, allow);
        });
        rejects(IndexGenerationFailure::Incompatible, [&] {
            (void)store.query(other_pinned, d.scope_id, {1, 0}, 8, allow);
        });
        expect(store.query(*pinned, d.scope_id, {1, 0}, 1, allow).front().unit.id == a,
               "valid snapshot was rejected after another index activation");
        MdbxExactIndexGenerationStore shared_store(context, "test");
        expect(shared_store.query(*pinned, d.scope_id, {1, 0}, 1, allow).front().unit.id == a,
               "shared MDBX connection rejected a valid snapshot");
        std::optional<ExactIndexGenerationSnapshot> expired_snapshot;
        {
            const auto separate_db = path / "separate.mdbx";
            const auto separate_cas = path / "separate-cas";
            auto separate_context = MdbxStorageContext::open(separate_db.string());
            create(separate_context, separate_cas);
            MdbxExactIndexGenerationStore separate_store(separate_context, "test");
            const auto separate_ids = ids(separate_context, d.scope_id);
            separate_store.begin("separate-first", d, 2);
            separate_store.append("separate-first", {
                {separate_ids[0], 1, {10, 0}}, {separate_ids[1], 1, {1, 0}}});
            separate_store.complete("separate-first"); separate_store.activate("separate-first", d);
            rejects(IndexGenerationFailure::Incompatible, [&] {
                (void)separate_store.query(*pinned, d.scope_id, {1, 0}, 8, allow);
            });
            expired_snapshot = separate_store.pin(d);
        }
        auto replacement_context = MdbxStorageContext::open((path / "separate.mdbx").string());
        MdbxExactIndexGenerationStore replacement_store(replacement_context, "test");
        rejects(IndexGenerationFailure::Incompatible, [&] {
            (void)replacement_store.query(*expired_snapshot, d.scope_id, {1, 0}, 8, allow);
        });
        mdbxc::KeyValueTable<std::string, std::string> canonical_rows(
            context->connection(), "agent_memory_knowledge_units");
        const auto row_a = canonical_rows.find(std::to_string(a.value()));
        const auto row_b = canonical_rows.find(std::to_string(b.value()));
        expect(row_a && row_b, "missing canonical rows for key-binding control");
        canonical_rows.insert_or_assign(std::to_string(a.value()), *row_b);
        store.begin("wrong-canonical-key", d, 1);
        rejects(IndexGenerationFailure::StaleBinding, [&] {
            store.append("wrong-canonical-key", {{a, 1, {1, 0}}});
        });
        canonical_rows.insert_or_assign(std::to_string(a.value()), *row_a);
        store.begin("atomic-batch", d, 2);
        rejects(IndexGenerationFailure::RebuildUnavailable, [&] {
            store.append("atomic-batch", {{a, 1, {10, 0}}, {b, 1, {}}});
        });
        rejects(IndexGenerationFailure::Incomplete, [&] { store.complete("atomic-batch"); });
        store.append("atomic-batch", {{a, 1, {10, 0}}, {b, 1, {1, 0}}});
        store.complete("atomic-batch");
        {
            auto binding_context = MdbxStorageContext::open((path / "binding.mdbx").string());
            const auto binding_cas = path / "binding-cas";
            create(binding_context, binding_cas);
            const auto regular = ids(binding_context, d.scope_id);
            MdbxRawMarkdownProductIngestion ingestion(ingestion_options(binding_context, binding_cas));
            const auto imported = ingestion.import(ObservedResource{d.scope_id, ResourceId{"resource:oversized"},
                "https://example.test/" + std::string(65536, 'x'), "# Oversized\n\nSource binding.\n",
                SourceTextOrigin::OriginalText, std::nullopt, 1}, 1);
            expect(imported.status == RawMarkdownProductImportStatus::Published,
                   "canonical ingestion rejected valid long URI");
            auto oversized = ids(binding_context, d.scope_id);
            oversized.erase(std::remove_if(oversized.begin(), oversized.end(), [&](KnowledgeUnitId id) {
                return std::find(regular.begin(), regular.end(), id) != regular.end();
            }), oversized.end());
            expect(regular.size() == 2 && oversized.size() == 1, "long-binding fixture identity mismatch");
            MdbxExactIndexGenerationStore binding_store(binding_context, "test");
            binding_store.begin("binding-limit", d, 2);
            rejects(IndexGenerationFailure::InvalidInput, [&] {
                binding_store.append("binding-limit", {
                    {regular[0], 1, {1, 0}}, {oversized[0], 1, {0, 1}}});
            });
            mdbxc::KeyValueTable<std::string, std::string> binding_rows(
                binding_context->connection(), "agent_memory_exact_index_generations");
            expect(!binding_rows.find(row_key("binding-limit")), "failed binding batch retained its first row");
            rejects(IndexGenerationFailure::Incomplete, [&] { binding_store.complete("binding-limit"); });
            binding_store.append("binding-limit", {{regular[0], 1, {1, 0}}, {regular[1], 1, {0, 1}}});
            binding_store.complete("binding-limit"); binding_store.activate("binding-limit", d);
            expect(binding_store.pin(d).size() == 2, "failed binding batch changed persisted progress");
        }
        rejects(IndexGenerationFailure::InvalidInput, [&] {
            store.append("oversized-batch", std::vector<ExactIndexVectorInput>(33));
        });
        expect(pinned->size() == 2 && pinned->source_frontier().size() == 32, "completion evidence missing");
        auto hits = store.query(*pinned, d.scope_id, {1, 0}, 1, allow);
        expect(hits.size() == 1 && hits[0].unit.id == a && hits[0].score == 10, "dot golden mismatch");
        hits = store.query(*pinned, d.scope_id, {0, 0}, 8, allow);
        expect(hits.size() == 2 && hits[0].unit.id < hits[1].unit.id && hits[0].score == 0 && hits[1].score == 0,
               "zero-score ties are not ordered by canonical unit identity");
        expect(store.query(*pinned, d.scope_id, {-1, 0}, 1, allow).front().unit.id == b,
               "negative dot golden mismatch");
        std::size_t zero_limit_authorize_calls = 0;
        const auto zero_limit_allow = [&zero_limit_authorize_calls](const KnowledgeUnitEnvelope&) {
            ++zero_limit_authorize_calls;
            return true;
        };
        expect(store.query(*pinned, d.scope_id, {1, 0}, 0, zero_limit_allow).empty(),
               "zero limit returned results");
        expect(zero_limit_authorize_calls == 0,
               "zero-limit query must not invoke authorization for any row");
        rejects(IndexGenerationFailure::InvalidInput, [&] {
            (void)store.query(*pinned, d.scope_id, {1, 0}, 0, {});
        });
        rejects(IndexGenerationFailure::Incompatible, [&] {
            (void)store.query(*pinned, ScopeId{"other"}, {1, 0}, 0, allow);
        });
        rejects(IndexGenerationFailure::Incompatible, [&] {
            (void)store.query(*pinned, d.scope_id, {1}, 0, allow);
        });
        hits = store.query(*pinned, d.scope_id, {1, 0}, 1,
                           [&](const KnowledgeUnitEnvelope& unit) { return unit.id != a; });
        expect(hits.size() == 1 && hits[0].unit.id == b, "filter was applied after top K");
        expect(store.query(*pinned, d.scope_id, {1, 0}, 8,
                            [](const KnowledgeUnitEnvelope&) { return false; }).empty(), "deny policy ignored");
        rejects(IndexGenerationFailure::InvalidInput, [&] { (void)store.query(*pinned, d.scope_id, {1, 0}, 1, {}); });
        rejects(IndexGenerationFailure::Incompatible, [&] { (void)store.query(*pinned, ScopeId{"other"}, {1, 0}, 1, allow); });
        rejects(IndexGenerationFailure::Incompatible, [&] { (void)store.query(*pinned, d.scope_id, {1}, 1, allow); });
        rejects(IndexGenerationFailure::Conflict, [&] { store.append("first", {{b, 1, {1, 0}}}); });
        const auto foreign = ids(context, ScopeId{"other"}).front();
        store.begin("wrong-scope", d, 1);
        rejects(IndexGenerationFailure::StaleBinding, [&] { store.append("wrong-scope", {{foreign, 1, {1, 0}}}); });
        rejects(IndexGenerationFailure::StaleBinding, [&] { store.append("wrong-scope", {{a, 9, {1, 0}}}); });
        for(int change = 0; change < 7; ++change) {
            auto incompatible = d;
            if(change == 0) incompatible.model_id += "-other";
            if(change == 1) incompatible.dimensions = 3;
            if(change == 2) incompatible.metric = "negative_squared_l2";
            if(change == 3) incompatible.codec = "lossy";
            if(change == 4) incompatible.schema_version = 2;
            if(change == 5) incompatible.scalar_bits = 8;
            if(change == 6) incompatible.recipe_id += "-other";
            rejects(IndexGenerationFailure::Incompatible, [&] { (void)store.pin(incompatible); });
            rejects(IndexGenerationFailure::Incompatible, [&] { store.activate("first", incompatible); });
            expect(store.pin(d).generation_id() == "first", "incompatible activation changed active pointer");
        }
        store.begin("identical", d, 2);
        store.append("identical", {{a, 1, {10, 0}}, {b, 1, {1, 0}}}); store.complete("identical");
        store.activate("identical", d);
        expect(store.pin(d).source_frontier() == pinned->source_frontier(), "identical rebuild frontier differs");
        auto alternative = d; alternative.model_id = "alternative-model"; alternative.metric = "negative_squared_l2";
        store.begin("alternative", alternative, 2);
        store.append("alternative", {{a, 1, {0, 0}}, {b, 1, {1, 0}}}); store.complete("alternative");
        store.set_before_activation_commit_hook_for_testing([] { throw std::runtime_error("injected commit fault"); });
        bool injected = false;
        try { store.activate("alternative", alternative); } catch(const std::runtime_error&) { injected = true; }
        expect(injected && store.pin(d).generation_id() == "identical", "activation fault advanced pointer");
        store.set_before_activation_commit_hook_for_testing({}); store.activate("alternative", alternative);
        expect(store.query(store.pin(alternative), d.scope_id, {1, 0}, 1, allow).front().unit.id == b,
               "negative L2 golden mismatch");
        expect(pinned->generation_id() == "first" && store.query(*pinned, d.scope_id, {1, 0}, 1, allow).front().unit.id == a,
               "activation changed pinned generation");
        store.activate("first", d);
        expect(snapshot(context, cas) == canonical, "lifecycle mutated canonical logical records/CAS");
    }
    pinned.reset();
    {
        auto context = MdbxStorageContext::open(db.string());
        MdbxExactIndexGenerationStore store(context, "test");
        auto first = store.pin(d); expect(first.generation_id() == "first", "reopen lost active generation");
        store.begin("retained-rebuild", d, first.size());
        store.append("retained-rebuild", first.retained_inputs()); store.complete("retained-rebuild");
        store.activate("retained-rebuild", d);
        expect(store.pin(d).source_frontier() == first.source_frontier(), "retained-input rebuild changed frontier");
        store.activate("first", d);
        rejects(IndexGenerationFailure::Incomplete, [&] { store.activate("wrong-scope", d); });
        expect(store.pin(d).generation_id() == "first", "failed activation changed selected generation");
        mdbxc::KeyValueTable<std::string, std::string> rows(context->connection(), "agent_memory_exact_index_generations");
        const auto original = rows.find(row_key("first")); expect(original.has_value(), "missing persisted test row");
        rows.insert_or_assign(row_key("first"), std::string("corrupt"));
        rejects(IndexGenerationFailure::Corrupt, [&] { (void)store.pin(d); });
        rejects(IndexGenerationFailure::Corrupt, [&] { store.activate("first", d); });
        rows.insert_or_assign(row_key("first"), *original);
        for(std::uint32_t bits : {0x7fc00000U, 0x7f800000U, 0xff800000U}) {
            auto nonfinite = *original;
            expect(nonfinite.size() >= 8, "retained FP32 payload is missing");
            for(unsigned i = 0; i < 4; ++i)
                nonfinite[nonfinite.size() - 4 + i] = static_cast<char>((bits >> (8U * i)) & 255U);
            rows.insert_or_assign(row_key("first"), nonfinite);
            rejects(IndexGenerationFailure::Corrupt, [&] { (void)store.pin(d); });
            rejects(IndexGenerationFailure::Corrupt, [&] { store.activate("first", d); });
        }
        rows.insert_or_assign(row_key("first"), *original);
        expect(rows.erase(row_key("first")), "retained row deletion failed");
        rejects(IndexGenerationFailure::Corrupt, [&] { (void)store.pin(d); });
        rejects(IndexGenerationFailure::Corrupt, [&] { store.activate("first", d); });
        rows.insert_or_assign(row_key("first"), *original);
        auto corrupted = *original; corrupted.back() ^= 1; rows.insert_or_assign(row_key("first"), corrupted);
        rejects(IndexGenerationFailure::Corrupt, [&] { (void)store.pin(d); });
        rows.insert_or_assign(row_key("first"), *original);
        expect(snapshot(context, cas) == canonical, "fault paths mutated canonical logical records/CAS");
        import(context, cas, d.scope_id, "resource:a", "# Alpha revision two\n\nNew retained source.\n", 2);
        const auto after_update = store.query(first, d.scope_id, {1, 0}, 8, allow);
        for(const auto& hit : after_update) expect(hit.unit.id != a, "A@1 survived A@2 publication");
        rejects(IndexGenerationFailure::StaleBinding, [&] { store.activate("first", d); });
        MdbxResourceManifestStorageOptions m; m.context = context; MdbxResourceManifestStorage manifests(m);
        const auto original_manifest = manifests.find_manifest(ResourceId{"resource:b"});
        expect(original_manifest.has_value(), "missing source manifest");
        expect(manifests.erase_manifest(ResourceId{"resource:b"}), "source manifest deletion failed");
        expect(store.query(first, d.scope_id, {1, 0}, 8, allow).empty(), "deleted source remained admissible");
        manifests.upsert_manifest(*original_manifest);
        expect(store.query(first, d.scope_id, {1, 0}, 8, allow).size() == 1, "source restore did not restore admission");
        MdbxKnowledgeUnitStoreOptions u; u.context = context; MdbxKnowledgeUnitStore units(u);
        KnowledgeUnitMutablePatch patch; patch.lifecycle_state = KnowledgeUnitLifecycleState::Deprecated;
        (void)units.update_mutable_fields(b, patch);
        expect(store.query(first, d.scope_id, {1, 0}, 8, allow).empty(), "inactive unit remained admissible");
        expect(units.erase(b), "failed to erase source unit");
        expect(store.query(first, d.scope_id, {1, 0}, 8, allow).empty(), "erased unit remained admissible");
        std::cout << "PASS controls=" << controls << " canonical_snapshot_sha256=" << hash(canonical) << '\n';
    }
    const char* keep_test_data = std::getenv("AGENT_MEMORY_KEEP_EXACT_GENERATION_TEST_DATA");
    if(!keep_test_data || std::string(keep_test_data) != "1") {
        std::error_code cleanup_error;
        std::filesystem::remove_all(path, cleanup_error);
        if(cleanup_error) throw std::system_error(cleanup_error, "failed to remove exact-generation test data");
    }
    return 0;
} catch(const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
