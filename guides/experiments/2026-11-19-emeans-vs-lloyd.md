# Faiss Lloyd versus CPU E-means

Status: `EXECUTED` synthetic smoke, `DEFER`, `production_activation: false`.
No production ANN implementation or default is changed.

## Historical v1 evidence is invalid for comparison

The original files under `artifacts/r5-emeans-vs-lloyd/` are preserved byte for
byte as historical evidence, not rewritten to the new schema. Their disposition
is `INVALID_FOR_COMPARISON`: Lloyd's default `max_points_per_centroid=256`
subsampled 2,048 of 4,096 training rows, while E-means used all rows. Moreover,
E-means' moving final-pass labels were used for MSE rather than reassigning
against its final centroids. The old training-time ratio and method-quality
comparison are withdrawn. Importing a real E-means implementation was confirmed;
the previous numbers did not establish a fair comparison.

The [R5 evidence disposition manifest](2026-11-20-r5-evidence-dispositions.json)
preserves the raw, sidecar and receipt hashes and records the revoked claims,
allowed historical meaning, forbidden uses and corrected v2 receipt binding.
`validate-r5-evidence-dispositions.py` rejects `PRIMARY` and production-decision
use by content hash, including renamed copies. Original historical JSON and
SHA-256 sidecars are not rewritten.

## Corrected v2 experiment

The executable uses pinned `faiss-cpu==1.16.0` and `numpy==2.4.6`, one Faiss
thread and pre-import BLAS/OpenMP thread limits of one. `NumpyEmeans` is the real
CPU implementation from `faiss.contrib.e_means`; Lloyd is not relabelled as
E-means, and neither result represents O-means.

The deterministic mixture has 16 dimensions and eight centroids. It separately
generates 4,096 train, 4,096 base, 128 query and 1,024 held-out rows, records
little-endian FP32 hashes, and checks all six cross-split intersections for
byte-identical rows. Query and held-out rows never enter training. The
independent NumPy FP64 squared-L2 oracle uses base-ID tie breaking; its exact
top-100 is cross-checked with `IndexFlatL2` before evaluating IVF.

Both methods receive the same complete training matrix and initial centroids.
Lloyd's `max_points_per_centroid=4096` prevents hidden subsampling. The common
budget is 40,960 row-to-codebook assignments, or 327,680 centroid-distance
evaluations. For Lloyd it is derived from full-row input and the observed ten
iterations; the E-means `_assign_rows` input rows are counted during execution.
Ten passes happen to coincide with this measured assignment-work budget here.
Equal epochs alone are not called equal work: update/revival arithmetic,
memory traffic and backend overhead differ, and wall time is not matched.
Four measured training runs alternate method order, after one excluded warmup
per method. All repeats must produce identical centroids.

Final train and held-out MSE and occupancy are independently recomputed against
the final centroids. IVF quantizers receive those centroids directly, without a
second training call. Exact installed-centroid digest and list membership are
checked before search; the digest must remain unchanged after search. Index
construction includes these diagnostic checks and is not a pure build-kernel
timing. Train and base occupancy and imbalance factor (`k * sum(count^2) / N^2`)
are recorded separately. Serialized index bytes, base-vector array bytes and
centroid array bytes are recorded; peak RSS is explicitly `NOT_MEASURED`.

## Two distinct retrieval measurements

`native_ivf` measures actual `IndexIVFFlat.search(..., 100)` at `nprobe` 1/2/4/8.
It resets `faiss.cvar.indexIVF_stats` for every measured query, records the native
`ndis` counter and requires equality with the sum of probed list sizes. It records
per-query recall@10/@100, three raw warm
timings per query, and p50/p95. One per-query warmup is excluded. These rows do
not have equal scan budgets and are not compared as equal-work speedups.

`equal_scan_proxy` separately routes through preassigned centroid lists, takes
exactly 256/512/1024/4096 base IDs per query, and reranks that candidate set
with independent FP64 squared L2. Lists are ordered by centroid distance; the
boundary list is truncated by stable base ID, not by hidden exact scores. The
timing includes Python routing, candidate materialization, exact scoring and
selection, but excludes list construction. This is a capped-candidate
research proxy, **not native IVF latency or a production retrieval path**.

## Local corrected observations

| Method | Final train MSE | Held-out MSE | Training p50 (ms) | Native 1-probe recall@10 / @100 | Serialized bytes |
| --- | ---: | ---: | ---: | ---: | ---: |
| Faiss Lloyd | 8.111261 | 8.872920 | 4.49 | 0.953125 / 0.935234 | 295,627 |
| CPU `NumpyEmeans` | 8.110503 | 8.870321 | 111.84 | 0.950000 / 0.935078 | 295,627 |

At a cap of 512 both methods reached recall@10 `0.847656` and recall@100
`0.858828`; at 1,024 both reached `0.985156` / `0.982266`. Full scan achieved
recall@10 and recall@100 of one in both measurement scopes. This small smoke
does not establish a meaningful quality advantage, timing significance, or a
representative R4 result. Repeated in-process timing samples are not
independent requests and do not establish stable p99 latency.

## Evidence and validation boundary

Full new evidence is kept outside the repository and in per-head CI artifacts;
large generated dumps are not committed. The receipt binds raw
output by SHA-256 and a checked sidecar, exact source-file hashes, configuration,
split hashes, software versions, actual execution checkout and a separately
declared expected PR HEAD. A local pre-commit receipt may have
`expected_pr_head_sha: null`; that means unassigned, not a fabricated exact-head
claim. Its source hashes bind the executed uncommitted scripts. CI explicitly
checks out its expected PR HEAD; a non-null expected HEAD must equal the actual
checkout and its committed script hashes before training starts. Runtime and
platform, Faiss compile options and NumPy build/BLAS configuration are recorded.

The [local source-bound receipt](2026-11-19-r5-emeans-vs-lloyd-v2.receipt.json)
and adjacent `.sha256` record clean source `6272e7b2`. Subsequent archival/docs
commits do not change that historical tested source identity.

The validator regenerates split hashes, replays training, recomputes final MSE,
occupancy, IVF sizes, native results/scans and capped-proxy recall, and checks
timing summaries against raw samples. Twelve negative controls re-sign
corrupted payloads before testing semantic rejection (MSE, centroid/corpus
digest, work count, recall, scan cap, result digest, timing, source binding,
exact checkout, execution status and Faiss version), plus raw-byte corruption
(13 controls total). This establishes
the fixture's executable contract, not independent scientific replication or
real-corpus generalization.

PR #638 owns the IVF-training control; PR #641 remains the feasibility and
ownership record. This follow-up does not rewrite their historical evidence.
O-means, GPU training, representative frozen R4 data, portable native serving
and production adoption remain untested. Disposition: `DEFER`.

R5 closure records the representative comparison explicitly as
`NOT_EXECUTED: REPRESENTATIVE_CORPUS_UNAVAILABLE`. This does not mean that the
repository has never used a representative corpus: the existing
[DE-1M source recovery receipt](2026-09-24-canonical-de1m-source-recovery.json)
records one million base vectors, 25,000 training vectors and 305 canonical
queries at dimension 384, with frozen hashes. On 2026-11-20 both recorded
payload locators were absent: the canonical
`E:/_repoz/agent-memory-workspaces/canonical-de1m-source/payload` and the legacy
`E:/_repoz/agent-memory-cpp/tmp/native-ann-confirmation-v1/de-1m/e5`. The older
152-query Prototype-IVF manifest is historical calibration, not the fresh
305-query source. Source receipts/hashes are available, but the checked
payloads are not; no current disjointness or representative training claim is
inferred from them. The 36-document/12-query MiniLM fixture is a regression
input, not representative training. This bounded closure does not download a
new corpus or add a dependency merely to produce a positive comparison.
The executed v2 synthetic smoke remains `EXECUTED`, separately from that
unexecuted representative evaluation.

Reproduce in an isolated environment (use `Scripts/python` on Windows or
`bin/python` on POSIX):

```text
python -m venv .venv-r5-emeans
.venv-r5-emeans/Scripts/python -m pip install faiss-cpu==1.16.0 numpy==2.4.6
.venv-r5-emeans/Scripts/python tools/agent-memory-bench/run_emeans_vs_lloyd.py \
  --output E:/_repoz/agent-memory-workspaces/2026-11-19-r5-emeans-vs-lloyd-v2/emeans.raw.json \
  --receipt E:/_repoz/agent-memory-workspaces/2026-11-19-r5-emeans-vs-lloyd-v2/emeans.receipt.json \
  --sidecar E:/_repoz/agent-memory-workspaces/2026-11-19-r5-emeans-vs-lloyd-v2/emeans.raw.sha256 --threads 1
.venv-r5-emeans/Scripts/python tools/agent-memory-bench/validate_emeans_vs_lloyd.py \
  --raw E:/_repoz/agent-memory-workspaces/2026-11-19-r5-emeans-vs-lloyd-v2/emeans.raw.json \
  --receipt E:/_repoz/agent-memory-workspaces/2026-11-19-r5-emeans-vs-lloyd-v2/emeans.receipt.json \
  --sidecar E:/_repoz/agent-memory-workspaces/2026-11-19-r5-emeans-vs-lloyd-v2/emeans.raw.sha256 --self-test
```
