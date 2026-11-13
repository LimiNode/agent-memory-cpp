# Sovetwave include-policy and dependency-boundary audit

Status: research-only Draft PR. This note records a bounded audit and a
lexical-subsystem pilot. It does not change production include lines, public
API names, install rules or dependency selection.

## Provenance and host boundary

The project-local Sovetwave integration was installed from the upstream
release [`v0.5.0-beta.4`](https://github.com/LimiNode/sovetwave/releases/tag/v0.5.0-beta.4).
The inspected upstream `SKILL.md` blob is
`92beec2728508b1feae39e6b2f0311473bcc4d42`. The host copy lives under the
ignored local `.codex/skills/sovetwave` directory; it is not vendored into the
library and is not a runtime or build dependency. Project code, comments,
commits and public documentation continue to use the repository's normal
professional language.

Sovetwave is used here as an evidence-first engineering aid. The repository
guides remain normative; this note does not duplicate the upstream manual or
extend `AGENTS.md` with a second policy layer.

## Question and non-goals

The question is whether a hybrid include convention is justified for this
brownfield C++17 static library:

- matching-header-first for independently compiled `.cpp` units;
- owning-domain or subdomain aggregate headers for public cross-module use;
- direct leaf includes in focused implementation and test translation units
  when they reduce coupling.

The audit does **not** authorize a repository-wide include rewrite, a new
header hierarchy, a header-only conversion, an MDBX dependency change, or a
production retrieval change. A later implementation PR must first show a
measured benefit and preserve installed-consumer and self-sufficiency
contracts.

## Header and translation-unit classification

The inventory was taken at source commit `343985bb0743a3984b2a0914c14816a190675834`.
The current install rule treats every `*.hpp`, `*.h` and `*.ipp` below
`src/agent_memory/` as installable, so `public` below means public by the
current package boundary, not necessarily an intended API promise.

| Class | Count | Current examples and interpretation |
|---|---:|---|
| Top-level public umbrella | 1 | `src/agent_memory.hpp`; complete library aggregate |
| Domain umbrellas | 13 | `chat.hpp`, `domain.hpp`, `index.hpp`, `lexical.hpp`, `retrieval.hpp`, `storage.hpp`, and the other direct domain aggregates |
| Dedicated nested subdomain umbrellas | 0 | `lexical/` and `retrieval/` currently expose domain aggregates rather than a second nested umbrella layer |
| Standalone public leaves | 92 | Domain, index, retrieval and storage leaf headers after umbrella/optional/internal classification |
| Optional infrastructure entry points | 26 | `src/agent_memory/infrastructure/mdbx/*.hpp`; available only for MDBX-enabled adapters |
| Private implementation headers | 1 | `storage/CanonicalContentStoreInternal.hpp`; it is marked backend-only but is currently installed by the broad glob |
| Independently compiled `.cpp` units | 112 | Includes the dependency-free core and optional adapter sources |

This classification argues for a hybrid policy, not for one universal include
style. The one private bridge header is a concrete packaging follow-up: a
future focused PR should decide whether to narrow the install allowlist or to
move the bridge behind a non-installed implementation boundary. This Draft PR
does not make that potentially compatibility-affecting change.

## Existing policy and CMake/dependency audit

The existing `guides/coding-style.md` already establishes the main boundary:
matching project header first in `.cpp` files, aggregate headers for user-facing
cross-module code, and narrow leaf includes for focused implementation/tests.
The following source checks confirm how that policy meets the build graph.

| Boundary | Observed implementation | Result |
|---|---|---|
| Parent-provided targets | `cmake/AgentMemoryDependencies.cmake` checks known MDBX target names before adding source dependencies | Parent projects can provide MDBX without a nested checkout |
| Flat source dependencies | `external/libmdbx` and `external/mdbx-containers` are siblings; `external/simdcomp` is benchmark-only | Matches the flat-submodule rule in `guides/dependencies.md` |
| Optional modes | MDBX, Zstd and JSON are independently gated by `AGENT_MEMORY_ENABLE_*`; `AGENT_MEMORY_MDBX_DEPS_MODE` is validated as `AUTO|SYSTEM|BUNDLED` and forwarded to MDBX containers | Core contracts remain dependency-free; adapters own optional includes |
| Public/private linkage | Optional MDBX and JSON targets are linked through private interface dependencies | Optional implementation code does not become a public header requirement by default |
| Installed/exported package | `install(TARGETS agent_memory EXPORT agent_memoryTargets)` and `agent_memoryConfigVersion.cmake` are generated; a Release/MDBX-off install exported `agent_memory::agent_memory` with install-prefix include paths | Installed consumer boundary is present and must remain part of any include pilot |
| Header installation | `install(DIRECTORY src/agent_memory FILES_MATCHING *.hpp *.h *.ipp)` installs all matching headers | The internal bridge leakage above is real and should be handled separately |

No new dependency target, include directory, submodule or runtime component is
needed for this audit.

## Lexical bounded pilot

### Method

The pilot compared the current matching-header-first source with a temporary
copy in which only the first project include of each selected lexical `.cpp`
file was replaced by `<agent_memory/lexical.hpp>`. Production files were not
modified. Both variants were preprocessed with GNU C++ 15.2.0, `-std=c++17`,
`-I src`, and `-E -P`; all seven temporary compilations completed with exit
code 0. The byte count is the preprocessed translation-unit size, not a claim
about wall-clock build time. A successful `-E -P` run proves preprocessing and
include expansion for that temporary translation unit only; it does not prove
successful object compilation, linking, ODR cleanliness or a percentage change
in end-to-end build time.

As a static fan-out baseline, `lexical.hpp` names 17 project headers directly;
the 17 lexical leaf headers have a median of one direct project include, a
maximum of four, and 21 in total. The selected `.cpp` files keep their matching
header as the first project include. These counts describe direct textual
edges, not the complete transitive include graph.

| Translation unit | Matching header first | Umbrella first | Change |
|---|---:|---:|---:|
| `ExactLexicalIndex.cpp` | 1,619,538 B | 1,714,547 B | +5.9% |
| `FlatLexicalIndex.cpp` | 1,475,368 B | 1,583,098 B | +7.3% |
| `KeywordOverlapIndex.cpp` | 1,400,137 B | 1,496,277 B | +6.9% |
| `Lexical.cpp` | 1,398,626 B | 1,527,496 B | +9.2% |
| `PersistentLexicalIndex.cpp` | 1,324,022 B | 1,525,108 B | +15.2% |
| `StandardTokenizer.cpp` | 1,156,979 B | 1,496,483 B | +29.4% |
| `TokenDictionary.cpp` | 1,185,045 B | 1,313,683 B | +10.9% |

The pilot is directional: it measures preprocessing, not a repeated clean or
incremental build. It nevertheless gives a concrete result for this subsystem:
the umbrella-first substitution increases the amount of parsed text for every
selected translation unit. There is no evidence to replace the current
matching-header-first default for implementation files.

### Required next measurement before any source rewrite

A follow-up pilot may add no more than one isolated lexical change set and must
record, on the same machine and compiler:

1. repeated clean-build and one-file incremental-build wall time;
2. preprocessed bytes and direct project-include fan-out;
3. include-order failures and public-header self-sufficiency results;
4. an installed consumer using the exported `agent_memory::agent_memory`
   target;
5. MDBX-off and MDBX-on configuration where the touched headers are in the
   optional adapter path.

The change is admissible only if the measurements show a reproducible benefit,
the public headers remain self-sufficient, and the installed consumer remains
valid. Otherwise retain the current convention and close the experiment as
negative evidence.

## Decision and follow-up

This PR records the host-local Sovetwave integration, the header classification,
the concrete CMake/dependency audit and the lexical preprocessing result. It
does not change production includes or package contents. The next separate PR,
if needed, is the install-allowlist correction for the backend-only bridge;
only after that boundary is settled should a source pilot be considered.
