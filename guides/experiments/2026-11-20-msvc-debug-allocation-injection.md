# MSVC checked-STL allocation injection boundary

Date: 2026-11-20. Status: reproduced diagnostic; test-only correction.
No index implementation, public API, ABI setting or storage format changes.

## Observation and mechanism

Native MSVC 19.44.35228, VS2022 Build Tools 14.44.35207, C++17, Debug with
the default `_ITERATOR_DEBUG_LEVEL=2` reproduced an abort in
`agent_memory_exact_lexical_index_test`. The initial full MDBX-enabled run
passed 80/81 tests; this one process displayed a CRT abort dialog.

A bounded diagnostic executable redirected CRT diagnostics and installed a
non-allocating terminate handler. It reported active `std::bad_alloc` with:

```text
test allocate_for_test -> operator new
-> allocator<std::_Container_proxy>::allocate
-> _Container_base12::_Alloc_proxy
-> basic_string(basic_string&&) noexcept
-> ChunkId / LexicalDocumentRecord move
-> preparation of the by-value upsert argument
```

Copying the argument was a disconfirmation control, not the repair. That
avoided the first move, but a later failure budget terminated through the
same proxy allocation from `basic_string()` inside `StoredRecord` default
construction. Both standard-string constructors are `noexcept` in the observed
MSVC STL. The global `operator new` replacement cannot turn those failures
into recoverable exceptions at the index boundary.

The affected implementation/test sources are identical between the original
integration base `342fffbe9f4909602002dc286562c3b5be15e06c` and
`main@2b65bdfef8b43978c56a2e75bbfd1d736a2fcb16` before this correction.
This is not evidence of a new storage defect or a demonstrated violation of
the index rollback guarantee. A clean sanitizer/MinGW run alone would not have
explained the MSVC abort.

## Narrow correction and coverage limit

Only the exhaustive global-new failure matrix is not executed for
`_MSC_VER && _ITERATOR_DEBUG_LEVEL > 0`. The test prints an explicit `SKIP`
diagnostic for that boundary. All ordinary semantic, replacement/ownership and
allocation-count assertions still execute in checked MSVC Debug.

The complete insertion/replacement failure matrix remains unchanged and prints
`PASS` in MSVC Release and other toolchains, including MinGW Debug. No allocator
size exemptions, fault-budget changes, iterator-debug ABI changes, production
exception suppression or catch-all recovery were introduced. The Debug result
must not be described as exhaustive allocation-failure qualification.

## Standalone reproducer

[`msvc_checked_string_allocation_repro.cpp`](../../tests/lexical/msvc_checked_string_allocation_repro.cpp)
has no dependency on `agent_memory`. It constructs a string before enabling a
global allocation failure, then moves the string. Its terminate handler exits
without unwinding or displaying a dialog. Exit 73 with the exact `EXPECTED`
diagnostic demonstrates the checked-STL boundary; exit 0 demonstrates the
unchecked move completing without an allocation. Other exits are not success.
This diagnostic source is not registered as a normal CTest test and does not
change test counts or library build flags.

From a VS2022 Developer PowerShell at the repository root:

```powershell
New-Item -ItemType Directory -Force .build/f1-repro
cl /nologo /std:c++17 /EHsc /MDd /Fo.build/f1-repro/debug.obj /Fe.build/f1-repro/debug.exe tests/lexical/msvc_checked_string_allocation_repro.cpp
& .build/f1-repro/debug.exe # Expected exit 73 and the EXPECTED diagnostic.
cl /nologo /std:c++17 /EHsc /MD /O2 /Fo.build/f1-repro/release.obj /Fe.build/f1-repro/release.exe tests/lexical/msvc_checked_string_allocation_repro.cpp
& .build/f1-repro/release.exe # Expected exit 0.
```

## Focused qualification

The ordinary test can be reproduced with an MDBX/JSON-off build; the index
itself does not depend on those optional adapters:

```powershell
cmake -S . -B .build/f1-msvc -G "Visual Studio 17 2022" -A x64 -DAGENT_MEMORY_ENABLE_MDBX=OFF -DAGENT_MEMORY_ENABLE_JSON=OFF -DAGENT_MEMORY_BUILD_TESTS=ON -DAGENT_MEMORY_BUILD_RESEARCH_TESTS=OFF
cmake --build .build/f1-msvc --config Debug --target agent_memory_exact_lexical_index_test --parallel 3
ctest --test-dir .build/f1-msvc --build-config Debug -V -R '^agent_memory_exact_lexical_index_test$' --timeout 45
cmake --build .build/f1-msvc --config Release --target agent_memory_exact_lexical_index_test --parallel 3
ctest --test-dir .build/f1-msvc --build-config Release -V -R '^agent_memory_exact_lexical_index_test$' --timeout 45
```

Observed focused controls, using matching-configuration core archives and the
corrected test source:

| Mode | Lexical result | Standalone reproducer |
| --- | --- | --- |
| MSVC Debug, checked STL | PASS, explicit matrix SKIP; ownership counts 49 at all four corpus sizes | Expected exit 73 |
| MSVC Release, unchecked STL | PASS, complete insertion/replacement fault matrix; ownership counts 18 at all sizes | Exit 0 |
| MinGW GCC 15.2 Debug | PASS, complete insertion/replacement fault matrix; ownership counts 6 at all sizes | Exit 0 |

Both MSVC modes were additionally rebuilt through the ordinary project CMake
target in `.build/f1-msvc`, including a fresh matching-configuration core
archive. Each focused CTest passed 1/1; verbose output independently confirmed
the Debug `SKIP` and Release complete-matrix `PASS` routes. The standalone
Debug/Release exit-code and diagnostic controls passed independently with
10-second process bounds. Focused lexical invocations were bounded to 45 seconds.

Exact-head CI remains a publication step. Existing ASan/UBSan results are
separate evidence; no new sanitizer execution is claimed by this note. This
does not yet replace the parent's full combined MDBX/platform matrix.
