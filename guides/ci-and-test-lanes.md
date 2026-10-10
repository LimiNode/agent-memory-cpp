# CI and test lanes

This repository has two different kinds of validation:

- library correctness and storage compatibility;
- exploratory algorithms, benchmark harnesses and research receipts.

They remain in one repository, but they do not need the same trigger or runtime cost. The CI workflow therefore classifies a change before selecting its lanes.

## Current lanes

| Lane | Runs for | Main checks |
| --- | --- | --- |
| Documentation | Markdown, guides and documentation changes | whitespace, relative Markdown links, and DBI manifest validation when its inputs changed |
| Core | C++, public headers, tests, examples, CMake and shared build files | CMake build and CTest on Ubuntu, Windows and macOS |
| Storage | MDBX and storage implementation changes | MDBX C1 wiring and semantic tests on Ubuntu |
| Research | research tools, benchmark inputs, codec/ANN/MIH/NeuRoute changes | the relevant benchmark and self-test harnesses |
| Full research | manual dispatch and the weekly scheduled run | all research lanes plus the full research CTest registration |

The aggregate CI gate is the stable status for branch protection. A lane may be skipped only when the classifier says that the lane is unrelated to the change. If a path is unknown, the classifier enables every lane (fail closed).

A repository change to the workflow, build metadata, dependency manifest or another shared control file is also treated as cross-cutting and enables every lane.

## CTest registration

The root CMakeLists.txt contains a large collection of self-tests accumulated during codec and retrieval research. The baseline audit found 272 root-level add_test registrations; the ordinary C++ suite under tests/CMakeLists.txt contains 43 tests.

Research self-tests are guarded by the CMake option AGENT_MEMORY_BUILD_RESEARCH_TESTS=ON. The option defaults to OFF. The normal core and MDBX jobs explicitly keep it off, so they do not require Python, NumPy or PyYAML merely to configure and run the semantic C++ tests. No test source or research harness is deleted. Research jobs and the full scheduled/manual lane opt in when they need those registrations.

This option controls CTest registration only. It does not make a research executable disappear, and it does not change the domain or storage contracts.

## Change classification

The classifier uses the pull request base and head (or the previous and current commit on main) and maps paths to lanes:

- src/, include/, tests/ and examples/ select Core by default.
- MDBX API facades (including `src/agent_memory/infrastructure/mdbx.hpp`),
  implementation, storage tests and MDBX submodules select Core and Storage.
- research-dependent source/test families under embedding, index, retrieval and
  eval, plus research tools and dependencies, select Core and Research.
- shared CMake/build-control files (`CMakeLists.txt`, `cmake/*`, and
  `*.cmake`) are cross-cutting and select every lane.
- README*, Markdown, guides/ and docs/ select Documentation.
- DBI manifest inputs additionally select the DBI validation step.
- unknown paths and other shared control files select every lane.

The classifier runs representative regression assertions for documentation,
the MDBX facade, common CMake, research sources and unknown paths. A classifier
change that drops a required lane therefore fails before the selected jobs run.
When adding a new subsystem, update the classifier in
[ci.yml](../.github/workflows/ci.yml) in the same change that introduces its
build or test surface. Do not silently classify a new correctness-critical
path as documentation or research-only.

## Classifier safety checks

The classifier also runs `git diff --check` for every pull-request or push
change set. The documentation job may repeat that check, but no other lane
depends on documentation classification for whitespace validation. If
`AGENT_MEMORY_BUILD_RESEARCH_TESTS=ON`, CMake uses a required Python
interpreter; it never reports a successful configuration with the requested
research registrations silently absent.

## Triggers

Pull requests use the path classifier. Pushes are limited to main to avoid running both a branch push and its pull request for the same work. A manual workflow dispatch and the weekly schedule deliberately enable the complete validation plan, including the full research CTest job.

The full-research job is intentionally not part of an ordinary documentation or C++ pull request. It remains available for release preparation, research milestones and periodic drift detection.

The storage lane currently runs the MDBX C1 smoke on Ubuntu. A Windows MDBX smoke was evaluated while preparing this split and exposed a macro collision between the pinned `mdbx-containers` headers and the Windows SDK (`small` from `rpcndr.h`). It is deferred to a dedicated portability fix; CI does not mark a known-failing probe as required. The regular Windows core lane remains enabled for the portable, MDBX-off configuration.

## Required status policy

Branch protection should require CI gate, rather than every conditional lane separately. The gate verifies:

- change classification succeeded;
- every lane selected for the change completed successfully;
- the full research lane succeeds for manual and scheduled runs.

This keeps a stable required check while allowing irrelevant expensive jobs to be skipped without weakening the selected coverage.

## Scope of this policy

This split changes registration and scheduling, not the evidence standard:

- core semantic tests remain required for core changes;
- MDBX changes exercise the storage lane;
- research changes still run their relevant harnesses;
- the complete research run remains available;
- no correctness test is removed;
- a skipped lane is valid only when path classification proves it is irrelevant.
