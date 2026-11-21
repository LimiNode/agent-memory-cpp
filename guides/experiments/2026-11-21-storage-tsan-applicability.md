# Storage ThreadSanitizer Applicability

Date: 2026-11-21. Context: Storage Product Integration Phase 1.
Status: diagnostic attempt executed; **TSan qualification NOT_QUALIFIED**.
This is not a performance measurement or production concurrency certification.
See the [source audit](../storage-product-integration-audit.md).

## Question and source binding

Can the available Linux TSan configuration qualify the integrated flat,
identity-mapping, typed-KU and snapshot-read transaction paths without changing
their source or suppressing diagnostics?

Runtime source was the clean checkout at
`acd5ad5545386b3feeaa79f8a587855b60801b4d`:

- `src` tree: `87a70c0f850266e549458be40f05760febb1050f`;
- libmdbx: `fc8b8e4697e0ef8b2cd5aee1f2d9fb0974fc665f`;
- mdbx-containers: `c2a2326273a3d85f29283e2cd34942c7b8d72cbd`.

Source/pins and clean status were checked before the build and test execution,
and independently rechecked after the failed test run. No dependency or test
timeout was changed. The expected outcome was either a supported, clean test
execution or an explicit diagnostic/applicability limitation; the standalone
atomic/thread probe alone could not qualify storage.

## Setup and reproduction

WSL2 Linux `6.18.33.2-microsoft-standard-WSL2`, GCC 15.2, CMake 4.2,
C++17, MDBX ON, JSON OFF, Debug, two build workers and serial CTest.
The standalone instrumented C++17 thread/atomic probe compiled and exited zero.
The real library, libmdbx and four test executables were instrumented with
`-fsanitize=thread`; instrumentation was not limited to a mock.

Representative command sequence, using a clean exact-source checkout and a
separate build directory:

```bash
export TMPDIR
TMPDIR=$(mktemp -d /tmp/agent-memory-product-tsan-XXXXXX)
export TMP="$TMPDIR" TEMP="$TMPDIR"
export TSAN_OPTIONS=halt_on_error=1:exitcode=66
cmake -S "$task_source" -B "$task_build" -G 'Unix Makefiles' \
  -DCMAKE_BUILD_TYPE=Debug -DCMAKE_CXX_STANDARD=17 \
  -DAGENT_MEMORY_ENABLE_MDBX=ON -DAGENT_MEMORY_MDBX_DEPS_MODE=BUNDLED \
  "-DAGENT_MEMORY_MDBX_CONTAINERS_SOURCE_DIR=$task_source/external/mdbx-containers" \
  -DAGENT_MEMORY_ENABLE_JSON=OFF -DAGENT_MEMORY_BUILD_TESTS=ON \
  -DAGENT_MEMORY_BUILD_RESEARCH_TESTS=OFF \
  -DAGENT_MEMORY_BUILD_EXAMPLES=OFF -DAGENT_MEMORY_BUILD_BENCHMARKS=OFF \
  '-DCMAKE_CXX_FLAGS=-g1 -O1 -fsanitize=thread -fno-omit-frame-pointer' \
  '-DCMAKE_C_FLAGS=-g1 -O1 -fsanitize=thread -fno-omit-frame-pointer -DMDBX_UNALIGNED_OK=0' \
  '-DCMAKE_EXE_LINKER_FLAGS=-fsanitize=thread'
cmake --build "$task_build" --parallel 2 --target \
  agent_memory_mdbx_canonical_flat_storage_test \
  agent_memory_mdbx_storage_identity_mapping_test \
  agent_memory_mdbx_knowledge_unit_typed_store_test \
  agent_memory_mdbx_snapshot_revision_bound_retriever_test
ctest --test-dir "$task_build" --parallel 1 --output-on-failure --no-tests=error \
  --output-junit "$task_evidence/tsan.xml" \
  -R '^agent_memory_mdbx_(canonical_flat_storage|storage_identity_mapping|knowledge_unit_typed_store|snapshot_revision_bound_retriever)_test$'
```

`task_source`, `task_build` and `task_evidence` are explicit task paths, not
additional CMake variables. The observed effective C++ flags also contain
Debug's later `-g`; this was not a reduced-debug-symbol build. The native Linux
temporary directory was isolated from other Windows/WSL tests.

## Actual observations

| Test, prefixed `agent_memory_mdbx_` | Observed result |
| --- | --- |
| `canonical_flat_storage_test` | FAIL, TSan race report during concurrent publication |
| `storage_identity_mapping_test` | PASS |
| `knowledge_unit_typed_store_test` | PASS |
| `snapshot_revision_bound_retriever_test` | PASS |

CTest JUnit records four tests, one failure, zero disabled/skipped. All selected
executables were built and ran. The flat test stopped at the first report, so
its remaining cases were not qualified by this attempt. The other three passing
executions establish only their exercised paths under this instrumented run.

Compact diagnostic excerpt (paths shortened, line numbers from pinned source):

```text
WARNING: ThreadSanitizer: data race
Read of size 8 by thread T1:
  txn_basal_start, external/libmdbx/mdbx.c:39394
  MdbxCanonicalFlatStorage::commit, MdbxCanonicalFlatStorage.cpp:535
Previous write of size 8 by thread T2:
  lck_txn_lock, external/libmdbx/mdbx.c:26230
  MdbxCanonicalFlatStorage::commit, MdbxCanonicalFlatStorage.cpp:535
SUMMARY: ThreadSanitizer: data race ... in txn_basal_start
```

Static inspection identifies `txn->owner` read before writer-lock acquisition
and an owner write after acquisition by the competing thread. This describes
the reported access pair; it does not independently prove the dependency's
intended memory-model contract. Pinned `mdbx-internals.h:322` explicitly emits:

```text
libmdbx is not compatible with ThreadSanitizer; you will get a lot of false-positive issues.
```

The build emitted that warning and GCC's unsupported `atomic_thread_fence`
TSan warning. The report is therefore retained as unresolved dependency-level
evidence, not automatically dismissed as a false positive or relabelled PASS.
No suppressions, dependency source edits or repeated attempts were used to
obtain a favourable result.

## Retained local evidence and limits

Root: `.artifacts/storage-product-phase1-2026-11-21/` in the development
workspace. These are local artifacts, not a published evidence release:

| Artifact | SHA-256 |
| --- | --- |
| `tsan-20261121T182811Z.log` | `0562697ba9fe0b3d43a403954811244480dacd9fa7bac8c35c3424d0157f1608` |
| `tsan-20261121T182811Z.xml` | `992bf514a9d5eef06b953c3539bed0fbd1e9697f84ea52fb02b4c1c9e044ddca` |

The retained `qualify-tsan.sh` binds the exact source/pins; commands and the
decision-relevant excerpt are preserved above under the compact-artifact policy.
The build remains in `.build/storage-product-tsan`; no large generated dump is
committed. This single bounded diagnostic attempt does not supersede #674's
separate ASan/UBSan evidence or establish race freedom, throughput or serving
tails. TSan remains an unmet qualification gate for this configuration.

Next check: review the precise owner access pair and sanitizer support with the
pinned dependency contract before selecting a supported race-detection route.
Any dependency corrective, pin change or suppression requires separate review;
none is made or authorized by this note.
