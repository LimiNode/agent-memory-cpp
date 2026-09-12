# Superseded initial semantic posting oracle

Status: SUPERSEDED HYBRID CONTROL (corrected by
`2026-09-12-semantic-k8-r4-spann-oracle-corrected.md`). Date: 2026-09-12.
This experiment tests whether a semantic coarse partition
can turn the strong full-dimensional THQ-ADC geometry into compact postings.
It is an in-memory oracle: no MDBX backend, physical page claim, payload
rerank, or production activation is implied.

Documents are assigned to the nearest `K={64,128,256,512}` raw-dot centroids,
with replication `r={1,2,4}`. Queries probe `nprobe={1,2,4,8,16,32}` cells.
The index is built from document vectors only; frozen teacher IDs are used only
for evaluation. Metrics separate posting entries touched, deduplicated
candidate documents, replication footprint, and teacher top-10 recall.

The runner and receipt are `run-semantic-kmeans-routing-oracle.py` and
`2026-09-12-semantic-k8-r4-spann-oracle-result.json`. This is the semantic
control against which a verified R4/K8/K32 mapping can later be compared under
matched candidate budgets (5k/10k/20k/50k/100k). A positive generator gate is
required before any MDBX or native page materialization.

## Full replay result

The 152-query replay used a deterministic 100k-document training sample and
assigned all 1M documents to the learned centroids. Mean generator results are:

| K | r | nprobe | candidates | posting entries | mean recall | worst recall |
|---:|---:|---:|---:|---:|---:|---:|
| 64 | 1 | 32 | 556,402 | 556,402 | 0.9789 | 0.70 |
| 64 | 2 | 32 | 720,811 | 1,112,071 | 0.9954 | 0.90 |
| 128 | 4 | 32 | 606,304 | 1,226,284 | 0.9961 | 0.90 |
| 256 | 4 | 32 | 382,647 | 651,807 | 0.9862 | 0.80 |
| 512 | 4 | 4 | 49,591 | 54,361 | 0.7901 | 0.00 |
| 512 | 4 | 8 | 87,995 | 103,408 | 0.8783 | 0.20 |

No configuration reaches 0.995 mean recall at or below 50k candidates, and the
best mean-recall configurations still have a 0.90 worst-query floor. Replication
improves recall, but the required candidate frontier remains substantially
larger (for example K=64,r=2 reaches 0.9954 only at about 721k candidates).
The result is retained as a useful hybrid control over raw-coordinate
surrogates, but is not authoritative cosine evidence and is not a positive
R4/SPANN generator gate. THQ-ADC reranking and physical materialization remain
deferred; see the corrected replay for the valid metric arms.
