# ADR: Durable Prompt Assembly Profile History

**Status:** Accepted for the experimental profile-storage slice

## Context

`PromptAssemblyProfile` is a versioned composition policy, not canonical
content. The pure compiler must receive an exact profile revision and must not
select or activate one implicitly. The repository has durable stores for
canonical documents, resources and knowledge units, but none of those stores
can represent the profile's ordered selection rules, variant map and renderer
limits without changing their identity contracts. Reusing a document or
metadata row would conflate profile revision with document revision and would
make reopen and corruption checks ambiguous.

## Decision

Add an opt-in MDBX adapter with two prefix-scoped tables:

- `<prefix>_prompt_profiles`: immutable, versioned payloads keyed by
  `(profile_id, revision)`;
- `<prefix>_prompt_profile_heads`: the highest published revision per profile
  identity.

Payloads carry an explicit wire version, bounded length-prefixed fields and all
selection, variant and limit fields. A repeated identical write is idempotent;
the same identity/revision with a different payload fails closed. Historical
rows are retained and exact reads never fall back to a newer revision.

The adapter owns storage integrity only. It does not authorize a caller,
validate canonical-document freshness, choose a profile, or activate one. The
caller must perform those checks before passing a profile to `compile_prompt()`.

## Consequences

The physical profile DBI is an experimental, additive schema and is not a
release gate for M0/KB-0. Reopen, exact-history, malformed-wire, orphan-head
and identity-mismatch tests are required before this slice can be considered
usable. A future production profile may reuse these contracts only after a
separate admission/activation design proves the authority boundary.
