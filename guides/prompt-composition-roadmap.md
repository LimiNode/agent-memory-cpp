# Prompt Composition Roadmap

**Status:** Phase A implemented as a pure, opt-in C++17 compiler. Durable
profile storage and consumer integration are not implemented.

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
- canonical preorder of included block IDs;
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
admission, retention and authorization remain outside this layer.

The compiler fails closed for malformed profiles, duplicate selections,
non-preorder documents, missing bindings, malformed templates, non-finite
values, ambiguous variants and resource-limit exhaustion. It returns no
partially rendered prompt on failure.

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

The fixture is intentionally in-memory. MDBX profile persistence is a
follow-up slice and must reuse the existing canonical-content revision/history
mechanisms rather than introducing a second block tree or document revision
system.

## Phase B: durable profiles (not implemented)

The next PR may persist versioned profiles and dictionaries using an existing
profile/metadata mechanism if it can prove reopen, exact revision binding,
corruption rejection and historical replay without a new DBI. It must not
activate a profile automatically, replace host policy, or turn inactive
variants into deletions. If the existing mechanisms cannot represent the
profile without ambiguity, record an ADR before adding physical schema.

## Non-goals

Prompt composition does not provide semantic search, summarization, model
inference, translation by an LLM, evolution search, access control, workflow
execution or ADELIA integration. Those consumers may propose a profile or a
candidate, but only their own governed policy can approve activation.
