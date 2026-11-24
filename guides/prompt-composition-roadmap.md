# Prompt Composition Roadmap

**Status:** Phase A implemented as a pure, opt-in C++17 compiler. A durable,
opt-in profile history adapter is implemented; consumer integration and
activation remain out of scope.

This guide owns deterministic assembly of a prompt-like text context from an
immutable `CanonicalDocumentRevision`. It is a projection over canonical
content, not a second document tree, a retrieval index, an authority layer or
an inference runtime.

## Boundary and source of truth

`CanonicalDocumentRevision` remains the only source of block text, stable
`ContentBlockId` values, parent relations and block revisions. Prompt
composition may omit blocks or render a bounded template, but it never mutates
the document and never deletes an inactive variant. `materialize_markdown()`
keeps its existing full-document semantics.

The dependency direction is:

```text
CanonicalDocumentRevision
        |
        +--> PromptAssemblyProfile + PromptRenderContext
                         |
                         +--> compile_prompt()
                                  |
                                  +--> CompiledPrompt + PromptAssemblyManifest
```

The compiler receives an already selected document revision. It does not read
`latest`, process environment, wall clock, time zone state, network services,
retrieval indexes or ADELIA state. A caller that needs a durable profile must
pin and validate that profile separately before calling the compiler.

Revision `0` is the valid initial canonical snapshot. The compiler accepts it
and binds the manifest to that exact document revision and to the revisions of
the selected blocks; it never substitutes a later snapshot.

## Phase A API

The public types are in `src/agent_memory/domain/PromptComposition.hpp`:

- `PromptAssemblyProfile` is an identity/revision-bearing set of declarative
  `PromptBlockSelection` rules and explicit variant values;
- `PromptRenderContext` carries locale, style, consumer profile and a map of
  typed `PromptTemplateBinding` values;
- `PromptCompileLimits` bounds template bytes, expansion count and result
  bytes;
- `CompiledPrompt` contains the rendered text and an immutable
  `PromptAssemblyManifest`;
- `PromptCompileStatus` makes stale/invalid/missing/ambiguous/budget failures
  observable without a partial result.

An omitted selection uses `PromptAssemblyProfile::default_enabled`. A selected
block can override that default and can be constrained by `locale`, `style` or
`consumer_profile`. Blocks are evaluated in canonical preorder. A disabled or
unselected parent excludes its complete subtree; a child cannot re-enable an
unreachable path.

Variants are explicit: a selection names a `variant_group` and
`variant_value`, while the profile supplies the selected value. The matching
alternative with the lowest declared priority is selected. Missing group
values and equal-priority matches fail with a typed status; the compiler never
chooses based on hash-map iteration order.

## Template contract

The only accepted template form is `{{key}}`, where `key` contains ASCII
letters, digits, `.`, `_` or `-` and is bounded in length. There are no
expressions, conditionals, includes, callbacks, `eval` or recursive expansion.
Values are supplied by the caller as `std::variant<std::string, bool,
std::int64_t, double>`. Non-finite floating-point values are rejected.

`TrustedControl` values are rendered in their canonical scalar form.
`UntrustedData` values are rendered as a deterministic quoted/escaped data
literal. This is a data-boundary aid, not an authority system: host policy must
still keep mandatory instructions and capability decisions outside the
template. `Code` blocks are literal by default and require an explicit
`allow_templates` selection to opt in.

Date, time and time-zone values are ordinary explicit bindings. The compiler
does not obtain a current time from the host.

## Provenance and replay

Every successful build records:

- document identity and exact revision;
- profile identity and exact revision;
- renderer revision and resource limits;
- canonical preorder of included block IDs and their exact block revisions;
- omission reasons;
- template binding keys (never the raw binding values);
- text and configuration SHA-256 digests.

The configuration digest includes the selected block IDs, selection flags,
conditions, variant choices and the values/trust classifications of bindings
actually used by the build. Thus two activation masks or two parameter sets
that produce the same text remain distinguishable. The text digest is computed
only over the final bytes.

The manifest deliberately does not store secrets or personal values. A caller
that needs stronger replay guarantees must retain the versioned binding source
under its own provenance policy and pass the same values again.

## Failure and freshness rules

The pure compiler has no store access and therefore cannot silently refresh a
stale document. A storage-facing adapter must compare the requested document
revision with the caller's pinned revision before invoking it, and must report
`StaleDocument` rather than substitute a newer revision. Durable profile
admission, retention and authorization remain outside this layer. The durable
profile adapter likewise does not select, authorize or activate a profile.

The compiler fails closed for malformed profiles, duplicate selections,
non-preorder documents, missing bindings, malformed templates, non-finite
values, ambiguous variants and resource-limit exhaustion. It returns no
partially rendered prompt on failure.

The result budget is enforced while literal text and template values are
appended, including untrusted-value escaping and inter-block separators. A
required selection whose condition or variant is not active is a profile error,
not a silent omission. `control_instruction` is recorded as profile metadata;
it is not an authority decision or a separate transport channel. A host that
needs distinct control and data messages must wrap the compiled result at its
own boundary.

## Required conformance cases

The Phase A test fixture covers:

1. `ru`/`en` locale alternatives;
2. independent profile inputs and byte-identical repeated compilation;
3. disabled-parent subtree suppression;
4. template substitution and deterministic escaping of untrusted data;
5. literal `Code` blocks;
6. malformed and missing bindings;
7. result-budget enforcement;
8. ambiguous variant rejection.

The compiler fixture remains in-memory. Durable profile persistence is tested
separately by the MDBX adapter and does not alter the compiler's pure-input
contract.

## Phase B: durable profiles

`IPromptAssemblyProfileStorage` and `MdbxPromptAssemblyProfileStorage` provide
an immutable, revision-addressable history for `PromptAssemblyProfile` values.
The MDBX adapter uses two tables under the configured prefix:

- `<prefix>_prompt_profiles` stores length-prefixed, versioned profile payloads
  keyed by `(profile_id, revision)`;
- `<prefix>_prompt_profile_heads` stores the highest published revision for
  each profile identity.

The adapter retains older revisions, supports exact revision reads and reopen,
and makes a repeated byte-identical write idempotent. A second payload for the
same identity and revision is rejected. Payload identity, malformed heads,
unsupported wire versions, truncated data, duplicate selections and orphan
heads fail closed. The adapter validates limits and selection invariants before
publishing any row.

This is storage-only infrastructure. It does not establish canonical content,
trusted frontier admission, freshness, authorization or automatic activation;
callers must perform those checks before invoking `compile_prompt()`. Profile
revision is deliberately separate from canonical document revision, and the
adapter introduces no second document tree or implicit revision substitution.
The additive schema decision and experimental scope are recorded in
[`guides/adr/2026-11-18-prompt-profile-history-storage.md`](adr/2026-11-18-prompt-profile-history-storage.md).

## Managed Memory Blocks / Keyed Memory Facade audit

The [memory-foundations audit](audits/2026-11-23-memory-foundations-corrective-audit.md#4-prompt--keyed-memory)
records follow-up requirements, not another implemented Phase. Investigate
Managed Memory Blocks as one high-level capability combining exact scoped keys
and governed context attachment. It is a facade over canonical references,
reusing block reads and expected-revision edits rather than duplicating text in
a new KV corpus. Reading a key is independent of authorization, shared attachment,
profile activation and prompt inclusion.

The [managed-block work order](audits/2026-11-23-memory-foundations-corrective-audit.md#41-managed-block-operations-and-independent-guarantees)
covers read/replace/append/delete/history, simple and explicit-control API
levels, shared-consumer rights and persistent/conditional/on-demand attachment.
Mutation protection and required context presence are independent trusted-host
policy decisions. Required content that is missing, denied or over budget must
cause an explicit refusal/error, not silent omission or an access bypass.
The host resolves freshness and pins revisions before invoking the compiler;
this follow-up does not introduce implicit `latest` reads or an agent runtime.

`required` is profile-relative and does not protect a block against every future
edit or activation choice; an unreachable subtree is excluded before descendant
required-condition/variant checks. Trusted host policy must govern proposed
changes separately.
`PromptPolicy`, `PromptCandidate` and `PromptGenome` are conceptual audit terms,
not existing public APIs. A genome, if needed, remains configuration over the
existing blocks/profiles. Storage/compiler success is not activation approval
or evidence that an evolutionary candidate improves behavior.

## Non-goals

Prompt composition does not provide semantic search, summarization, model
inference, translation by an LLM, evolution search, access control, workflow
execution or ADELIA integration. Those consumers may propose a profile or a
candidate, but only their own governed policy can approve activation.
