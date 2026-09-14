# Native K1/coarse → INT8 K16 → R4 → THQ → exact quality (2026-09-14)

## Question

Does the positive logical mean-of-K16 coarse/refine frontier survive the native
INT8 scorer and the complete three-seed cascade, including THQ interval-squared
reranking and final exact reranking?

## Setup

- frozen DE-1M: 1,000,000 documents, 152 queries, 384 dimensions;
- three semantic R4 seeds (`2026082701/02/03`);
- native full occupied-address coarse scan, then INT8 K16 refinement;
- `A ∈ {128, 256, 512, 1024, 2048, 4096, 8192, 16384}` addresses per seed;
- three score streams fused by descending address score with global unique-document
  budgets `5k/10k/20k/50k` and whole-posting reads;
- THQ interval-squared top-256, followed by exact FP32 reranking of only those
  THQ candidates; exact top-10 is retained as the final cascade output;
- teacher IDs are evaluation-only and are not used by routing or reranking.

Short prefixes that cannot reach a requested budget are recorded as
`budget_exhausted` snapshots and are never reported as full-budget points.

## Results

Mean teacher recall (candidate, THQ top-256, and exact top-10; the latter two
are identical here because THQ did not remove any teacher at these points):

| A/seed | @5k | @10k | @20k | @50k |
| ---: | ---: | ---: | ---: | ---: |
| 128 | .6112 | .6112* | .6112* | .6112* |
| 256 | .7414 | .7414* | .7414* | .7414* |
| 512 | .8447 | .8461 | .8461* | .8461* |
| 1024 | .9191 | .9197 | .9197 | .9197* |
| 2048 | .9572 | .9592 | .9599 | .9605 |
| 4096 | .9842 | .9855 | .9862 | .9882 |
| 8192 | .9941 | .9941 | .9941 | .9947 |
| 16384 | .9974 | .9974 | .9974 | .9974 |

`*` marks a row where at least one query exhausted the selected address
prefix before reaching the requested unique-candidate budget. Such rows are
achieved-prefix evidence only; the authoritative full-budget frontier is
`A=8192` or `A=16384`.

At `A=8192`, the native integrated route reaches `.9941 @ 5k` and `.9947 @
50k`; at `A=16384` it reaches `.9974` at every budget. The independent audit
recomputed all 4,864 row recalls from the stored IDs, verified exact IDs are a
subset of THQ top-256, checked the exhaustion semantics, and recomputed all 32
summary aggregates.

The native component is not an end-to-end latency claim. For one seed, the
full coarse pass has p50 about 36.8 ms/query; INT8 refinement p50 is about
45.6 ms at A=8192 and 94.3 ms at A=16384. Across three seeds these arithmetic
costs are approximately three times larger before posting reads, fusion, THQ,
or exact work.

## Interpretation

The earlier logical result is reproduced by the native cascade: the strong
quality frontier is not an artifact of the Python scorer, and THQ interval² plus
exact reranking does not cause an observed teacher-recall drop. The remaining
bottleneck is route generation: a full occupied-address coarse pass plus K16
refinement, not downstream THQ scoring. This is evidence for a strong routing
oracle, not yet a deployable MDBX design; physical pages, cache behavior, and
end-to-end latency were not measured.

The `A=8192` point is the practical quality/control arm. Smaller prefixes are
not competitive at the same budgets, while A=16384 buys only a small quality
increase at roughly double the refinement work.

## Limitations and follow-up

- The native kernel is scalar INT8 arithmetic; SIMD and physical layout remain
  separate controls.
- The experiment does not compare K8/K16/K32 on the full occupied-address
  route. That bake-off is the next gate, with FP32-vs-INT8 quality parity and
  representative work measured independently.
- The final exact top-10 is a teacher-ID recall control; graded qrels/nDCG are
  not present in this frozen DE-1M fixture.
- If full K16/K32 arithmetic remains too expensive, run a global representative
  top-R oracle before introducing a representative-layer ANN index.

## Provenance and audit

Raw output and receipt are retained outside Git under
`E:\\_repoz\\agent-memory-workspaces\\r4-k1-integrated-native-quality-raw`.

| artifact | SHA-256 |
| --- | --- |
| runner | `498327c92220faaa4ea08cddc8154aea4cfda44987fa280b2b65fb1130ef92cb` |
| audit | `439e901146c5c433ebeafde4c81947802cbc681cc17ccb0c66707774f1b62aae` |
| native harness source | `88798429a1e8a49d816cf4d423c6b3dda2468b7ffbd5c83d1308b1c26a757ecf` |
| frozen fixture manifest | `f58e074e481dc910ca7b12b35bc27dc51097640704fb2b0c749018bcf2edd57b` |
| integrated raw JSON | `fbb18589de77e10d06be66a3a7f0dba7994b2659aeae0bdf58b5b746382ddbe7` |
| integrated receipt | `dba2e9c330c45be668e11d03377328c6253cfce70657c2145ced2cf4b6975663` |

The independent audit command is:

```powershell
python tools/agent-memory-bench/audit-r4-k1-integrated-native-quality.py `
  --receipt E:\\_repoz\\agent-memory-workspaces\\r4-k1-integrated-native-quality-raw\\integrated.receipt.json `
  --raw E:\\_repoz\\agent-memory-workspaces\\r4-k1-integrated-native-quality-raw\\integrated.raw.json `
  --native-executable E:\\_repoz\\agent-memory-workspaces\\r4-k1-integrated-native-quality\\build-r4-k1\\tools\\agent-memory-bench\\Release\\agent-memory-neuroute-r4-k1-coarse-refine.exe `
  --thq-manifest E:\\_repoz\\agent-memory-cpp\\tmp\\thq-full-scan-v2\\manifest.json
```
