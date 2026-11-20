# R6.0 — retrieval algorithm inventory and bounded research entry gate

Date: 2026-11-20. Status: source inventory complete; new algorithm experiments
`NOT_EXECUTED`. Baseline: `main@9005a010b1044e1c93ef3690d41fd56a2c7bbb2e`,
tree `1ca0c789b48016804f9ca521ce6d61c517567333`.

## Question and boundary

Which genuinely untested mechanisms warrant a bounded R6 experiment after
R5 closure? Expected outcome: reuse existing algorithms and reject duplicate
proposals before implementing anything. Actual outcome: one certificate pilot
is eligible for correctness work; two routing ablations remain conditional on
verified inputs. This is an inventory, not a new registry, production policy,
public interface, storage requirement, or claim of algorithmic novelty.

The existing [research registry](2026-11-18-algorithm-research-sweep.md) retains
its historical audit identities and separate execution ledger. The
[evaluation ladder](../evaluation-roadmap.md#evaluation-ladder),
[retrieval coverage audit](../retrieval-roadmap-coverage.md) and
[retrieval execution owner](../retrieval-execution-roadmap.md) remain the
authorities for evidence scope and implementation maturity.

## Actual-main acceptance evidence

R5's [final closure report](https://github.com/LimiNode/agent-memory-cpp/pull/671#issuecomment-7028509810)
records all eleven merges, unchanged production source and independent replay.
For this exact baseline:

- [Full CI 44677297465](https://github.com/LimiNode/agent-memory-cpp/actions/runs/44677297465):
  32 successful jobs, no skipped/failed jobs; full configured CTest/JUnit
  equality, 393/393 tests.
- [Packaging 44677302422](https://github.com/LimiNode/agent-memory-cpp/actions/runs/44677302422):
  6/6 successful jobs on the same source SHA.
- [Combined artifact 14603880946](https://github.com/LimiNode/agent-memory-cpp/actions/runs/44677297465/artifacts/14603880946):
  receipt SHA-256
  `171b6e8eb10af6a1a6723d27292f2ab1765db4df064ee3655becc4c9d167e2a4`;
  source archive SHA-256
  `be6366664c44c70c94563e4ca4368b1a203b41c3013e3c862e3218bd01041d83`.
  Retention ends 2027-02-18; this is not a permanent evidence release.

The receipt binds all 2,297 source blobs and 64 artifact files. Independent
replay covered seven Linux/Windows lexical, admission and vector records plus
CPU E-means, with original downloaded bytes unchanged. This inventory read the
original receipt and recomputed its digest; it did not rerun those benchmarks.
The tracked R5 closure note retains its earlier `IN_PROGRESS` snapshot; the
linked final report and exact-run artifact carry the subsequent completion.

These gates provide contract/oracle and bounded component evidence (ladder
rungs 1–3). A complete `search()` or synthetic `retrieve()` interval is not
automatically rung 4 ingestion-to-context evidence or rung 5 operational replay.
No new R6 latency, workload-frequency, applied-quality or lifecycle result is
claimed here.

## Existing implementation and reuse map

| Surface | Mechanism present at the baseline | Maintained control / limitation |
| --- | --- | --- |
| Exact vector | [`ExactVectorIndex.cpp`](../../src/agent_memory/index/ExactVectorIndex.cpp) scans metadata-eligible records; compensated cosine numerator, checked finite scores, deterministic score/ID ordering and partial top-K sort | Actual-index [R5 vector qualification](2026-11-19-exact-vector-index-decision.md), independent Decimal oracle and cancellation regression. `KEEP CURRENT`; kernel-only reduction controls are not replacement indexes. |
| Exact lexical | [`ExactLexicalIndex.cpp`](../../src/agent_memory/lexical/ExactLexicalIndex.cpp) scans records, filters, computes BM25, materializes positive-score hits, full-sorts and truncates | [R5 top-K study](2026-11-19-exact-lexical-topk-decision.md): source-bound private variants and seven directly timed stages. `CONDITIONAL` small-K research; production `KEEP CURRENT`. This class has no postings candidate iterator to which WAND can simply be attached. |
| Flat binary control | [`FlatBinarySignatureIndex.cpp`](../../src/agent_memory/index/FlatBinarySignatureIndex.cpp) computes all stored-signature Hamming distances, applies metadata eligibility, finds a distance-histogram cutoff, and sorts selected records by distance/ID | Reuse as the exhaustive signature-space control for multiprobe/MIH. Exact Hamming ordering does not imply exact dense-vector ordering or acceptable semantic retrieval quality. |
| Core multiprobe | [`MultiProbeHammingIndex.cpp`](../../src/agent_memory/index/MultiProbeHammingIndex.cpp) uses radius 0–2 buckets, per-query `seen(N)`, candidate-target stopping, Hamming scoring and partial sort | Existing [Hamming hot-path harness](../../tools/agent-memory-bench/hamming_hot_path.cpp). Exact ordering is over discovered candidates; the bounded probe contract is not global exact nearest-neighbor recall. |
| Native MIH research | [`mih_native_hot_path.cpp`](../../tools/agent-memory-bench/mih_native_hot_path.cpp) and [`mih_native_sparse_arbitrary_m.cpp`](../../tools/agent-memory-bench/mih_native_sparse_arbitrary_m.cpp) already implement fixed/arbitrary partitions, two-pass/streaming generation-array dedup and global-exact Hamming coverage certificates | Reuse these baselines. The sparse runner has a strict unseen-distance stopping bound and flat conformance checks. Generation state or certified early stopping alone would not be new MIH research; this is not a production `MultiProbeHammingIndex` implementation. |
| Packed scalar/THQ | [`thq_fastscan_v3_benchmark.cpp`](../../tools/agent-memory-bench/thq_fastscan_v3_benchmark.cpp) already compares byte LUT, unrolled byte, pair FP32, pair SIMD and local/global-u8 layouts; [`native_thq_full_scan.cpp`](../../tools/agent-memory-bench/native_thq_full_scan.cpp) provides a native packed full-scan path | [FastScan v3](2026-09-21-thq-fastscan-v3.md), existing packed-codec controls and [progressive ADC](2026-09-11-progressive-thq-aosoa-roadmap.md) are prior art within this repository. Approximate u8 ordering is not bitwise/exact FP32 pair ordering. |
| Hybrid/fusion | [`HybridRetrievalEngine.cpp`](../../src/agent_memory/retrieval/HybridRetrievalEngine.cpp) executes analyzer → lexical index → reranker → protected-result admission → response | Despite its name, this engine is not an implemented lexical+dense RRF executor. Hybrid/RRF is `Contract only` in the coverage audit. Metadata propagation does not establish authorization or pinned-revision freshness. |
| Reranking/admission | The same engine admits finite reranker scores only for unique upstream identities with unchanged protected metadata/source fields; production lookups are linear | [R5 full-path admission](2026-11-19-production-reranker-admission.md) compares source-bound linear/sorted/hash variants and direct stages. Production `KEEP CURRENT`; dispatcher `DEFER`. Repeated synthetic sizes are not a real request distribution; allocation totals are not peak RSS. |

## Keep serving modes and historical conclusions separate

The evaluation owner distinguishes **full-flat packed 1M**, **Prototype-IVF /
balanced cascade**, and **Modern R4 cascade**. Candidate-local or fixed-top128
scoring is component evidence, not a fourth serving mode. Routing, gather,
packing, shortlist selection, final rerank, materialization and I/O must retain
their stated scopes; results must not be transferred between modes.

- [R1–R5 sweep history](2026-11-18-algorithm-research-sweep.md) retains negative
  evidence for Hamming as a final ranker, label-free learned ADC, equal-payload
  uncertainty/two-plane controls and tiny-corpus multiprobe selection. Python
  ANN timing does not license production HNSW. Early superseded codec reports
  are historical, not current decision evidence.
- [FastScan v3](2026-09-21-thq-fastscan-v3.md) measured a dense-layout advantage
  that did not transfer to random-ID R4 gathering/packing. Global-u8 top128
  membership changed; surviving final top10 on exposed queries does not make
  the quantized stage exact or supply untouched held-out confirmation.
- [Actual-R4 K8 frontier](2026-09-01-neuroute-actual-r4-codec-frontier.md)
  rejected all 24 fixed K1/K2-shortlist → K8-refinement points under its
  registered gates. It did not reject every incumbent-dependent safe bound or
  every prototype-routing family. Do not repeat that fixed-shortlist sweep.
- [Replication topology](2026-08-30-neuroute-replication-topology.md) rejected
  nearest-secondary, SOAR-style and training-co-occurrence global copies at
  matched unique-candidate work: heavier postings reduced address breadth.
  [The scale pilot](2026-09-12-semantic-routing-scale-r4-gate.md) did not test
  boundary-selective, load-capped placement. A new ablation must charge raw
  postings and deduplication, not just unique candidates.
- R5 kept compensated cosine, deferred E-means adoption, and retained lexical
  and admission defaults. Its [v1 disposition manifest](2026-11-20-r5-evidence-dispositions.json)
  marks E-means v1 `INVALID_FOR_COMPARISON` and vector v1 `SUPERSEDED` without
  rewriting historical bytes. None of those decisions is a new ANN quality
  result or a reason to reopen another compander/Lloyd/E-means sweep.

The input audit recovered the minimal FastScan codes, thresholds and query
payloads under canonical `.artifacts/legacy/` and `.artifacts/legacy-tmp/`.
This inventory independently recomputed their sizes and SHA-256 hashes,
matching the existing [dense v3 audit](2026-09-21-thq-fastscan-v3-dense.audit.json):

| Canonical-checkout-relative payload | Bytes | SHA-256 |
| --- | --- | --- |
| `.artifacts/legacy/canonical-thq4-table-v1/thq4-document-codes.u8` | 96,000,000 | `0a0c825720bccef97a0fd1af5c7727671b5e0a79be09b643e0fcb558236e2b70` |
| `.artifacts/legacy-tmp/thq-full-scan-v1/thq4-quantile-thresholds.f32` | 4,608 | `ceaa7ed312525bfa81a96b9f53aa79fb7f210387eb190a752051165dbdbd37a4` |
| `.artifacts/legacy/fidelity-heavy-batch-v1/inputs/queries.f32` | 233,472 | `abe14a8790bd488fc91b01b4b1d6ab664db1d2f2d69e67147ed8439f54c73191` |

The local locator/packing manifest is
`.artifacts/r6-actual-main-inputs/verified-fastscan-inputs.json` under the
canonical checkout root; it is not a committed dataset or permanent release.
Inspected release metadata does not establish a published asset containing
these exact three payloads. Public full-source computational replay remains
`NOT_ESTABLISHED`; local hash verification is not dataset publication.

This is verified payload availability, not a rerun of the old
benchmark or execution of a new R6 arm. The frozen first eight dense queries
are eligible for candidate-1 correctness/mechanics only. The old 8 dense /
152 R4 queries and previously used 305-query evaluation are exposed, not a
new sealed test set. Fresh sealed quality evidence remains unavailable.
`PENDING_SOURCE_REPLAY` applies where required inputs are still missing;
it is independent of the engineering decision `DEFER`. Compact historical
reports are not substitutes for either inputs or new execution evidence.

## At most three falsifiable hypotheses

All three are `NOT_EXECUTED` here. The pilot's numerical convention and
reproducibility contract belong in its own experiment note before code.

| Hypothesis | Reused control and narrow new mechanism | Falsifier / stop rule | Next decision |
| --- | --- | --- | --- |
| Certified quantized-LUT refinement | Existing FastScan `score_pair` sequence and deterministic score/ID ordering. Test whether outward-safe bounds from actual quantization residuals allow u8 screening followed by exact pair-score refinement. | Any escaped reference score or ordered-ID/score-bit mismatch rejects correctness. Refine boundary ties; charge setup, selection, gather/packing and duplicate layouts. Near-exhaustive refinement or no preregistered full-path gain means `KEEP CURRENT`. | Eligible for a small correctness pilot using hash-verified, exposed payloads. Exact means relative to this FP32 ADC reference, not raw-vector retrieval or byte-kernel bit equivalence. Performance is `NOT_EXECUTED` here; applied quality remains `DEFER` without fresh sealed inputs. |
| Incumbent-dependent K8 ball-bound screening | Exact K8 reference and existing K1/K2 refinement/Prototype-IVF controls. Test a conservative upper bound using actual normalized prototype vectors and a real incumbent, rather than a fixed shortlist. | Any ordered-address mismatch rejects the arm. Include FP32 reference error, conservative radius, ties and prototype replacement; approximate INT8 K1 is not an exact bound. Stop loose bounds or no matched total-path gain. | `DEFER` until source payloads and a comparable downstream contract are verified. This is a bound ablation, not a newly named PRR family. |
| Sparse, load-capped IVF replication | Existing single assignment and ordinary nearest-secondary placement at matched storage; compare boundary-only eligibility with boundary-plus-load caps and matched random eligibility. | No held-out Pareto improvement after charging raw postings/deduplication, or improvement explained solely by extra work, rejects the tested ablation. Freeze centroids/scoring; do not repeat unconditional r=1/2/4. | `DEFER` until FP32 inputs, memberships, centroids, routing streams and separate evaluation inputs are verified. No persisted-layout or production IVF API proposal follows from this inventory. |

WAND/MaxScore, core multiprobe specialization and hybrid/adaptive fusion are
not additional active hypotheses. WAND first needs valid upper bounds and a
measured scoring/traversal bottleneck; fusion needs a sealed tuning/evaluation
split. Changes to physical postings or storage remain a separate requirement
for the storage owner, not part of this research inventory.

## Checks and next bounded step

This slice performed graph/source/document discovery, exact-baseline inspection,
receipt/input digest inspection and Markdown/diff checks only. No benchmark, new
quality evaluation, dependency install, registry edit, production change,
merge, release or tag was performed. Publication of this note is tracked by
its own Draft PR, not counted as new experiment evidence.

Historical workspace paths remain discovery locators. New output placement
follows [workspace layout](../workspace-layout.md): registered `.worktrees/`,
canonical-root `.build/`, manifest-backed `.artifacts/` and disposable `.tmp/`.
This inventory performs no migration or cleanup.

Next: use the verified minimal inputs for only the smallest certificate
counterexample/control in an isolated research worktree. Keep
correctness, retrieval quality, component performance and operational readiness
as separate results; stop or defer a hypothesis without inventing evidence.
