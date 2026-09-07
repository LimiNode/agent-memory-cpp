# Experiment Notes

`guides/experiments/` stores human-readable experiment records. These notes are
not raw benchmark dumps; they are compact research logs that explain why a run
was performed, what was expected, what happened, and what should be checked
next.

For the cross-PR causal history and merge/evidence ledger, see
[`research-timeline.md`](research-timeline.md). It is an index into the
experiment notes, not a replacement for them.

## When to write or update a note

Create or update an experiment note when a PR:

- tests a hypothesis;
- compares algorithms, encoders, indexes, storage layouts, or benchmark
  methodology;
- produces benchmark numbers that influence the roadmap;
- changes the interpretation of earlier benchmark results.

Create one note per research line, not per command invocation. If a later PR
continues the same question, append a new dated section instead of overwriting
earlier results.

## Required contents

Each note should include:

- date and PR/commit context;
- question or hypothesis;
- setup and command/config references;
- expected result;
- actual result, preferably with compact tables;
- interpretation;
- limitations and threats to validity;
- possible improvements;
- follow-up checks.

## Raw artifact policy

Do not commit large generated JSON reports by default. Commit only:

- small, stable smoke fixtures;
- example configs;
- manually curated tables or short excerpts needed to support the note.

When raw reports matter, store the command, config path, output path, git head,
and enough identifying metadata for reproduction. If a future PR needs
long-term raw artifact retention, add an explicit policy for artifact location,
size budget, and cleanup before committing dumps.

## Evidence releases

Long-lived compact research evidence belongs in GitHub Releases, not in Git
and not in the short-lived Actions artifact store. Library releases use normal
semantic tags and names such as `v0.2.0` / `agent-memory-cpp 0.2.0`. Research
releases must use an evidence namespace and a visually distinct title, for
example `evidence/mih-banding-v3` / `[Evidence] MIH banding cascade v3`.

An evidence release is published only after its archive validator succeeds.
It must state its archive SHA-256, internal bundle-root SHA-256, target commit,
and artifact scope. Draft releases are review staging only and must not be
called public in experiment notes. Do not commit large ZIPs or generated DB
files; retain a compact manifest and a stable release link instead. If evidence
outgrows practical GitHub Release size or count, publish it to an archival
service and retain the manifest plus external link here.

## Timing methodology

Experiment notes must distinguish:

- data generation;
- exact baseline build and query timing;
- encoder training/cold-start timing;
- binary materialization/build timing;
- query encoding;
- candidate search;
- exact rerank;
- process-wide memory high-water marks.

Timing values from a single local run are directional. Treat them as stable
benchmark evidence only after the harness uses repeated runs, warm-up rules,
fixed environment notes, and preserved raw outputs.
## Latest PCA12 routing follow-ups

- [Diversity-aware centroid routing](2026-09-06-pca12-routing-diversity-centroid.md)
- [Weighted-kNN cell routing](2026-09-06-pca12-weighted-knn-routing.md)
- [Direct4096 data scaling](2026-09-06-direct4096-data-scaling.md)
- [Centroid-prior/Hungarian set router](2026-09-06-centroid-prior-hungarian-set-router.md)
- [Routing synthesis and native-cascade status](2026-09-06-pca12-routing-synthesis.md)
- [Native document-routing full-cascade bake-off](2026-09-06-native-document-routing-bakeoff.md)
- [Flat compact codes and FP32-free final rerank](2026-09-06-flat-code-and-fp32-free-final-rerank.md)
- [R4, flat THQ and product-profile synthesis](2026-09-06-r4-flat-product-synthesis.md)
- [THQ-aware directional MIH triage](2026-09-06-thq-mih-directional-triage.md)
- [Directional THQ geometry oracle](2026-09-06-thq-direction-geometry-oracle.md)
- [Teacher-direction THQ transition-cost oracle](2026-09-06-thq-transition-cost-oracle.md)
- [Prototype-direction THQ transition-cost oracle](2026-09-06-thq-prototype-transition-cost-oracle.md)
- [Multi-anchor THQ transition-cost oracle](2026-09-07-thq-multianchor-transition-cost-oracle.md)
- [Continuous THQ ray/segment geometry oracle](2026-09-07-thq-ray-segment-oracle.md)
- [Runtime-anchor and spherical THQ geometry oracle](2026-09-07-thq-runtime-anchor-and-spherical-oracle.md)
- [Prototype-IVF anchor recall oracle](2026-09-07-prototype-ivf-anchor-recall.md)
- [Nearest-E5 anchor inside IVF pool](2026-09-07-ivf-pool-nearest-anchor-ray.md)
- [Anchor-routing synthesis and next gates](2026-09-08-anchor-routing-synthesis.md)
- [Bounded best-anchor-in-pool pilot](2026-09-08-bounded-best-anchor-ray.md)
- [Prototype-to-document expansion ceiling](2026-09-08-prototype-document-expansion.md)
- [Document-conditioned prototype target replay](2026-09-08-document-conditioned-prototype-target.md)
- [Downstream best-anchor screen](2026-09-08-downstream-best-anchor-screen.md)
- [Quota multi-anchor downstream oracle](2026-09-08-quota-multianchor-oracle.md)
- [Broad shared-alpha anchor oracle closure](2026-09-08-broad-shared-alpha-oracle.md)
- [THQ-aware IVF comparison](2026-09-08-thq-aware-ivf.md)
- [Native flat THQ versus E5-IVF bake-off](2026-09-08-native-thq-ivf-bakeoff.md)
