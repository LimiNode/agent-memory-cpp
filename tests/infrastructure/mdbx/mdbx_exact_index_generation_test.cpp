#include <agent_memory/infrastructure/mdbx/MdbxExactIndexGenerationStore.hpp>
#include <agent_memory/infrastructure/mdbx/MdbxResourceManifestStorage.hpp>
#include "exact_generation_fixture.hpp"

#include <chrono>

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
    std::filesystem::create_directories(path);
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
        rejects(IndexGenerationFailure::Incomplete, [&] { store.activate("first", d); });
        store.append("first", {{a, 1, {10, 0}}});
        rejects(IndexGenerationFailure::Incomplete, [&] { store.complete("first"); });
        rejects(IndexGenerationFailure::RebuildUnavailable, [&] { store.append("first", {{b, 1, {}}}); });
        rejects(IndexGenerationFailure::Conflict, [&] { store.append("first", {{a, 1, {1, 0}}}); });
        store.append("first", {{b, 1, {1, 0}}}); store.complete("first"); store.complete("first");
        store.activate("first", d); pinned = store.pin(d);
        expect(pinned->size() == 2 && pinned->source_frontier().size() == 32, "completion evidence missing");
        auto hits = store.query(*pinned, d.scope_id, {1, 0}, 1, allow);
        expect(hits.size() == 1 && hits[0].unit.id == a && hits[0].score == 10, "dot golden mismatch");
        hits = store.query(*pinned, d.scope_id, {0, 0}, 8, allow);
        expect(hits.size() == 2 && hits[0].unit.id < hits[1].unit.id && hits[0].score == 0 && hits[1].score == 0,
               "zero-score ties are not ordered by canonical unit identity");
        expect(store.query(*pinned, d.scope_id, {-1, 0}, 1, allow).front().unit.id == b,
               "negative dot golden mismatch");
        expect(store.query(*pinned, d.scope_id, {1, 0}, 0, allow).empty(), "zero limit returned results");
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
    // Preserve this small test database as failure/reopen evidence; no cleanup.
    return 0;
} catch(const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
