# Raw Markdown Technical Preview candidate

This is candidate documentation, not a published release. It describes only
the experimental Raw Markdown source-backed path integrated by
[PR #607](https://github.com/LimiNode/agent-memory-cpp/pull/607), at
`52004324bc0d2b07c3657245195db349dd391774`, and its operational qualification.
The [preflight](technical-preview-preflight.md) records the release decision
boundary. No tag, version change, ABI stability, general migration promise or
production retrieval activation follows from this guide.

The broader [normalized-first profile](first-release-profile.md) is unchanged.
Tables/cells, retained images, timed transcripts, Summary/Concept publication,
ANN/R4 serving, graph navigation and general coordinator recovery are not
supported by this bounded preview. Markdown table/image syntax is retained as
text, not interpreted as those capabilities. The host supplies UTF-8 bytes;
the library does not fetch URLs, parse arbitrary documents or run an agent.

## Build, install and run the public example

Use CMake 3.20+, a C++17 compiler and pinned dependency checkouts. The static
library exports `agent_memory::agent_memory`. MDBX is optional globally but
required for this path; JSON, Zstd and research tooling are not required.
Supported build hosts are Windows, Linux and macOS. The MDBX product evidence
below names the tested lanes separately; minimal-package portability does not
by itself qualify MDBX data-file interchange or every filesystem on those hosts.

```sh
git submodule update --init external/libmdbx external/mdbx-containers
cmake -S . -B .build/preview -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DAGENT_MEMORY_ENABLE_MDBX=ON -DAGENT_MEMORY_ENABLE_JSON=OFF \
  -DAGENT_MEMORY_BUILD_TESTS=OFF -DAGENT_MEMORY_BUILD_EXAMPLES=OFF \
  -DAGENT_MEMORY_BUILD_BENCHMARKS=OFF -DAGENT_MEMORY_BUILD_RESEARCH_TESTS=OFF
cmake --build .build/preview --parallel
cmake --install .build/preview --prefix "$PWD/.build/preview-prefix"
```

The installed package requires installed `mdbx_containers` and libmdbx
packages too. Build those from the same pinned sources; the reproducible
provider/container configure and install commands are in
[packaging CI](../.github/workflows/packaging.yml), job
`mdbx-installed-consumer`, step `Build dependency packages`. It deliberately
keeps both sources flat under `external/`. See also
[dependencies](dependencies.md) and [packaging](../packaging/README.md).

After installing dependencies into `mdbx-containers-prefix` and
`mdbx-provider-prefix`, run the independent consumer:

```sh
cmake -S packaging/consumers/mdbx-hydration-installed \
  -B .build/preview-consumer -G Ninja -DCMAKE_BUILD_TYPE=Release \
  "-DCMAKE_PREFIX_PATH=$PWD/.build/preview-prefix;$PWD/.build/mdbx-containers-prefix;$PWD/.build/mdbx-provider-prefix"
cmake --build .build/preview-consumer --parallel
.build/preview-consumer/agent_memory_mdbx_raw_markdown_product_consumer
```

On Windows, run the corresponding `.exe`. The consumer uses only installed
public headers and the exported target, with standard C++17 (extensions off).
Its [complete executable source](../packaging/consumers/common/mdbx_raw_markdown_product_consumer.cpp)
is the authoritative runnable example, including temporary-directory cleanup
and every status check. A minimal composition is:

```cpp
#include <agent_memory/infrastructure/mdbx/MdbxRawMarkdownProductIngestion.hpp>

void example() {
    using namespace agent_memory;
    auto context = MdbxStorageContext::open("memory.mdbx");
    RawMarkdownProductIngestionOptions options;
    options.context = context;
    options.table_prefix = "preview";
    options.raw_cas_root = "cas";
    MdbxRawMarkdownProductIngestion product{options};
    const auto imported = product.import(ObservedResource{
        ScopeId{"preview"}, ResourceId{"a"}, "a.md", "# Exact evidence\n",
        SourceTextOrigin::OriginalText, std::nullopt, 1}, 1);
    if(imported.status != RawMarkdownProductImportStatus::Published) return;
    const auto hits = product.retrieve_and_hydrate(
        ScopeId{"preview"}, RetrievalQuery{"evidence", std::nullopt, 8, {}}, 4096);
    for(const auto& hit : hits) {
        if(hit.hydration.status == AuthoritativeArticleHydrationStatus::Hydrated) {
            // Only these verified bytes may be used as a source-backed quote.
            const auto& bytes = hit.hydration.bytes;
            (void)bytes;
        }
    }
}
```

`Published` is a completed publication observation, not a perpetual freshness
promise. Check each subsequent hydration status. A candidate's inline text
alone is not verified source bytes. Missing/corrupt CAS may leave that candidate
visible, with `Missing/CasMissing` or `Corrupt/CasCorrupt` and zero hydration
bytes. There is no automatic repair. Budget overflow throws
`ProjectionAwareLexicalRetrievalBudgetExceeded`, not a complete partial ranking.

## Input and execution limits

Only case-insensitive `.md`/`.markdown` URI suffixes and `OriginalText` are
accepted; a URI with a trailing query/fragment does not match those suffixes.
Bodies must be valid UTF-8. Defaults from
[the public options](../src/agent_memory/infrastructure/mdbx/MdbxRawMarkdownProductIngestion.hpp):

| Budget | Default |
|---|---|
| Full source body / full CAS verification | 64 MiB each |
| Returned source range | 64 KiB |
| Query candidates | 64 |
| Scanned units per query | 4096 |
| Indexed projection bytes per query | 8 MiB |

These are admission/work ceilings, not a claim that all 64 MiB bodies fit every
other importer or storage limit. The query rebuilds a bounded in-memory lexical
view; it is not an unbounded persistent serving engine. MDBX key limits depend
on page geometry and encoded composite keys; keep scope/resource identifiers
short and qualify application identifiers rather than assuming arbitrary
string lengths. The installed fixture uses `preview`, `a` and `b`.

## Cold offline backup and restore

This procedure qualifies a clean, same-version copy on trusted local storage.
It does not provide a backup service, online consistency, atomic MDBX+CAS
snapshots, process-kill recovery, fsync durability or concurrent filesystem
mutation protection. It does not authorize hot workspace relocation. Lifecycle
and placement remain owned by
[storage integration sections 3.1, 3.2 and 5.1](storage-backend-integration-roadmap.md).

1. Stop every writer, importer, query and background task using this database
   **and** CAS root. Drain transactions/cursors. Record configuration and
   expected resource/unit IDs, revisions, SHA-256 and evidence for validation.
2. Destroy all stores, adapters, retained options/context pointers and other
   connection handles. For a host-attached connection, the host must also close
   its connection after draining users. A single `context.reset()` is not enough
   while another shared handle remains. Keep the source quiescent through copy.
3. Check that the expected MDBX data file and CAS directory both exist. This
   preview uses `no_subdir=true`: copy the complete MDBX data file and the
   **entire** File CAS tree, including historical objects, into a separate fresh
   backup directory. Do not copy transient MDBX lock files as database data.
   Check copy errors; a partial destination is not a completed backup.
4. Restore both components to another fresh location, preserving configuration.
   Explicitly configure the restored MDBX filename and CAS root. Do not open the
   old and restored corpus for competing authoritative writes.
5. Before accepting the restore, re-read expected resources through the public
   product path; compare IDs, generation, full resource/CAS SHA-256, typed
   source/binding/artifact identities, Original candidate revision, evidence
   ranges and exact hydrated bytes. Check retained stale candidates remain stale.
   Verify every required CAS object, not merely directory existence. Do not
   enable application traffic if any required observation fails.

The consumer implements this with two resources, A@2 and B@1, plus retained
A@1 evidence. All owned MDBX handles are scoped out before each copy. A fresh
restored root is explicitly passed to the product. Its negative controls are:

| Incomplete/incompatible input | Qualified behavior |
|---|---|
| MDBX only or CAS only | Host preflight rejects before creating a restore destination |
| Missing CAS root with intact MDBX | Exact candidate observed, `Missing/CasMissing`, zero bytes |
| Corrupted restored CAS object | Exact candidate observed, `Corrupt/CasCorrupt`, zero bytes |
| Wrong table prefix | Empty namespace, not proof of an empty or successful restore |
| Expected manifest profile version 2 against stored version 1 | Hydration rejects with `Stale/ProjectionMissing`, zero bytes; no implicit migration |

`MdbxStorageContext::open()` can create a new database. A wrong/missing database
path is not inherently a restore error; the host must check expected files and
identities first. Prefix sanitization can also map different strings to the
same namespace. Neither paths nor namespace configuration should be guessed.

## Persistence and configuration inventory

This is a current writer inventory, not a global database schema version or a
promise that a historical reader accepts future formats. Preserve the complete
MDBX file rather than reconstructing selected DBIs. For prefix `P`, the relevant
owners are:

| Owner / data | Current format or invariant |
|---|---|
| `MdbxStorageContext` | Owned single-file MDBX, `no_subdir=true`, explicit absolute/relative path policy, `max_dbs=96` default. Record host-attached flags/geometry separately if used; that mode is not this copy fixture. |
| `MdbxResourceImporter` | `P_resource_import_{intents,bindings,epochs,fencing_tokens,fencing_schema}`; intent v4, fencing v1. Preserve lease/replay inputs and stable `bound_at_ms`. |
| `MdbxDurableLexicalDocumentMemory` | `P_durable_lexical_catalog`; entry v2, pending v2/v3/v4 selected by guard shape. Canonical revisions/bodies/ledgers are owned separately. |
| `MdbxCanonicalContentStore` | `P_canonical_{heads,revisions,bodies,ledgers}`; revision/body v3, logical body v1, ledger v1. |
| `MdbxResourceManifestStorage` | `P_resource_manifests`; writer payload v6; profile `agent_memory.m0_resource_importer.raw_chunks_original@P`, schema version 1. |
| `MdbxKnowledgeUnitStore` | `P_knowledge_units`, `P_content_key_to_unit_id`, `P_knowledge_units_by_kind`; envelope v1, by-kind index schema 2, Chunk identity recipe 2. Preserve IDs, revisions, scope and exact resource references. |
| `MdbxProjectionStore` | `P_unit_projections`; payload v1; identity includes unit ID, kind and projection revision. Current Original revision equals unit revision, derivation generation 1; preserve derivation fingerprint/body. |
| `MdbxResourceArtifactBindingStore` | `P_resource_artifact_bindings`; length-framed exact resource revision key and two opaque source/binding IDs; currently no independent payload version marker. Do not invent one on restore. |
| `MdbxArtifactCatalog` | `P_artifact_{schema,artifacts,bindings,revisions,intents,receipts}`; catalog marker v2; artifact, binding, intent and receipt payloads v1. |
| `FileRawBlobStore` | Explicit filesystem root, `sha256/<first-two-hex>/<digest>.raw`; `ArtifactId=sha256:<hex>`. SHA-256 covers complete decoded original bytes, not a pathname or prototype hash. |

Record `ResourceId`, `KnowledgeUnitId`, scope, resource generation, full digest
algorithm/value, source range/quote evidence, `SourceRevisionId`,
`ArtifactBindingId`, `ArtifactId`, pipeline configuration and stable binding
timestamp. The product derives bounded opaque source/binding labels; do not
recompute them with a new recipe or replace the full revision binding.
Physical directory changes do not create new logical identities.

The qualification baseline `9f3e6f900474b16f901c473c6cc6d89a0882131b`
pins libmdbx `fc8b8e4697e0ef8b2cd5aee1f2d9fb0974fc665f` and
mdbx-containers `4ab32b5eab3cdfce95d1a6c0f05f566019793ec4`.
Use the candidate commit's actual pins, compiler and flags for writer/reader
replay, not arbitrary dependency upgrades. No cross-platform database-file
portability, cross-version upgrade, binary ABI or general migration guarantee
is established by same-version local backup/restore.

## Qualification and remaining release gates

The operational fixtures are pinned at commit
[`b975835e34bd1770a87934aedb6a0fca48179042`](https://github.com/LimiNode/agent-memory-cpp/commit/b975835e34bd1770a87934aedb6a0fca48179042).
Local Release checks at that fixture revision: focused MDBX CTest 6/6;
installed hydration and Raw Markdown consumers 2/2; installed product,
hydration and umbrella header probes with MDBX on/off 6/6; MDBX-off library
build. The host was Windows/MinGW GCC 15.2.0. Exact-head CI/packaging URLs and
review status belong to the operational Draft PR, not the older #607 runs.

The installed executable covers A@1 import and exact hydration, close/reopen,
A@2 generation/digest/bytes, retained A@1 stale admission and hydration, typed
query budget failure, cold backup/restore identity/evidence parity, full CAS
verification, and the negative controls above. Packaging CI executes this
consumer in its Ubuntu MDBX lane. Minimal packaging also runs on Windows and
macOS; that is not MDBX consumer execution on those platforms. Local Windows
MinGW qualification is additional evidence, not a universal filesystem claim.

The [internal product test](../tests/infrastructure/mdbx/mdbx_raw_markdown_product_ingestion_test.cpp)
separately covers importer/catalog/pre-join faults and deterministic
supersession. Its after-join/before-final-observation interruption now requires
`Incomplete` to the original caller while the completed product is readable;
identical replay after reopen preserves generation and identities. A later
revision still makes the old evidence stale. This is injected interruption,
not a process-kill or atomic cross-store transaction claim.

Before publication: independent review of this operational slice, exact-head
CI and packaging on the intended release commit, explicit release approval and
the agreed ANN/R4 upgrade proof/review are still required. The latter is the
second agent's independent compatibility work, not an existing corruption
finding or permission to activate ANN. Reopen, replay, cold backup, cross-version
compatibility and derived-index rebuild remain distinct evidence categories.
General migration/backup tooling and broader input profiles remain deferred.
