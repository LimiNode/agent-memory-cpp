# Pre-release ANN/R4 workspace cleanup manifest

Status: `READ-ONLY INVENTORY`

Date: 2026-11-12

Inventory target: the local `agent-memory-cpp` repository and registered
worktrees

This manifest records storage and branch pressure discovered while preparing
the ANN/R4 compatibility audit. It authorizes no deletion, branch close,
worktree removal, `git prune`, `git gc`, reset, or force operation.

## Inventory snapshot

The inventory was collected from repository metadata and the local filesystem
at `main@795588b0e3f170deb279431693772042372dbf18`. The audit worktree's only
changes were this task's two new reports; other agents' worktrees were read
only. Size scans ended at approximately 01:45--01:50 UTC; this is a live
filesystem snapshot, not a consistent backup.

The detailed, local-only machine-readable manifest is:

```text
E:\_repoz\agent-memory-cpp\.artifacts\pre-release-ann-r4-audit-20261112\inventory.json
SHA-256: 98cd29d5ec7254b80cce5f192761e6d538293b2ac92d00d25fd72b2844809585
```

It records absolute per-workspace paths, logical file sizes, heuristic
build/cache/raw/model buckets, receipt/manifest name counts, all 303 worktree
HEADs and status output, 528 local branches, 457 current remote heads joined
to 608 GitHub PR records, 54 files of at least 512 MiB, and three model hashes.
It is deliberately not committed as a large generated research dump. A reviewer
without this machine can verify the compact summary, not the full local
inventory; it must be re-created before any future cleanup.

| Item | Observed value | Handling |
| --- | ---: | --- |
| Registered worktrees | 303 | preserve until each owner confirms disposal |
| Dirty worktrees | 16, including this audit's two untracked reports | do not reset, rebase, or delete |
| Clean worktrees | 287 | candidates only after branch/PR/evidence ownership review |
| Local branches | 528 | preserve local-only commits and worktree ownership |
| Remote heads (`git ls-remote --heads liminode`) | 457 | no branch deletion in this task |
| Open PRs at inventory time | 15 | preserve all PR branches and their evidence |
| `.worktrees` | about 99.69 GiB | mixed active work and historical evidence; not reclaimable by name alone |
| `.artifacts` | about 56.20 GiB | research payloads; preserve until receipt lineage is audited |
| `.artifacts/legacy` | about 32.03 GiB | likely historical evidence; no deletion authority |
| `.artifacts/legacy-tmp` | about 23.97 GiB | may be disposable, but uniqueness and receipt checks are missing |
| `.git` | about 1.50 GiB | no prune/gc; object retention is required for PRs and receipts |
| `.build` | about 0.41 GiB | reproducible build output candidate, not automatically safe |
| `.tmp` | about 0.36 GiB | temporary databases/logs candidate, not automatically safe |
| Main worktree, excluding separately counted hidden roots | about 4.06 GiB | dirty; includes about 4.05 GiB in build-path files |
| Sibling worktrees and three standalone directories | about 25.51 GiB | additional to the `.worktrees` directory |
| Total inventoried logical file lengths | 201,540,788,479 bytes (187.70 GiB) | includes sources/submodules/builds; not unique physical allocation |

Sizes sum file lengths and skip directory reparse points. Sparse files,
hardlinks, compression and active writers prevent equating this with physical
reclaimable space. Build-path and extension buckets overlap: about 45.01 GiB
matches build/cache paths and about 58.03 GiB matches raw-payload extensions.
These are heuristics, not a semantic dataset classifier; notably `.bin` may
contain model weights or vectors. Receipt-name matches do not prove that a
file is covered by a reproducibility manifest.

No `E:\_repoz\agent-memory-workspaces` directory was found. All registered
sibling worktrees plus the standalone `agent-memory-cpp-lexical-perf-build`,
`agent-memory-cpp-lexical-perf-build-mingw` and `agent-memory-cpp-preservation`
directories were inventoried. Other drives, remote evidence releases, active
file handles and authoritative-copy locations were not comprehensively audited.

## Branch disposition (not deletion permission)

`liminode` targets `LimiNode/agent-memory-cpp`; `origin` targets the
`NewYaroslav/agent-memory-cpp` fork. Both remotes had identical 457 branch names
and HEAD SHAs at refresh, checked with `git ls-remote --heads` for each. PR
states below are from `LimiNode/agent-memory-cpp`, not inferred from the fork.

| Current remote heads | Count | Disposition |
| --- | ---: | --- |
| Attached to an open PR | 15 | active; preserve, including stacked bases |
| Attached to a merged PR | 369 | integration observed; artifact retention/content equivalence still unresolved |
| Attached to a closed, unmerged PR | 58 | may be abandoned or superseded; preserve until evidence owner confirms |
| No matching PR head name | 15 | unknown/evidence-only candidate; do not infer abandonment |

`git log --branches --not --remotes` found 191 locally reachable commits not
reachable from currently tracked remote refs. They are not all necessarily
unpushed work (squash histories differ), but none may be discarded from this
count alone. The object store reports 2,546 loose objects (5.75 MiB), 24,643
packed objects (370.07 MiB), 10 packs, zero prune-packable objects, and one
zero-byte garbage entry warning. No object cleanup was attempted.

## Concrete retention and review candidates

Paths below are absolute. `Unknown` reproducibility/copy/dependency means
protect, not permission to remove. Per-workspace details remain in the local
manifest.

| Path | Bytes | Reproducibility / authoritative copy / dependency | Action and risk |
| --- | ---: | --- | --- |
| `E:\_repoz\agent-memory-cpp\.build` | 444,715,341 | build paths; regeneration command/toolchains not replayed; no authoritative duplicate verified | review after owner confirmation; medium; safe reclaim not established |
| `E:\_repoz\agent-memory-cpp-lexical-perf-build` | 256,844,249 | standalone build; no raw-payload extension matches; replay and active process checks pending | review only; medium |
| `E:\_repoz\agent-memory-cpp-lexical-perf-build-mingw` | 220,759,253 | toolchain-specific build with six receipt/manifest name matches; dependencies unresolved | review only; medium |
| `E:\_repoz\agent-memory-cpp\.tmp` | 381,434,560 | includes `release-history-bundle-restore-20261013.git`, staging Git history and preservation verification directories | protect historical evidence; high; not disposable merely because temporary |
| `E:\_repoz\agent-memory-cpp\.worktrees\learned-sparse-experiment` | 4,893,918,410 | 4,564,508,256 build-path bytes include two BGE-M3 weight files; open learned-sparse work #558/#561 is a retention dependency | protect models and research evidence; high |
| `E:\_repoz\agent-memory-cpp\.worktrees\m0-resource-importer` | 4,348,157,711 | mostly build-path bytes; exact reproduction and current owner/receipt closure unresolved | review build subdirectories only, preserve source; medium |
| `E:\_repoz\agent-memory-cpp\.worktrees\agent-memory-cpp-scale-aware-native-mih-sweep` | 7,170,943,712 | frozen vectors and historical research payloads; exact dependency closure unresolved | protect; high |
| `E:\_repoz\agent-memory-cpp\.artifacts\legacy\r4-representative-hnsw-control-raw\representatives-k16.f32le` | 3,232,991,232 | R4 HNSW control referenced by the R4 evidence ledger; source/reproduction/duplicate not verified | protect frozen control; high |
| `E:\_repoz\agent-memory-cpp\.artifacts\legacy\r4-fused-payload-clean-raw\fused-page-aligned.bin` | 819,200,000 | historical R4 physical payload; exact receipt path/hash closure unresolved | protect; high |

### Hash-confirmed duplicate content is not yet safe deletion

These three files have equal length `2,271,145,830` bytes and identical SHA-256:

```text
b5e0ce3470abf5ef3831aa1bd5553b486803e83251590ab7ff35a117cf6aad38

E:\_repoz\agent-memory-cpp\.worktrees\bge-m3-sparse-pilot\build\bge-m3-artifacts\pytorch_model.bin
E:\_repoz\agent-memory-cpp\.worktrees\learned-sparse-experiment\build\bge-m3\pytorch_model.bin
E:\_repoz\agent-memory-cpp\.worktrees\learned-sparse-experiment\build\hf-cache\hub\models--BAAI--bge-m3\snapshots\5617a9f61b028005a4858fdac845db406aefb181\pytorch_model.bin
```

Keeping one authoritative copy would make up to `4,542,291,660` logical bytes
(4.23 GiB) redundant. Actual physical savings, loader paths, offline
availability, hardlink status, model revision/configuration and receipt
references remain unchecked. This is a conditional deduplication candidate,
not 4.23 GiB of approved reclaim. Open #558/#561 and the learned-sparse notes
must remain reproducible; no copy was removed or relocated.

## Protection classes

### Protect without further review

- dirty worktrees and any worktree containing unpushed commits;
- branches referenced by open PRs, including stacked PR bases;
- canonical source bundles, model/training artifacts, frozen R4 inputs and
  candidate streams;
- checksum-bound raw receipts and evidence required by historical PRs;
- any database or payload whose owning receipt has not been located;
- the Git object database and remote-tracking metadata until all PRs are
  integrated or explicitly abandoned.

### Review candidates, but do not remove in this task

- clean worktrees whose branch is merged, has no unpushed commits, and is not a
  base for an open PR;
- `.build` directories whose compiler/configuration can be reproduced from a
  clean checkout;
- `.tmp` databases and logs whose path is not referenced by a receipt and whose
  owning process is no longer active;
- duplicate artifact copies after byte-level hashes and receipt references
  prove that one retained copy is sufficient.

### Not safe to classify by directory name

`.artifacts/legacy`, `.artifacts/legacy-tmp`, old research worktrees, and large
model files may look obsolete but can contain the only copy of a frozen input
or source-bound receipt. A future cleanup pass must build a manifest of
SHA-256, owner PR/commit, receipt references, and regeneration status before
proposing removal.

## Safe cleanup protocol for a future authorized pass

1. enumerate each candidate with an absolute path and content hash;
2. map it to a branch, PR, receipt, release, or explicit regeneration command;
3. verify no dirty worktree, open PR, stacked base, or running process depends
   on it;
4. preserve one immutable copy of every source/model/frozen-input bundle;
5. move only explicitly approved disposable files to a recoverable quarantine;
6. re-run CI/evidence validators and record the reclaimed bytes;
7. perform Git prune/gc only after an explicit repository-wide retention review.

The conservative safe-to-delete estimate is **0 bytes**. About `0.86 GiB` in
the three named build roots is a review candidate, not approved reclaim; `.tmp`
is excluded because it contains history/preservation work. The `4.23 GiB`
duplicate-model estimate is conditional. The roughly `56.20 GiB` of artifacts
and `99.69 GiB` of nested worktrees remain protected until ownership and
reproducibility are resolved.

## Collection and verification

Read-only Git inputs were `git worktree list --porcelain`, per-worktree
`git status --porcelain`, `git for-each-ref` over `refs/heads`, live
`git ls-remote --heads` for both remotes, `git count-objects -vH`, and
`git log --branches --not --remotes`. PR dispositions came from the paginated
GitHub pulls API, including merged/closed records, not `git branch --merged`.
Directory traversal summed `FileInfo.Length` without following directory
reparse points; the main worktree excluded the separately counted hidden roots.
The local manifest was checked for all 303 worktree-size bindings, 457 remote
heads, 54 large-file entries, zero scan errors, total byte arithmetic and the
three equal model hashes. Authoritative-copy and process-use audits are still
required before approval of any cleanup.

## Relationship to the ANN/R4 gate

Cleanup must not remove retained embeddings, canonical source bytes, route
manifests, candidate streams, THQ representations, or receipts needed to prove
the later-generation rebuild. The compatibility gate is incomplete until its
inputs and before/after canonical snapshots can be reproduced from retained
evidence. Workspace reduction is therefore a separate, reviewable operation,
not a prerequisite hidden inside the ANN implementation.
