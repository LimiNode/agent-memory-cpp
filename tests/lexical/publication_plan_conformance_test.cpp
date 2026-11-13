#include <agent_memory/lexical/PublicationPlan.hpp>

#include <cstdint>
#include <iostream>
#include <limits>
#include <string>
#include <utility>

namespace {

    using namespace agent_memory;

    int fail(const char* message) {
        std::cerr << message << '\n';
        return 1;
    }

    PublicationProfileIdentity profile(const ScopeId& scope) {
        return PublicationProfileIdentity{scope, "coordinated-mdbx-v1", 1};
    }

    PublicationFencingIdentity fencing(
        const char* owner,
        const std::uint64_t epoch,
        const char* digest
    ) {
        return PublicationFencingIdentity{owner, epoch, digest};
    }

    PublicationEvidence evidence(
        const char* resource,
        const std::uint64_t generation,
        const std::uint64_t unit_revision,
        const char* digest
    ) {
        return PublicationEvidence{
            ResourceId{resource},
            generation,
            generation,
            unit_revision,
            ProjectionVersionRef{
                unit_revision,
                generation,
                generation,
                "original-v1"
            },
            ResourceManifestSchema{"resource-manifest-v6", 6},
            generation,
            ResourceManifestState::Active,
            6,
            digest
        };
    }

    PublicationPlan plan(
        const ScopeId& scope,
        const PublicationSequence expected_sequence,
        const char* publication_id,
        const char* payload_digest,
        const PublicationFencingIdentity& owner,
        PublicationEvidence expected,
        PublicationEvidence target
    ) {
        return PublicationPlan{
            PublicationIdentity{scope, publication_id},
            PublicationScopeFrontier{scope, expected_sequence},
            profile(scope),
            owner,
            std::move(expected),
            std::move(target),
            PublicationPayloadIdentity{
                "resource-publication",
                payload_digest,
                "canonical+unit+projection+lexical+manifest"
            }
        };
    }

    int test_value_validation_and_admission() {
        const ScopeId scope{"scope:alpha"};
        const auto owner = fencing("writer-a", 1, "token-a");
        PublicationConformanceModel model{scope, owner};

        if(!model.valid()) {
            return fail("valid scope and fencing must create a valid model");
        }

        if(model.has_admitted_profile()) {
            return fail("model must start without an admitted profile");
        }
        const auto first_admission = model.admit_profile(profile(scope));
        const auto replay_admission = model.admit_profile(profile(scope));
        if(first_admission.code != PublicationAdmissionCode::Admitted
            || replay_admission.code != PublicationAdmissionCode::AlreadyAdmitted) {
            return fail("profile admission must be idempotent");
        }

        auto conflicting = profile(scope);
        conflicting.profile_id = "other-profile";
        if(model.admit_profile(conflicting).code != PublicationAdmissionCode::ProfileConflict) {
            return fail("a second profile must not replace the admitted profile");
        }

        auto invalid_profile = profile(scope);
        invalid_profile.profile_version = 0;
        if(model.admit_profile(invalid_profile).code != PublicationAdmissionCode::InvalidProfile) {
            return fail("invalid profile identity must fail closed");
        }

        if(model.rotate_fencing(owner).code != PublicationAdmissionCode::AlreadyAdmitted) {
            return fail("identical fencing rotation must be an idempotent no-op");
        }
        if(model.rotate_fencing(fencing("writer-a", 1, "token-old")).code
                != PublicationAdmissionCode::FencingConflict
            || model.rotate_fencing(fencing("writer-a", 2, "token-b")).code
                != PublicationAdmissionCode::Admitted
            || model.rotate_fencing(owner).code != PublicationAdmissionCode::FencingConflict) {
            return fail("fencing epoch must advance monotonically and reject rollback");
        }

        if(!is_valid_publication_fencing(owner)
            || !is_valid_publication_payload(PublicationPayloadIdentity{
                "payload", "digest", "canonical"
            })) {
            return fail("valid ownership and payload identities must be admitted");
        }
        return 0;
    }

    int test_invalid_constructor_and_profile_guard() {
        const auto owner = fencing("writer-a", 1, "token-a");
        PublicationConformanceModel invalid_scope{ScopeId{}, owner};
        PublicationConformanceModel invalid_owner{
            ScopeId{"scope:invalid"},
            PublicationFencingIdentity{}
        };
        if(invalid_scope.valid() || invalid_owner.valid()
            || invalid_scope.admit_profile(profile(ScopeId{"scope:invalid"})).code
                != PublicationAdmissionCode::InvalidModel
            || invalid_owner.rotate_fencing(owner).code
                != PublicationAdmissionCode::InvalidModel) {
            return fail("invalid constructor state must remain unusable");
        }
        return 0;
    }

    int test_commit_replay_and_scope_cas() {
        const ScopeId scope{"scope:alpha"};
        const auto owner = fencing("writer-a", 1, "token-a");
        PublicationConformanceModel model{scope, owner};
        const auto admission = model.admit_profile(profile(scope));
        if(admission.code != PublicationAdmissionCode::Admitted) {
            return fail("initial profile admission must succeed");
        }
        const auto target = evidence("resource:alpha", 1, 1, "binding-a1");

        const auto first = plan(
            scope,
            PublicationSequence{},
            "publication-1",
            "payload-a1",
            owner,
            PublicationEvidence{},
            target
        );
        const auto committed = model.publish(first);
        if(committed.code != PublicationOutcomeCode::Committed
            || !committed.receipt.has_value()
            || committed.receipt->committed_frontier.sequence.value() != 1
            || model.frontier().sequence.value() != 1) {
            return fail("first publication must allocate sequence one");
        }

        const auto replay = model.publish(first);
        if(replay.code != PublicationOutcomeCode::Replayed
            || !replay.receipt.has_value()
            || !replay.receipt->replay
            || replay.receipt->committed_frontier.sequence.value() != 1
            || model.frontier().sequence.value() != 1) {
            return fail("same publication must replay without advancing sequence");
        }

        auto altered_precondition = first;
        altered_precondition.expected_frontier.sequence = PublicationSequence{1};
        if(model.publish(altered_precondition).code
                != PublicationOutcomeCode::PublicationIdentityConflict) {
            return fail("replay identity must include the original expected frontier");
        }

        const auto stale = plan(
            scope,
            PublicationSequence{},
            "publication-2",
            "payload-a2",
            owner,
            PublicationEvidence{},
            evidence("resource:beta", 1, 1, "binding-b1")
        );
        if(model.publish(stale).code != PublicationOutcomeCode::StaleFrontier
            || model.frontier().sequence.value() != 1) {
            return fail("a second plan from the old frontier must be rejected");
        }

        const auto update = plan(
            scope,
            PublicationSequence{1},
            "publication-2",
            "payload-a2",
            owner,
            target,
            evidence("resource:alpha", 2, 2, "binding-a2")
        );
        if(model.publish(update).code != PublicationOutcomeCode::Committed
            || model.frontier().sequence.value() != 2) {
            return fail("a plan with the current frontier must commit exactly once");
        }
        if(model.publish(first).code != PublicationOutcomeCode::Replayed) {
            return fail("identical replay must survive later unrelated publication");
        }
        return 0;
    }

    int test_conflicts_and_atomic_rejection() {
        const ScopeId scope{"scope:alpha"};
        const auto owner = fencing("writer-a", 1, "token-a");
        PublicationConformanceModel model{scope, owner};
        const auto admission = model.admit_profile(profile(scope));
        if(admission.code != PublicationAdmissionCode::Admitted) {
            return fail("conflict test profile admission must succeed");
        }
        const auto target = evidence("resource:alpha", 1, 1, "binding-a1");
        const auto first = plan(
            scope,
            PublicationSequence{},
            "publication-1",
            "payload-a1",
            owner,
            PublicationEvidence{},
            target
        );
        const auto initial = model.publish(first);
        if(initial.code != PublicationOutcomeCode::Committed) {
            return fail("conflict test initial publication must succeed");
        }

        auto changed_payload = first;
        changed_payload.payload.payload_digest = "tampered";
        if(model.publish(changed_payload).code
                != PublicationOutcomeCode::PublicationIdentityConflict
            || model.frontier().sequence.value() != 1) {
            return fail("same identity with another payload must not mutate state");
        }

        auto wrong_owner = first;
        wrong_owner.fencing = fencing("writer-b", 2, "token-b");
        if(model.publish(wrong_owner).code != PublicationOutcomeCode::FencingMismatch) {
            return fail("a stale or foreign fencing identity must be rejected");
        }

        auto bad_binding = plan(
            scope,
            PublicationSequence{1},
            "publication-2",
            "payload-a2",
            owner,
            evidence("resource:alpha", 1, 1, "wrong-binding"),
            evidence("resource:alpha", 2, 2, "binding-a2")
        );
        if(model.publish(bad_binding).code != PublicationOutcomeCode::BindingMismatch
            || model.frontier().sequence.value() != 1) {
            return fail("wrong expected binding must leave the prior frontier intact");
        }

        auto wrong_profile = first;
        wrong_profile.identity.publication_id = "publication-profile-mismatch";
        wrong_profile.profile.profile_id = "other-profile";
        if(model.publish(wrong_profile).code != PublicationOutcomeCode::ProfileMismatch) {
            return fail("publish must reject a non-admitted profile");
        }

        auto invalid_manifest = first;
        invalid_manifest.identity.publication_id = "publication-invalid-manifest";
        invalid_manifest.target_evidence.manifest_state =
            static_cast<ResourceManifestState>(255);
        if(model.publish(invalid_manifest).code != PublicationOutcomeCode::InvalidPlan) {
            return fail("invalid manifest state must fail closed");
        }

        auto wrong_canonical = plan(
            scope,
            PublicationSequence{1},
            "publication-wrong-canonical",
            "payload-a2",
            owner,
            target,
            evidence("resource:alpha", 1, 2, "binding-a2")
        );
        wrong_canonical.target_evidence.canonical_revision = 0;
        if(model.publish(wrong_canonical).code != PublicationOutcomeCode::InvalidPlan) {
            return fail("missing canonical revision must fail closed");
        }

        auto invalid = first;
        invalid.identity.publication_id = "publication-invalid";
        invalid.target_evidence = PublicationEvidence{};
        if(model.publish(invalid).code != PublicationOutcomeCode::InvalidPlan
            || model.frontier().sequence.value() != 1) {
            return fail("invalid target evidence must be rejected atomically");
        }

        const auto before_binding = model.binding(target.resource_id);
        model.fail_next_commit_for_testing();
        const auto failed_update = plan(
            scope,
            PublicationSequence{1},
            "publication-2",
            "payload-a2",
            owner,
            target,
            evidence("resource:alpha", 2, 2, "binding-a2")
        );
        if(model.publish(failed_update).code != PublicationOutcomeCode::CommitFailure
            || model.frontier().sequence.value() != 1
            || !model.binding(target.resource_id).has_value()
            || !before_binding.has_value()
            || model.binding(target.resource_id)->manifest_binding_digest
                != before_binding->manifest_binding_digest
            || model.binding(target.resource_id)->resource_generation
                != before_binding->resource_generation
            || model.receipt("publication-2").has_value()) {
            return fail("commit-stage failure must leave every model state component unchanged");
        }
        if(model.publish(failed_update).code != PublicationOutcomeCode::Committed
            || model.frontier().sequence.value() != 2
            || !model.receipt("publication-2").has_value()) {
            return fail("a failed commit must be retryable without a partial mutation");
        }
        return 0;
    }

    int test_scope_profile_and_overflow_guards() {
        const ScopeId scope{"scope:alpha"};
        const auto owner = fencing("writer-a", 1, "token-a");
        PublicationConformanceModel model{
            scope,
            owner,
            PublicationSequence{std::numeric_limits<std::uint64_t>::max()}
        };
        const auto admission = model.admit_profile(profile(scope));
        if(admission.code != PublicationAdmissionCode::Admitted) {
            return fail("overflow test profile admission must succeed");
        }

        const auto overflow = plan(
            scope,
            PublicationSequence{std::numeric_limits<std::uint64_t>::max()},
            "publication-overflow",
            "payload-overflow",
            owner,
            PublicationEvidence{},
            evidence("resource:alpha", 1, 1, "binding-a1")
        );
        if(model.publish(overflow).code != PublicationOutcomeCode::SequenceOverflow
            || model.frontier().sequence.value()
                != std::numeric_limits<std::uint64_t>::max()) {
            return fail("sequence exhaustion must fail closed without mutation");
        }

        PublicationConformanceModel other_scope{
            ScopeId{"scope:beta"},
            owner
        };
        const auto other_admission = other_scope.admit_profile(profile(ScopeId{"scope:beta"}));
        if(other_admission.code != PublicationAdmissionCode::Admitted) {
            return fail("other scope profile admission must succeed");
        }
        auto wrong_scope = overflow;
        wrong_scope.identity.scope_id = ScopeId{"scope:beta"};
        wrong_scope.expected_frontier.scope_id = ScopeId{"scope:beta"};
        wrong_scope.profile.scope_id = ScopeId{"scope:beta"};
        if(model.publish(wrong_scope).code != PublicationOutcomeCode::ScopeMismatch) {
            return fail("cross-scope publication must be rejected");
        }
        return 0;
    }

} // namespace

int main() {
    int failures = 0;
    failures += test_value_validation_and_admission();
    failures += test_invalid_constructor_and_profile_guard();
    failures += test_commit_replay_and_scope_cas();
    failures += test_conflicts_and_atomic_rejection();
    failures += test_scope_profile_and_overflow_guards();
    return failures == 0 ? 0 : 1;
}
