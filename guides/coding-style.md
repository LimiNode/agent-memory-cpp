# Coding Style

## Scope

These rules apply to C++ sources, headers, examples, tests, CMake files, and
project documentation. Prefer the local style in nearby files when it is more
specific.

## Naming

- Class, struct, and enum names use `PascalCase`.
- Methods and free functions use `snake_case`.
- Private and protected data members use `m_` plus `snake_case`.
- Boolean variables should start with `is`, `has`, `use`, or `enable`.
- Enum values use `PascalCase`.
- Public/configuration macros use the `AGENT_MEMORY_` prefix.

Getter methods may omit `get_` when they behave like property accessors, such as
`size()` or `empty()`. Use `get_` when the method performs computation or when
omitting the prefix would be misleading.

## File Names

- If a file contains one primary class, use `PascalCase`, for example
  `LibraryInfo.hpp`.
- If a file contains helpers, utilities, or aggregate includes, use
  `snake_case` or a clear aggregate name.
- Keep `.hpp` and `.cpp` files side by side under `src/`.

## Include Guards

- Use `#pragma once` and a non-reserved include guard for project-owned `.hpp`
  and `.h` headers.
- Guard names use `AGENT_MEMORY_HEADER_<PATH>_<FILE>_<EXT>_INCLUDED`.
- Examples:
  - `AGENT_MEMORY_HEADER_AGENT_MEMORY_HPP_INCLUDED`;
  - `AGENT_MEMORY_HEADER_CORE_LIBRARY_INFO_HPP_INCLUDED`;
  - `AGENT_MEMORY_HEADER_STORAGE_MDBX_STORAGE_HPP_INCLUDED`.
- Keep implementation fragments such as `.ipp` unguarded if they are included
  from guarded headers.
- Public/configuration macros should not reuse include-guard names.

## Includes

- In `.cpp` files, include the matching project header first.
- Keep project headers before external or standard library headers.
- Public project headers must not include dependencies through parent-directory
  paths such as `../` or `../../`. Use the public include root instead, for
  example `#include <agent_memory/domain.hpp>`.
- User-facing code should prefer top-level or subdomain aggregate headers such
  as `<agent_memory.hpp>` or `<agent_memory/index.hpp>`.
- Prefer subdomain/domain aggregate headers for cross-module public
  dependencies when a leaf header does not need a single narrow type.
- Leaf public headers may be included directly by implementation files and
  focused tests, but keep their cross-domain includes narrow. Use forward
  declarations plus the smallest required public header when a type is only
  used by reference or pointer in the header.
- `agent_memory_header_self_sufficiency_test` compiles every public header as
  the first include in its own translation unit; update or extend that test
  when changing public include conventions.
- Do not use `using namespace` in headers or implementation files.

## Formatting

- Use 4 spaces for indentation.
- Keep opening braces on the same line for namespaces, classes, functions, and
  control blocks.
- Keep lines around 100-120 columns where practical.
- Prefer simple control flow over clever expressions.

## Comments And Doxygen

- All comments and Doxygen text must be in English.
- Prefer triple-slash Doxygen comments for public APIs.
- Explain why the API has its shape and state the contract or invariant; do not
  translate obvious syntax into English.
- Add comments for non-obvious constraints, invariants, dependency boundaries,
  ownership rules, or algorithmic choices.

For contract-bearing public types and APIs, document the applicable parts of:

- semantic role and stable identity rules;
- ownership, lifetime and revision/version semantics;
- preconditions, postconditions, no-op and conflict behavior;
- atomicity and thread-safety;
- dependency boundaries and fail-closed behavior.

When the reason is materially architectural, name the stable repository guide
and section or an ADR. A reference at file, type or interface level may cover
several obvious members. Do not add roadmap links to trivial getters, obvious
constructors, every private helper or ordinary control flow.

Production comments must not use PR numbers, chat transcripts, temporary agent
prompts, Habr/blog posts, benchmark scratch notes or local filesystem paths as
normative authority. External sources belong in a guide's research/reference
section and remain evidence or inspiration; the repository's guide or ADR owns
the adopted contract.

Do not introduce a custom Doxygen tag merely to encode design provenance. Plain
stable paths and named sections are sufficient.

Use this tag order when a public API needs detailed documentation:

```text
\brief
\tparam
\param
\return
\throws
\pre
\post
\invariant
\complexity
\thread_safety
\note / \warning
```

## Tests

- Test names should describe the behavior under test.
- Avoid magic literals in tests when the value has domain meaning.
- Keep smoke tests small; add focused tests when real domain behavior appears.
