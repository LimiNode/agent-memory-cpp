#include "../infrastructure/mdbx/exact_generation_fixture.hpp"
#include <cstdlib>
#ifdef UPGRADE_NEW
#include <agent_memory/infrastructure/mdbx/MdbxExactIndexGenerationStore.hpp>
#endif

int main(int argc, char** argv) try {
    using namespace agent_memory;
    using namespace exact_fixture;
    if(argc != 5) throw std::runtime_error("usage: consumer create|upgrade|verify|interrupt|crash DB CAS SNAPSHOT");
    const std::string mode = argv[1];
    const std::filesystem::path db = argv[2], cas = argv[3], expected_path = argv[4];
    std::filesystem::create_directories(db.parent_path());
    std::string canonical;
    if(mode == "create") {
        expect(!std::filesystem::exists(db), "OLD must create a fresh database");
        auto context = MdbxStorageContext::open(db.string()); create(context, cas);
        canonical = snapshot(context, cas);
        expect(canonical.find("knowledge_units") != std::string::npos &&
               canonical.find("artifact") != std::string::npos &&
               canonical.find("CAS ") != std::string::npos, "OLD snapshot omitted canonical/provenance/CAS");
        // Opening the new table is deliberately not attempted by OLD.
        MDBX_dbi dbi; auto txn = context->connection()->transaction(mdbxc::TransactionMode::READ_ONLY);
        expect(mdbx_dbi_open(txn.handle(), "agent_memory_exact_index_generations", MDBX_DB_DEFAULTS, &dbi) == MDBX_NOTFOUND,
               "OLD database already contains derived generations"); txn.commit();
        std::ofstream output(expected_path, std::ios::binary); output << canonical;
        expect(output.good(), "could not write OLD snapshot");
    } else {
        std::ifstream input(expected_path, std::ios::binary);
        expect(input.good(), "missing OLD canonical snapshot");
        canonical.assign(std::istreambuf_iterator<char>(input), {});
        {
            auto context = MdbxStorageContext::open(db.string());
            expect(snapshot(context, cas) == canonical, "NEW open changed canonical records");
#ifdef UPGRADE_NEW
            if(mode == "upgrade") {
                ExactIndexSpaceDescriptor d; d.model_id = "compat-reference-space-v1";
                d.recipe_id = "sha256-primary-text-first-two-bytes-fp32-v1";
                d.scope_id = ScopeId{"alpha"}; d.dimensions = 2;
                MdbxExactIndexGenerationStore store(context, "upgrade");
                try { (void)store.pin(d); throw std::runtime_error("unexpected existing generation"); }
                catch(const IndexGenerationError& e) { expect(e.code() == IndexGenerationFailure::NotFound, "unexpected pin error"); }
                std::vector<ExactIndexVectorInput> vectors;
                MdbxKnowledgeUnitStoreOptions o; o.context = context; MdbxKnowledgeUnitStore units(o);
                for(const auto id : ids(context, d.scope_id)) {
                    const auto unit = units.find(id); expect(unit.has_value(), "missing canonical unit");
                    const auto digest = sha256(unit->primary_text);
                    vectors.push_back({unit->id, unit->revision, {float(digest[0]), float(digest[1])}});
                }
                expect(vectors.size() == 2, "OLD corpus cardinality changed");
                store.begin("generation-1", d, vectors.size()); store.append("generation-1", vectors);
                store.complete("generation-1"); store.activate("generation-1", d);
                const auto pinned = store.pin(d);
                const auto hits = store.query(pinned, d.scope_id, {1, 1}, 8,
                    [](const KnowledgeUnitEnvelope& unit) { return unit.scope_id == ScopeId{"alpha"}; });
                expect(hits.size() == 2 && hits[0].unit.sources.size() == 1, "source-bound exact query failed");
                store.begin("generation-2", d, vectors.size()); store.append("generation-2", vectors);
                store.complete("generation-2"); store.activate("generation-2", d);
                expect(pinned.generation_id() == "generation-1", "generation pin changed");
                store.activate("generation-1", d);
                expect(snapshot(context, cas) == canonical, "generation activation/rebuild/rollback changed canonical records");
            }
#else
            expect(mode == "verify", "OLD only supports create and verify");
#endif
        }
#ifdef UPGRADE_NEW
        {
            auto context = MdbxStorageContext::open(db.string());
            ExactIndexSpaceDescriptor d; d.model_id = "compat-reference-space-v1";
            d.recipe_id = "sha256-primary-text-first-two-bytes-fp32-v1";
            d.scope_id = ScopeId{"alpha"}; d.dimensions = 2;
            MdbxExactIndexGenerationStore store(context, "upgrade"); const auto selected = store.pin(d);
            expect(selected.generation_id() == "generation-1" && selected.size() == 2, "reopen lost selected generation");
            if(mode == "upgrade") {
                store.begin("retained-rebuild", d, selected.size());
                store.append("retained-rebuild", selected.retained_inputs()); store.complete("retained-rebuild");
                store.activate("retained-rebuild", d); store.activate("generation-1", d);
            }
            if(mode == "interrupt") {
                const auto id = ids(context, d.scope_id).front();
                store.begin("interrupted", d, 2); store.append("interrupted", {{id, 1, {1, 0}}});
                std::_Exit(24);
            }
            if(mode == "crash") {
                store.set_before_activation_commit_hook_for_testing([] { std::_Exit(23); });
                store.activate("generation-2", d);
                throw std::runtime_error("activation crash injection did not execute");
            }
            if(mode == "verify") {
                try { store.activate("interrupted", d); throw std::runtime_error("interrupted build activated"); }
                catch(const IndexGenerationError& e) {
                    expect(e.code() == IndexGenerationFailure::Incomplete, "interrupted build did not fail closed");
                }
                expect(store.pin(d).generation_id() == "generation-1", "incomplete build changed active pointer");
            }
            expect(store.query(selected, d.scope_id, {1, 1}, 8,
                                [](const KnowledgeUnitEnvelope&) { return true; }).size() == 2,
                   "reopened generation query failed");
            expect(snapshot(context, cas) == canonical, "reopen changed canonical rows/CAS");
        }
#endif
    }
    std::cout << "PASS mode=" << mode << " canonical_snapshot_sha256=" << hash(canonical) << '\n'; return 0;
} catch(const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
