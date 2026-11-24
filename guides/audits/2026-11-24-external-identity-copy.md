# External identity copy hardening (2026-11-24)

Status: implemented foundation slice; final qualification/publication receipt
is in the Draft PR. No merge authorization. Base: codec #690 at
`7bb88efc078f3b7c3c9a3fb49235d472bbaef717`; no change to #690 itself.
Normative owner: [identity policy, section 6.1](../identity-policy.md#61-explicit-exception-safe-value-copies).

## Evidence and decision

The #695 diagnostic at `9ed28efef9a01b1b235c5301ba0a098594b3d51f`
reproduces failing `std::variant` copy cleanup with standard headers alone.
Ubuntu GCC 15.2.0 (`g++-15` 15.2.0-16ubuntu1), libstdc++ headers under
`/usr/include/c++/15`, runtime package `libstdc++6` 16-20260322-1ubuntu1:
O0 ASan/UBSan reaches `variant:1916`; O3 without sanitizers reports an invalid
free. Direct active-alternative construction propagates `bad_alloc` correctly.
The installed header's never-valueless path visits storage after an alternative
copy threw during construction. This is not evidence of an importer map erase
defect or of any particular upstream bug number.

OpenTelemetry [#4707](https://github.com/open-telemetry/opentelemetry-cpp/issues/4707)
is an independently reported similar cleanup failure with more alternatives;
the reproducer/layout differ, so identical bug identity is not asserted.

Two strategies considered:

| Strategy | Boundary, compatibility and qualification |
| --- | --- |
| A: direct active-alternative construction | Chosen. One compiled AMXR copy implementation recursively rebuilds components; additive Conversation wrappers use it. Same owning value types, aggregate initialization, ABI layout, C++17 and wire bytes. `bad_alloc` propagates; fully constructed temporary members clean up normally; no copy of an incomplete variant base. |
| B: fixed STL | Local package metadata lists only the installed g++/libstdc++ candidates, and the reproduction fails there. No alternative fixed header/runtime pair has been proved. Waiting/pinning is not a demonstrated repair; no compiler, optimization, sanitizer or injection setting is changed as a workaround. |

No custom special member is added to an aggregate, no scalar alias or alternative
is replaced. A copy costs the same owned strings/vectors as the corresponding
ordinary copy; composites reserve once and move completed components. These
are not string/AMXR serialize-deserialize round-trips. Ordinary caller-side
implicit copies remain STL operations; callers needing this exceptional-path
contract must use named copies. A valueless source throws `bad_variant_access`;
there is no attempt to admit or repair malformed values.

## Copy-path inventory

| Value/path | Handling |
| --- | --- |
| ExternalIdentityScalar | Visit active alternative, direct in-place construction; signed/unsigned/string tags retained |
| Component / vector element | Copy name and explicitly constructed scalar; reserve then move elements, never vector-copy nested scalar variants |
| ExternalObjectKey | Scalar direct construction or recursive component vector; no variant copy constructor |
| ExternalObjectRef / optional parent | Copy namespace strings, explicitly copy parent/object keys, move completed optional |
| optional external / ConversationSourceBinding | Stage both refs with shared copy, move them into a completed binding |
| Unresolved target / link target | Explicit external copy; construct target variant from completed exact/unresolved value |
| Metadata / links | Copy each link through target helper, copy optional source through binding helper |
| Envelope | Metadata helper; legacy/call/opaque alternatives contain no AMXR; result target uses shared helper |
| AMXR / AMCE decoding | Construct alternatives/refs from decoded rvalues and move them; no new copying implementation |
| By-value entry/envelope conversion API | Existing ABI unchanged; caller-side lvalue copies remain caller/STL work; library decode paths pass rvalues |
| Import source validation / normalization | Downstream #695 must use shared binding copy, not aggregate source copy |
| Source-shaped witness and declared target | Downstream #695 must rebuild DTO fields and route every source/unresolved ref through shared helpers |
| Structured source map key | Existing canonical AMXR bytes and strong-ID strings, not an external variant copy |
| Store correct | Expanded OOM fixture reproduces the old envelope-copy failure; downstream must use shared envelope copy |
| Store Current origin metadata | Expanded OOM fixture reproduces copy-assignment failure; downstream may move metadata from the privately decoded root envelope |
| Exact/history reads | Decode fresh canonical bytes and move results; fault tests must cover external keys, receipts and pinned continuation |
| Equality / comparisons / encoding | Read const refs; do not copy external variants |

The old Phase 3A implementation at `cd6d5dff` was separately compiled with the
new external-key fixture and this foundation library. ASan/UBSan independently
reproduced failures at `correct`'s envelope copy (old line 372) and Current's
metadata copy assignment (old line 489). This is a concrete need for the two
downstream store corrections, not authorization to optimize/rewrite the fold.
The earlier local-observation 199-failure evidence remains historical and valid
only for its original fixture scope.

## Regression boundary

`external_identity_copy_failure_test` uses isolated standard replacement
allocation functions. Inputs, expected bytes and assertions are outside the
gate. Every measured ordinal throws once, checks unchanged original AMXR/AMCE
bytes, and retries successfully. GNU Debug ASan/UBSan passed 129 actual failures:
0/0/1/2/5/11/24/36/50 for signed/unsigned/string/component/composite/ref/binding/
metadata/envelope. Zero-allocation numeric copies are reported as such. The
fixture contains composite parent and object keys, nested optional sources,
unresolved metadata links and a result target; byte re-encoding is exact.
Static assertions preserve aggregates and the original scalar alias.

This is not whole-process OOM survival, malloc/OS/crash qualification or a fix
for arbitrary caller copies. Checked MSVC STL retains its explicit SKIP 77 for
proxy allocations in noexcept construction. A skipped lane is not a pass.
Windows, Linux Release/Debug, consumer, sanitizer and hosted evidence must be
bound to the final published heads, with any unavailable profile stated.

Foundation changes are distributed to the unmerged #694/#695 stack by ordinary
cherry-picks, not rebase/merge/force-push. Their PRs must name the originating
foundation commit and acknowledge duplicate logical patches until the owner
reconciles/retargets the resulting integration tree. No branch is deleted.
