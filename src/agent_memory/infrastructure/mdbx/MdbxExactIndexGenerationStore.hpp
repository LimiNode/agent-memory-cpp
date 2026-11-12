#pragma once
#ifndef AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_EXACT_INDEX_GENERATION_STORE_HPP_INCLUDED
#define AGENT_MEMORY_HEADER_INFRASTRUCTURE_MDBX_MDBX_EXACT_INDEX_GENERATION_STORE_HPP_INCLUDED

#include <agent_memory/domain/SearchProjection.hpp>

#include <functional>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

#if AGENT_MEMORY_HAS_MDBX
#include "MdbxStorageContext.hpp"

namespace agent_memory {

/// Immutable space identity. See guides/index-lifecycle-roadmap.md,
/// "Durable exact generation compatibility profile" for persistence semantics.
struct ExactIndexSpaceDescriptor final {
    std::uint32_t schema_version = 1;
    std::string model_id;
    std::string recipe_id;
    std::uint32_t dimensions = 0;
    std::uint32_t scalar_bits = 32;
    std::string metric = "dot";
    std::string normalization = "none";
    std::string codec = "fp32-le-v1";
    ScopeId scope_id;
    ProjectionKind projection_kind = ProjectionKind::Original;
};

enum class IndexGenerationFailure {
    NotFound, Incomplete, Incompatible, Corrupt, StaleBinding, Conflict,
    RebuildUnavailable, InvalidInput
};

/// Expected lifecycle failures are typed; MDBX/storage failures propagate.
class IndexGenerationError final : public std::runtime_error {
public:
    IndexGenerationError(IndexGenerationFailure code, const std::string& message);
    [[nodiscard]] IndexGenerationFailure code() const noexcept { return m_code; }
private:
    IndexGenerationFailure m_code;
};

/// Producer supplies explicit canonical identity, never a vector position.
/// An empty vector means exact retained input is unavailable, not a zero vector.
struct ExactIndexVectorInput final {
    KnowledgeUnitId unit_id;
    std::uint64_t unit_revision = 0;
    std::vector<float> values;
};

struct ExactIndexGenerationData;

/// Immutable completed generation copy; activation cannot change pinned data.
/// This does NOT pin canonical truth across separate queries.
class ExactIndexGenerationSnapshot final {
public:
    [[nodiscard]] const std::string& generation_id() const;
    [[nodiscard]] const ExactIndexSpaceDescriptor& descriptor() const;
    [[nodiscard]] const std::string& source_frontier() const;
    [[nodiscard]] std::size_t size() const;
    /// Exact FP32 retained inputs, still bound to descriptor().model_id/recipe_id.
    /// Use for compatible rebuild; never relabel as output of a different model.
    /// Append revalidates canonical freshness when these inputs are reused.
    [[nodiscard]] std::vector<ExactIndexVectorInput> retained_inputs() const;
private:
    explicit ExactIndexGenerationSnapshot(std::shared_ptr<const ExactIndexGenerationData> data);
    std::shared_ptr<const ExactIndexGenerationData> m_data;
    friend class MdbxExactIndexGenerationStore;
};

/// Admitted envelope remains canonical; no CAS hydration is implied.
struct ExactIndexHit final {
    KnowledgeUnitEnvelope unit;
    double score = 0;
};

/// Bounded, opt-in derived generation lifecycle with a persisted exact backend.
/// All state is independent of canonical records. Only pointer selection is
/// claimed atomic. Calls own their MDBX transactions; snapshots are copyable.
/// Context must outlive this store; host authorization is required per query.
class MdbxExactIndexGenerationStore final {
public:
    MdbxExactIndexGenerationStore(std::shared_ptr<MdbxStorageContext> context,
                                  std::string index_name,
                                  std::string table_prefix = "agent_memory");
    ~MdbxExactIndexGenerationStore();
    MdbxExactIndexGenerationStore(const MdbxExactIndexGenerationStore&) = delete;
    MdbxExactIndexGenerationStore& operator=(const MdbxExactIndexGenerationStore&) = delete;

    /// Identical begin is idempotent; conflicting reuse fails. Count is 1..1024.
    void begin(const std::string& generation, const ExactIndexSpaceDescriptor& descriptor,
               std::size_t expected_count);
    /// Appends 1..32 unique units; binds current canonical evidence in one txn.
    void append(const std::string& generation, const std::vector<ExactIndexVectorInput>& inputs);
    /// Validates exact cardinality/bindings and persists an immutable manifest.
    void complete(const std::string& generation);
    /// Validates completion, descriptor and current bindings before selection.
    /// Also implements explicit rollback to a retained compatible generation.
    void activate(const std::string& generation, const ExactIndexSpaceDescriptor& expected);
    /// Fails closed on missing, incomplete, incompatible or corrupt generation.
    [[nodiscard]] ExactIndexGenerationSnapshot pin(const ExactIndexSpaceDescriptor& expected) const;
    /// Exhaustive bounded exact scan; scope, current admission and host policy
    /// are checked BEFORE scoring/top-K. Missing policy is InvalidInput.
    [[nodiscard]] std::vector<ExactIndexHit> query(
        const ExactIndexGenerationSnapshot& snapshot, const ScopeId& scope,
        const std::vector<float>& vector, std::size_t limit,
        const std::function<bool(const KnowledgeUnitEnvelope&)>& authorize) const;

    /// Throws immediately before pointer commit, for rollback fault controls.
    void set_before_activation_commit_hook_for_testing(std::function<void()> hook);
private:
    class Impl;
    std::unique_ptr<Impl> m_impl;
};

} // namespace agent_memory
#endif
#endif
