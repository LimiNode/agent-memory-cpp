# Multilingual Autoencoder Binary Encoder Study

## 2026-07-31 — Protocol established

### Status

Planned. This note records the agreed experiment before external datasets,
embeddings, training checkpoints, or benchmark figures are created. Later PRs
append measured results here rather than replacing this protocol.

### Question

Can a document-only trained autoencoder produce binary codes that preserve
multilingual E5 retrieval quality better than zero-training binary baselines,
without concealing regression in Russian or other individual languages?

### Dataset and split plan

- CI regression fixture: BEIR SciFact.
- Primary study: MIRACL with per-language Wikipedia corpora, queries, and
  qrels. It evaluates monolingual retrieval per language.
- Initial languages: `ru,en,de,fr,es,ar,zh,ja`.
- Training receives only E5 `passage: ` document embeddings; E5 `query: `
  embeddings and qrels are evaluation-only.
- Held-out run: training document IDs do not overlap evaluation document IDs.
  Evaluation corpora retain all qrels-referenced documents plus deterministic
  distractors.
- Checkpoints are selected using document-only validation reconstruction/code
  health, or a predeclared budget, never qrels.

### Expected result

At equal training-vector count, a balanced multilingual mixture should retain
quality across more languages than a RU-only encoder. It might still reduce
Russian quality relative to RU-only; this is a hypothesis to measure, not an
assumption.

### Planned matrix

| Regime | Training documents |
| --- | ---: |
| RU-only | 100k Russian |
| RU+EN | 50k Russian + 50k English |
| Eight-language balanced | 12.5k per selected language |

The primary run follows with 25k documents per selected language and may expand
to 50k only after the smaller run is stable.

### Measurements

For each language and bit budget (64/128/256), record exact E5, binary-only,
and binary-candidate-filter plus exact-rerank quality:

- nDCG@10, Recall@10, Recall@100, and MRR;
- exact-top-k and qrels candidate coverage;
- `relative_retention = compressed_metric / original_e5_metric`;
- macro average, minimum-language score, and maximum relative degradation;
- training/materialization/build/query-encoding/candidate-search/rerank timing
  and memory.

### Threats to validity

- MIRACL does not measure cross-language retrieval.
- A balanced Wikipedia sample is not automatically representative of a
  production memory corpus.
- Local timing results need repeated runs, warm-up, environment details, and
  preserved raw artifacts before they become stable performance evidence.
- A corpus-adaptive run can be useful operationally, but is transductive and
  must not be presented as held-out generalization.

### Follow-up ladder

1. Implement the external MIRACL preparation and manifest validator.
2. Implement offline autoencoder training and versioned artifact export.
3. Load the artifact for dependency-free C++ encoder inference.
4. Run this MIRACL matrix and record results.
5. Evaluate the frozen artifact on Mr. TyDi without retraining.
6. If cross-language retrieval is a product goal, evaluate XOR-Retrieve.

See [multilingual autoencoder evaluation](../multilingual-autoencoder-evaluation.md)
for the normative contract and source links.

## 2026-07-31 — First RU development pilot (128-bit linear AE)

### Status

Completed as an end-to-end pipeline check, not as a quality claim or a member
of the planned equal-budget matrix. The pilot is useful negative evidence: a
small document-only linear autoencoder did not preserve held-out E5 retrieval.

### Setup

- MIRACL Russian dev, qrels-closed evaluation corpus: 13,607 documents, 1,252
  queries, and 13,100 judgments.
- Document-only training source: 2,500 selected E5 `passage: ` embeddings;
  2,358 were training rows and 142 were the deterministic validation subset.
- E5: `intfloat/multilingual-e5-small` at revision
  `614241f622f53c4eeff9890bdc4f31cfecc418b3`, CPU float32, batch size 32,
  eight recorded CPU threads.
- Linear `tanh_sign_ste_v1` autoencoder, 128 bits, seed 42, 20 epochs,
  batch size 256, learning rate `0.001`, quantization weight `0.1`, and
  balance weight `0.01`.
- Artifact SHA-256:
  `d8fb1a44a0250f5cb898ab79520ea64e9d05fe334993b9ecd828b71e3265577c`.
  Materialization manifest SHA-256:
  `9e93b16351a95cf3da7f240f0cdc6f9bdf7a2f77e3c29516b68758ff5ae14245`.

The C++ evaluator compared original-float E5 with Hamming candidate selection
followed by exact cosine reranking. It separately ranked decoder-reconstructed
document vectors against the original float query. `oracle_k` was 10.

### Results

| Candidate limit | Random candidate coverage expectation | Exact top-10 candidate coverage | Coverage lift vs random | Reranked top-10 agreement | Reranked nDCG@10 |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 128 | 0.0094 | 0.0088 | 0.934x | 0.0088 | 0.0108 |
| 512 | 0.0376 | 0.0408 | 1.085x | 0.0408 | 0.0445 |
| 2,048 | 0.1505 | 0.1574 | 1.046x | 0.1574 | 0.1724 |
| 13,607 (full corpus) | 1.0000 | 1.0000 | 1.000x | 1.0000 | 0.8027 |

The original-float E5 oracle achieved nDCG@10 `0.8027`, Recall@10 `0.9350`,
and MRR `0.8144`. Decoder approximation was also ineffective: nDCG@10
`0.00077` and exact-top-10 agreement `0.00096`.

### Interpretation

The full-corpus row recovers the original E5 result, so the exact reranker and
qrels evaluation path agree with the oracle. The loss occurs before reranking:
the 128-bit code trained on only 2,358 document vectors almost never admits
the original nearest neighbours. Increasing the candidate limit helps, but
only by sacrificing most of the candidate-stage reduction.

More strongly, this artifact does not materially outperform a uniformly random
candidate shortlist. For a shortlist of size `C` from 13,607 documents, the
expected coverage of a fixed oracle neighbour is `C / 13,607`. The observed
coverage is 0.934x, 1.085x, and 1.046x that expectation at 128, 512, and
2,048 candidates respectively. The small deviations do not demonstrate a
useful locality signal. Exact reranking therefore improves nDCG only when the
larger shortlist happens to contain more relevant E5 neighbours.

The subsequent diagnostic pass explains why. It found a complete hard-code
collapse rather than merely weak neighbourhood preservation:

| Diagnostic | Held-out RU result |
| --- | ---: |
| Unique document codes / documents | `1 / 13,607` |
| Unique query codes / queries | `1 / 1,252` |
| Constant document bits | `128 / 128` |
| Sampled document/document Hamming mean / stddev | `0.0 / 0.0` |
| Query/document Hamming mean / stddev | `0.0 / 0.0` |
| Cosine vs negative-Hamming correlation | undefined (zero Hamming variance) |
| Mean cosine(document, decode(code)) | `0.85285` |
| Mean cosine(document, cyclically shuffled decode(code)) | `0.85285` |
| Mean decoded-document norm | `0.85430` (zero standard deviation) |

The apparently reasonable decoder reconstruction cosine is therefore not
evidence of retained per-document information: the same decoder vector is
compared with every document, and the shuffled control is identical. This
artifact is a collapsed-code negative control. Its retrieval result must not be
used to compare bit budgets or autoencoder variants.

This result does **not** reject autoencoder binarization in general. It rejects
this undertrained 2.5k-document linear artifact as a useful RU candidate
filter, and it rejects its decoder as a compact replacement for retained float
vectors.

### Algorithm review and required diagnostics

The trainer is a document-only linear autoencoder. For every E5 passage vector
`x`, it computes `z = tanh(Wx + b)`, applies a hard `-1/+1` sign code in the
forward pass with a straight-through gradient, and trains a linear decoder from
that code. Its loss is MSE reconstruction plus quantization and per-bit balance
penalties. The C++ encoder uses the same affine sign threshold; the evaluator
compares all original and decoded vectors with cosine similarity, so decoder
ranking is not accidentally using unnormalised dot products. Queries and qrels
never enter training or checkpoint selection.

That contract is reproducible but is not retrieval-aware: MSE reconstruction
does not directly preserve nearest-neighbour order, and 2,358 training vectors
are far too few evidence for a 128-bit E5 code that must serve 13,607 held-out
documents and 1,252 query embeddings from a different prefix distribution.
Before spending compute on the 25k 64/128/256 grid, the evaluator must emit:

- per-bit occupancy, entropy, constant-bit fraction, exact-code uniqueness,
  and sampled document/document Hamming distribution;
- query/document Hamming distribution and Pearson correlation of E5 cosine
  with negative Hamming distance;
- oracle-neighbour coverage with its random-shortlist expectation and lift;
- document-to-decoded cosine, decoded-vector norms, and a deterministic
  shuffled decoder control.

These are sanity diagnostics, not replacement quality metrics. A candidate
filter should not proceed to an expensive grid unless it shows a meaningful
positive cosine-to-Hamming relation and candidate coverage above random.

### Next baseline: NLB-paper versus project adaptation

The collapsed `tanh_sign_ste_v1` pilot is not a reproduction of Tissier,
Gravier, and Habrard's *Near-Lossless Binarization of Word Embeddings*.
Their method uses a tied decoder `tanh(W^T b + c)`, 0/1 hard codes, no
straight-through encoder gradient, and the stated `0.5 * ||W^T W - I||²`
regulariser. The first replacement experiment must preserve that identity as
an `nlb_paper_tied_v1` artifact family rather than changing the existing STE
family in place.

The paper was evaluated on 300-dimensional word embeddings and 400k--2.3M
training rows, not on E5 query/document retrieval. It is evidence for a
baseline worth testing, not a transfer claim. For 384-dimensional E5 vectors,
the paper-stated Gram target is necessarily rank-deficient at 64/128/256 bits.
Any row-orthogonal or tight-frame objective is a separate project adaptation
with its own artifact family and comparison, not a paper reproduction.

Source: [Tissier, Gravier, Habrard, AAAI 2019](https://aaai.org/ojs/index.php/AAAI/article/view/4692),
[paper PDF](https://cdn.aaai.org/ojs/4692/4692-13-7731-1-10-20190707.pdf), and
[reference implementation (GPL-3.0)](https://github.com/tca19/near-lossless-binarization).

## 2026-07-31 — Preliminary RU NLB-paper tied baseline (128 bits)

### Status

Completed as a methodology replacement check on the same small development
split as the collapsed STE pilot. It demonstrates a non-collapsed locality
signal, but it is not a 25k/full-dev result and must not be compared as a
finished multilingual ablation.

### Setup

- Same E5 materialization, 2,358 document-only training vectors, 142
  document-only validation vectors, and held-out RU evaluation corpus as the
  preceding pilot.
- `nlb_paper_tied_v1`: 0/1 hard step without an STE gradient; encoder weights
  tied to the transposed decoder; clipped `[-1, 1]` input; `tanh` decoder;
  paper-stated `0.5 * ||W^T W - I||²` penalty.
- 128 bits, seed 42, 20 epochs, batch size 75, SGD momentum 0.95, learning
  rate 0.001 with 0.95 per-epoch decay, regulariser weight 1.0.
- Preliminary artifact SHA-256:
  `a4e0594174eff5ef55f950c2c4527365d98ac93b1f875b386f6763f65c4b17bc`.

### Results

| Candidate limit | Random expectation | Exact top-10 candidate coverage | Coverage lift vs random | Reranked nDCG@10 | Reranked Recall@10 |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 128 | 0.0094 | 0.4199 | 44.64x | 0.5280 | 0.5438 |
| 512 | 0.0376 | 0.6265 | 16.65x | 0.6624 | 0.7196 |
| 2,048 | 0.1505 | 0.8465 | 5.62x | 0.7656 | 0.8724 |

The code is non-degenerate: document-code uniqueness is `0.99993`, only
`5 / 128` document bits are constant on held-out documents, mean bit entropy
is `0.4583`, and Pearson correlation between E5 cosine and negative Hamming
distance is `0.3417`. Decoder reconstruction remains a weak compact-storage
path (nDCG@10 `0.1253`, exact-top-10 agreement `0.1119`), so exact float
reranking remains mandatory.

### Interpretation

This is a large methodological improvement over the collapsed STE artifact,
not proof that NLB solves E5 retrieval. The candidate filter now carries
substantial neighbourhood information on this development fixture, but the
remaining quality gap to original E5 nDCG@10 `0.8027` is material. The next
experiment is a fresh current-preparer 25k RU run at 128 bits, followed only
if its health and held-out metrics remain positive by the predeclared
64/128/256 comparison and the multilingual equal-budget matrix.

### Evaluation hardening discovered by the pilot

The first run exposed two contract defects that were fixed before recording
the table:

- the C++ materialized-artifact loader now accepts standard four-field TREC
  qrels (`query Q0 document grade`) emitted by the MIRACL preparer, in addition
  to the older three-column form;
- exact-agreement accounting now distinguishes whole candidate-pool coverage
  from top-K agreement, so decoder agreement cannot be spuriously reported as
  `1.0` merely because its full ranking contains every document.

### Limitations and next checks

- The training set is far below the planned RU-only 100k and primary 25k-per-
  language regimes; it is a development pilot only.
- This run uses a single artifact and seed, one bit budget, and no repeated
  timing protocol.
- The prepared development split predates the bounded-query smoke extension;
  its recorded manifest remains the provenance for this result, but future
  comparable runs must use the current preparer and source-replay validator.

### Predeclared fresh RU 25k stop/go gate

The development materialization is leakage-safe: its `2,500` train document
IDs and `13,607` evaluation document IDs have an exact intersection of zero.
It is nevertheless too small and predates the current preparer contract. The
next evidence point is therefore a fresh, source-replay-validated RU slice:
`25,000` document-only training rows, all RU dev qrels, qrels-closed evaluation
documents plus `10,000` deterministic distractors, and the current E5
materializer recipe.

Train one fresh `nlb_paper_tied_v1` baseline grid at `64`, `128`, `256`, and
`512` code bits with the same candidate budgets. This separates the effect of
code width from the effect of replacing the old development split. The 128-bit
run is the predeclared control point: evaluate it at exactly 512 and 2,048
candidates, then apply `validate-nlb-pilot-gates.py`. The gate decides whether
the study advances to retrieval-aware objectives after the grid; it does not
silently suppress the other three baseline widths.

The following decision contract is executable rather than retrospective
judgment:

| Gate | GO condition | STOP / diagnose condition |
| --- | --- | --- |
| Candidate locality | top-10 coverage@512 >= `0.45`; coverage@2048 >= `0.70` | near-random candidate coverage |
| End-to-end retrieval | reranked nDCG@10@2048 >= `0.90 ×` original-float E5 nDCG@10 | training quality does not transfer to held-out evaluation |
| Geometry | Pearson(cosine E5, -Hamming) >= `0.20` | undefined/near-zero correlation or a narrowly collapsed Hamming distribution |
| Code health | constant bits <= `10%`; unique document codes >= `99%` | constant-bit or collision failure |

The gate consumes only reports from one artifact and one materialization
manifest, so it also rejects accidental mixing of artifacts or dataset splits.
The 512-bit **code** is a controlled point in this baseline grid, not another
candidate-count change. Keep original float vectors for exact reranking;
decoder-only ranking remains an explicitly failed compact-storage baseline
until it passes a separate quality gate.

## 2026-07-31 — Fresh RU 25k NLB-paper bit grid

### Status

Completed. This is the first source-replay-validated result for the current
preparer contract. It replaces neither the small development result nor the
future multilingual ablation; it answers the narrower question of how NLB code
width affects a held-out Russian E5 candidate filter.

### Setup

- MIRACL RU input revisions and the current stable-hash preparer configuration
  from `miracl-ae-study-ru-pilot.example.json`.
- `25,000` prepared document embeddings were split deterministically into
  `23,801` document-only training rows and `1,199` document-only validation
  rows. Train IDs and the `22,607` qrels-closed evaluation document IDs have
  zero overlap.
- Evaluation uses all `1,252` RU dev queries and `13,100` qrels. Queries and
  qrels did not enter training or checkpoint selection.
- E5 is `intfloat/multilingual-e5-small` at revision
  `614241f622f53c4eeff9890bdc4f31cfecc418b3`; materialization manifest SHA-256
  is `cd1987fdef63f5f6b4fd595d312648ea58f85aa502ed982958ebf02e99290e86`.
- All artifacts use `nlb_paper_tied_v1`, seed 42, 20 epochs, batch size 75,
  SGD momentum 0.95, initial learning rate `0.001` with `0.95` per-epoch
  decay, and paper-identity regulariser weight `1.0`.
- Quality is a single deterministic run. It is not a repeated timing or
  multi-seed performance claim.

### Candidate-filter results

Original full E5 reaches nDCG@10 `0.80145`. The table reports exact top-10
candidate coverage, then qrels nDCG@10 after exact float reranking of the
selected candidates. Random coverage expectations are `0.00566`, `0.02265`,
and `0.09059` for 128, 512, and 2,048 candidates respectively.

| Bits | Candidates | Coverage | Lift vs random | Reranked nDCG@10 | Retention of full E5 |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 64 | 128 | 0.1571 | 27.75x | 0.2438 | 30.43% |
| 64 | 512 | 0.3042 | 13.43x | 0.4074 | 50.83% |
| 64 | 2,048 | 0.5423 | 5.99x | 0.5979 | 74.61% |
| 128 | 128 | 0.4658 | 82.27x | 0.5847 | 72.96% |
| 128 | 512 | 0.6632 | 29.28x | 0.6894 | 86.01% |
| 128 | 2,048 | 0.8470 | 9.35x | 0.7603 | 94.86% |
| 256 | 128 | 0.7149 | 126.26x | 0.7362 | 91.86% |
| 256 | 512 | 0.8588 | 37.92x | 0.7780 | 97.08% |
| 256 | 2,048 | 0.9561 | 10.55x | 0.7992 | 99.72% |
| 512 | 128 | 0.9200 | 162.48x | 0.7926 | 98.89% |
| 512 | 512 | 0.9744 | 43.03x | 0.7991 | 99.71% |
| 512 | 2,048 | 0.9956 | 10.99x | 0.8018 | 100.05% |

The 512-bit/2,048-candidate nDCG is `0.00039` above the full-E5 metric. This
does not mean the candidate filter improves exact similarity: a candidate
subset can replace an omitted low-relevance exact top-10 item with a more
relevant lower-ranked item under incomplete qrels. Treat this sub-0.05%
difference as effectively equal, not a quality gain.

### Code health and decoder observation

| Bits | Unique held-out document codes | Constant held-out bits | Cosine vs -Hamming Pearson | Decoder-only nDCG@10 |
| ---: | ---: | ---: | ---: | ---: |
| 64 | 97.45% | 4.69% | 0.2752 | 0.0010 |
| 128 | 100.00% | 0.00% | 0.3746 | 0.1243 |
| 256 | 100.00% | 2.34% | 0.5016 | 0.4169 |
| 512 | 100.00% | 3.12% | 0.6429 | 0.6950 |

The predeclared 128-bit gate passes: coverage@512 `0.6632`, coverage@2048
`0.8470`, rerank retention `94.86%`, Pearson correlation `0.3746`, zero
constant bits, and `99.9956%` unique held-out codes. The 64-bit code remains
meaningful rather than random, but its `97.45%` code uniqueness and low
candidate coverage make it unsuitable for the same quality target.

The cosine-to-negative-Hamming correlation was a useful predeclared collapse
guard for this first run, not a sufficient retrieval-quality objective. Future
comparisons must report it, but use local dense-neighbour preservation,
candidate coverage, and qrels metrics as their primary pass/fail evidence.

The decoder outcome changes the earlier STE-only conclusion but not the
storage decision. A 512-bit decoder reaches nDCG@10 `0.6950` (about 86.7% of
full E5), which is evidence worth studying, but it remains below the
binary-candidate-plus-float-rerank path and is not sufficient to discard float
vectors. Compact decoder-only storage needs its own multi-seed and
cross-language acceptance criteria.

### Interpretation and next check

NLB does not merely avoid the STE collapse: on this fresh held-out RU setup it
preserves a strong, monotonic E5-locality signal. The operating frontier is
clear: 128 bits is a material candidate reduction with a visible quality gap;
256 bits is near-full E5 at 512--2,048 candidates; 512 bits is near-full E5
even at 128 candidates. This validates binary codes as a candidate-generation
layer for this fixture, not yet as a production default or a multilingual
claim.

The following experimental order is intentionally conservative:

1. Repeat NLB at 128, 256, and 512 bits with at least two additional training
   seeds and query-bootstrap confidence intervals.
2. Compare the same held-out fixture with the existing zero- and
   unsupervised-training baselines: raw-coordinate sign, random hyperplanes,
   PCA plus sign, PCA whitening plus sign, and ITQ. This determines whether
   NLB's gain is from learning rather than from binarisation alone.
3. Add bit-information and local-neighbour diagnostics: per-bit occupancy and
   entropy, pairwise bit correlation, effective rank of the bit-correlation
   matrix, Hamming-distance histogram, nearest-neighbour Hamming margin, and
   dense-top-K coverage in binary top-K.
4. Run the deferred in-memory MIH-style band study below. It must measure real
   learned-code bucket behaviour before any MDBX table layout is committed.
5. Compare float-rerank with compact modes: Hamming-only, Hamming shortlist
   plus decoded documents, decoded-query plus decoded-documents, and the
   existing original-query plus original-document exact rerank. Measure the
   quality/latency/storage Pareto for comparable operating points.
6. Only then introduce a retrieval-aware objective. It must use distinct
   document-only, retrieval-training, calibration, and final held-out
   query--qrel splits; the RU dev queries and qrels above remain held-out.
   Separately compare int8/PQ/OPQ as non-binary compression baselines.

### Deferred MIH-style band study

The following is a recorded research design, not an MDBX implementation
commitment. It is motivated by Multi-Index Hashing (MIH): split one binary code
into short substrings, union postings with matching substrings, then score the
union with the full-code Hamming distance. A direct lookup of a full 64-, 128-,
256-, or 512-bit code is an equality lookup; it is not Hamming-neighbour
search.

For `m` disjoint bands, exact lookup of every band guarantees retrieval of a
code within Hamming radius `m - 1`: at most `m - 1` bit differences cannot
damage every band. Radius probing enlarges the candidate set and the guarantee,
but must be evaluated from the actual code distribution rather than a uniform
bucket assumption.

The first in-memory matrix is:

| Code | Band experiments |
| --- | --- |
| NLB-64 | four 16-bit exact bands; four 16-bit radius-1 bands |
| NLB-128 | eight 16-bit exact bands; four alternative 16-bit partitions; eight 16-bit radius-1 bands |
| NLB-256 | eight selected 16-bit bands; all sixteen 16-bit exact bands; several independent partitions |
| NLB-512 | eight or sixteen selected 16-bit bands; do not assume that all thirty-two bands are useful |

Band construction must be learned-code aware. Adjacent NLB bit positions have
no presumed semantic order, so a useful partition may interleave bits chosen
for high entropy, low pairwise correlation, balanced occupancy, and measured
local retrieval utility. The study must keep code quality and band-policy
quality separate, and measure per-band bucket p50/p95/p99/max, empty buckets,
raw and deduplicated candidate counts, duplicate rate, posting bytes, lookup
count, exact-neighbour coverage, and final reranked qrels quality.

Only a favourable and repeatable in-memory result permits an MDBX prototype.
At that point the durable design should use the same full-code artifact for
both short band keys and full Hamming scoring where possible; an independently
trained 64-bit coarse code is a separate hypothesis, not a prerequisite.
Physical key packing, DBI flags, byte order, duplicate-posting representation,
generation handling, deletion/update amplification, and migration rules remain
uncommitted until the current MDBX API and the measured band workload are
evaluated together.

Reference: Norouzi, Punjani, and Fleet, *Fast Exact Search in Hamming Space
with Multi-Index Hashing* (2014), <https://norouzi.github.io/research/mih/>.

## 2026-07-31 — Code-capacity diagnostic and standard-baseline control

### Status

Completed as a diagnostic continuation of the fresh RU 25k NLB grid. The new
metrics are descriptive evidence for choosing the next training experiments;
they are not an acceptance gate and do not make the current standard-baseline
comparison training-budget fair.

### Question

Does the relatively weak 64--256-bit NLB frontier arise from an absence of
useful binary capacity, redundant bits, or merely a failure to preserve local
dense-space order?

### Method

`BinaryCodeHealthMetrics` now reports, in addition to occupancy and sampled
Hamming distance:

- total, p05, median, and p95 per-bit binary entropy;
- mean/p95/p99/max absolute off-diagonal bit correlation on a deterministic
  512-document sample;
- a stable-rank-style participation ratio of that sampled correlation matrix.

The autoencoder evaluation also reports the Hamming distance from each dev
query to its dense rank-1 and dense rank-100 documents, plus their difference.
The rank-100 item is a teacher-geometry control, not a qrels negative label.

### NLB results at 512 candidates

| Bits | Total bit entropy | Fraction of nominal width | Correlation participation ratio | Cosine vs -Hamming | Coverage@512 | Reranked nDCG@10 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 64 | 25.75 | 40.2% | 46.8 | 0.275 | 0.304 | 0.407 |
| 128 | 69.90 | 54.6% | 90.4 | 0.375 | 0.663 | 0.689 |
| 256 | 137.23 | 53.6% | 149.3 | 0.502 | 0.859 | 0.778 |
| 512 | 293.16 | 57.3% | 203.9 | 0.643 | 0.974 | 0.799 |

NLB-128 has no completely constant held-out bits, but that does not mean that
all bits are informative: its entropy p05 is `0.004`, median is `0.620`, and
only `69.90` independent-bit-equivalent entropy units are present before any
bit correlation is considered. Its sampled correlation participation ratio is
`90.4` rather than 128. The 64-bit code is more constrained still.

For NLB-128, the mean query-to-dense-rank-1 Hamming distance is `19.56`; the
mean dense-rank-100 distance is `28.11`; and their mean margin is `8.55` bits.
Only `8.15%` of queries have a nonpositive rank-1/rank-100 margin. Thus the
code does retain local ordering, but it does so with materially less effective
capacity than its nominal width suggests.

### Preliminary 128-bit standard-baseline control

The same held-out RU evaluation at 512 candidates was also run for existing
encoders. PCA and ITQ currently use their deterministic 2,048-vector training
cap, while NLB uses its 23,801-vector document-only split. Therefore this is a
useful diagnosis, not a winner declaration.

| Encoder | Train vectors | Total bit entropy | Participation ratio | Rank-1 to rank-100 margin | Coverage@512 | Reranked nDCG@10 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Random hyperplanes | 0 | 63.5 | 89.7 | 7.64 | 0.592 | 0.655 |
| Randomized Hadamard | 0 | 68.3 | 91.0 | 8.15 | 0.627 | 0.671 |
| Pair-difference projection | 2,048 | 126.1 | 22.7 | 13.02 | 0.549 | 0.619 |
| PCA + sign | 2,048 | 127.6 | 99.6 | 19.53 | 0.931 | 0.793 |
| ITQ rotation | 2,048 | 127.1 | 77.9 | 17.14 | 0.934 | 0.790 |
| NLB-paper tied | 23,801 | 69.9 | 90.4 | 8.55 | 0.663 | 0.689 |

The result supports the capacity hypothesis: PCA+sign has nearly saturated
per-bit entropy and low observed correlation while NLB-128 does not. It also
shows that plain reconstruction is not automatically a better binary-retrieval
objective than a PCA/ITQ geometry baseline.

### Interpretation and next checks

The evidence is sufficient to reject the assumption that `constant_bit_fraction
== 0` proves a healthy code. It does **not** yet identify a training defect in
NLB, and it does not prove that PCA/ITQ will remain superior after a fair
same-document/same-budget comparison.

The next experiments are intentionally ordered:

1. Select one deterministic 2,048-document subset from the NLB stable training
   split and train NLB on exactly those IDs; compare it with PCA and ITQ trained
   on the same IDs.
2. Add the same local-margin diagnostics to every standard encoder report and
   measure 64/128/256/512 on the common fixture.
3. Evaluate post-hoc median thresholds as a separately versioned calibration
   artifact. This tests bit balance without conflating it with a new loss.
4. Only then test ITQ warm-start, balance/decorrelation losses, and finally
   retrieval-aware query--document distillation using a separate retrieval
   training split. RU dev queries and qrels remain final held-out evaluation.

Raw JSON reports remain local under `tmp/`; this note records the
materialization provenance and compact values needed to reproduce the research
decision.

### Post-hoc median-threshold diagnostic

Before changing the NLB training objective, the existing frozen NLB-128 weight
matrix was evaluated with one document-only calibration change:

```text
bit_j(x) = 1[W_j x - median_train(W_j x) >= 0]
```

The median was calculated only from the same `23,801` stable training document
IDs used by the artifact; held-out documents, queries, and qrels did not enter
the calibration. This is not yet a persisted artifact family or a decoder
result. It is a temporary NumPy diagnostic designed to isolate thresholding.

| NLB-128 threshold | Total document entropy | Unique held-out document codes | Exact top-10 coverage@512 |
| --- | ---: | ---: | ---: |
| Existing zero threshold | 69.90 / 128 | 99.996% | 0.6634 |
| Per-bit document-only median | 127.59 / 128 | 99.991% | 0.8883 |

This is decisive evidence that the zero-threshold paper baseline is poorly
matched to anisotropic E5 vectors. Its fixed projection weights are already
useful; the uncalibrated affine decision boundary wastes much of the available
code capacity. The result does **not** license changing `nlb_paper_tied_v1`:
the calibrated encoder is a new project adaptation, must have a new artifact
family and provenance, and must be evaluated in the C++ exact-rerank path.

The next implementation PR therefore starts with
`nlb_median_threshold_v1`, preserving the trained matrix and tied-decoder
weights but materializing an explicit verified encoder-bias file from the
document-only calibration split. Decoder-only quality is reported separately:
the existing decoder was trained for zero-threshold codes and must not be
assumed valid after threshold calibration.

### Persisted `nlb_median_threshold_v1` result

The diagnostic was then promoted to a distinct, self-contained artifact and
evaluated through the C++ qrels evaluator. The calibrator verifies the frozen
source NLB artifact and its weight digests, uses only the `23,801` stable
document-only training IDs, asserts that they do not overlap the held-out
evaluation-document IDs, and stores a per-bit `float32_le` bias equal to the
negative projection median. Its artifact records the source-artifact hash,
calibration policy, canonical calibration-ID-list hash, source manifest hashes,
and the pinned Python `3.12.13` / NumPy `2.5.1` environment inherited from the
NLB trainer lock.

The resulting 128-bit artifact SHA-256 is
`567bf125764d20adde5ef58155c8cfa4d9a8d83ece7c61b4feada1da4a211992`.
Evaluation still uses the original held-out 22,607 RU documents, 1,252 queries,
and 13,100 qrels; no query or qrel affects the calibration.

| Candidates | Coverage | Lift vs random | Exact-rerank nDCG@10 | Retention of full E5 |
| ---: | ---: | ---: | ---: | ---: |
| 128 | 0.7580 | 133.8x | 0.7465 | 93.14% |
| 512 | 0.8886 | 39.23x | 0.7826 | 97.65% |
| 2,048 | 0.9671 | 10.68x | 0.7977 | 99.53% |

At 512 candidates, calibration raises coverage from `0.6632` to `0.8886` and
reranked nDCG@10 from `0.6894` to `0.7826`, while the full E5 oracle is
`0.80145`. Document-code entropy rises from `69.90 / 128` to `127.59 / 128`;
there are no constant held-out bits, code uniqueness remains `99.991%`, the
sampled bit-correlation participation ratio is `88.0`, and E5-cosine versus
negative-Hamming Pearson correlation is `0.410`. This confirms that threshold
placement, rather than an absence of useful NLB projection geometry, was the
dominant problem in the zero-threshold 128-bit artifact.

The tied decoder improves but remains unsuitable for compact-only retrieval:
decoder-only nDCG@10 is `0.1728`, far below the exact-rerank path. Thus this
result validates the calibrated code only as a candidate-generation layer with
retained float vectors. The next comparison must keep the median calibration
separate from both an altered training objective and retrieval-aware fine-
tuning: first run the same artifact family at 64/256/512 bits and compare it
fairly with the standard binary baselines on this exact held-out fixture.

### Median-calibrated NLB bit grid

The same calibrator was applied independently to the frozen 64-, 256-, and
512-bit `nlb_paper_tied_v1` artifacts. Every artifact uses the same 23,801
document-only stable training IDs and the same held-out 22,607-document RU
evaluation root. The reported metrics are from the C++ exact-rerank evaluator;
the random candidate expectations are still `0.00566`, `0.02265`, and
`0.09059` for 128, 512, and 2,048 candidates.

| Bits | Candidates | Coverage | Lift vs random | Exact-rerank nDCG@10 | Retention of full E5 |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 64 | 128 | 0.5014 | 88.56x | 0.6084 | 75.91% |
| 64 | 512 | 0.6894 | 30.44x | 0.7141 | 89.10% |
| 64 | 2,048 | 0.8689 | 9.59x | 0.7737 | 96.53% |
| 128 | 128 | 0.7580 | 133.80x | 0.7465 | 93.14% |
| 128 | 512 | 0.8886 | 39.23x | 0.7826 | 97.65% |
| 128 | 2,048 | 0.9671 | 10.68x | 0.7977 | 99.53% |
| 256 | 128 | 0.9356 | 165.25x | 0.7981 | 99.58% |
| 256 | 512 | 0.9827 | 43.39x | 0.8012 | 99.97% |
| 256 | 2,048 | 0.9973 | 11.01x | 0.8015 | 100.00% |
| 512 | 128 | 0.9852 | 174.01x | 0.8017 | 100.03% |
| 512 | 512 | 0.9982 | 44.07x | 0.8014 | 99.99% |
| 512 | 2,048 | 0.9998 | 11.04x | 0.8015 | 100.00% |

This removes the earlier ambiguity about whether the 128-bit result was an
isolated threshold effect. Calibration produces near-maximal marginal entropy
at every width: `63.81 / 64`, `127.59 / 128`, `255.05 / 256`, and
`509.87 / 512`. The cosine-to-negative-Hamming correlation rises from `0.316`
at 64 bits to `0.538` at 256 and `0.669` at 512; the corresponding correlation
participation ratios are `52.6`, `135.1`, and `174.3`.

The practical frontier on this one RU fixture is therefore sharper than the
zero-threshold result suggested: 128 bits is a strong reduction with a small
but measurable quality gap, 256 bits already reaches 99.58% full-E5 nDCG@10
at 128 candidates, and 512 bits reaches the full-E5 result within measurement
noise at the same budget. This is evidence for a calibrated NLB candidate
filter, not an argument to discard float vectors or to call the result
multilingual.

The previous PCA/ITQ table remains an intentionally **unfair preliminary
control** because those encoders used their 2,048-vector training cap while
NLB used 23,801 document vectors. The next comparison must first materialize a
canonical deterministic 2,048-document training-ID subset. NLB, PCA+sign,
ITQ, random hyperplanes, and optional pair-difference projection will all use
that exact ID list, while the held-out documents, queries, and qrels remain
unchanged. The reports must include the subset ID hash and selected count; no
winner claim is allowed until this common-budget control exists.

### Common-budget 128-bit learning curve

The common-budget control was expanded into nested canonical document-only
training subsets of 512, 2,048, 8,192, and 23,801 IDs. Each list is a prefix
of one stable SHA-256 ID ordering, so a larger point adds rows without changing
the smaller point. PCA+sign, ITQ, and NLB receive exactly the same train IDs at
each size. NLB uses a separate fixed 1,199-document validation list for
checkpoint selection; its median threshold is calculated from its train list
only. This is therefore a production-like train-budget comparison, not a
strict total-accessible-data budget comparison.

All rows use the same held-out RU corpus, queries, qrels, 128-bit signatures,
and 512 binary candidates followed by exact float reranking.

| Train IDs | PCA coverage | PCA nDCG@10 | ITQ coverage | ITQ nDCG@10 | Median NLB coverage | Median NLB nDCG@10 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 512 | 0.9204 | 0.7926 | 0.9293 | 0.7930 | 0.8883 | 0.7834 |
| 2,048 | 0.9317 | 0.7954 | 0.9350 | 0.7925 | 0.8893 | 0.7855 |
| 8,192 | 0.9332 | 0.7934 | 0.9387 | 0.7929 | 0.8886 | 0.7836 |
| 23,801 | 0.9350 | 0.7972 | 0.9365 | 0.7895 | 0.8891 | 0.7833 |

Under this fixed-epoch, single-seed run and the then-current normalized
regularizer, increasing the document-only training subset from 512 to 23,801
did not materially improve NLB-128: its coverage remained around `0.889` and
nDCG@10 around `0.783--0.786`. PCA+sign is stronger at every measured budget
and reaches `0.7972` at 23,801 documents. This is not evidence that additional
data cannot help after changing the objective, training duration, or
regularizer normalization. ITQ is statistically too close to PCA at the small
budgets to claim a distinct win, and is lower at the largest point in this
single-seed run.

This is not a failure of calibrated NLB as a candidate filter: it retains about
97.7--98.0% of full-E5 nDCG@10 at 512 candidates. It is evidence that the
remaining gap is not primarily median estimation or raw train-row count. The
next training changes should target code correlation and local E5 geometry:
learnable median-initialized bias, decorrelation, and document-only neighbour
or margin distillation. Before declaring a small PCA/ITQ difference, run at
least two additional seeds and query-bootstrap confidence intervals. A strict
total-data-budget 512 control remains a separate future ablation.

### Evaluation-integrity follow-up

Before re-running any Hamming candidate figures, the evaluator was changed to
order equal scores by `document_id` ascending rather than materialization row
position. A permutation regression test now proves that tied Hamming candidates
produce the same coverage and reranked recall regardless of input row order.

Each report now records the artifact family and bit count; train, validation,
and threshold-calibration ID hashes when they exist; held-out document-ID,
query-ID, and qrels hashes; the language IDs; the evaluation protocol; and the
tie-break policy. The NLB pilot gate must receive a separate expected-identity
manifest and rejects every mismatch instead of merely requiring two reports to
agree with one another. Existing numeric tables predate this ranking-integrity
change and are historical observations only; the common RU Hamming baselines,
quantile sweep, and Hamming-versus-asymmetric comparison must be rerun before
they are used for a new selection decision.

The current evaluator contract also pins an evaluator ID/version, a scalar
ranking-similarity backend, and a SHA-256 manifest of the project-owned C++ source
units that load the artifact, produce binary codes, rank candidates, and
aggregate qrels metrics. CMake regenerates that manifest when one of those
units changes. The standard baseline evaluator uses the same scalar reference
backend and streams per-query qrels metrics too, so its future comparison
reports do not retain corpus-sized rankings for every query.

Evaluation report schema v2 additionally requires an `evaluator_build_environment`
object. `configured_environment_sha256` covers the published compiler identity,
C++ language mode/extensions, generator, platform, pointer width, and base
C++ flags fingerprint; `build_configuration` and
`active_configuration_flags_sha256` identify the Debug/Release-like variant
actually compiled. The report exposes both flag-family hashes, so a differing
environment fingerprint can be diagnosed without relying on an absolute
compiler installation path. It is provenance, not a promise that two
independently built binaries are bit-identical. The NLB quality gate validates
this object's shape but deliberately does not require it to match the expected
identity. It does require every report in one gate decision to carry the same
build environment, so its 512- and 2,048-candidate evidence cannot be silently
combined across toolchains or build configurations.

`tools/agent-memory-bench/nlb-pilot-expected-identity.example.json` documents
the required manifest shape. A concrete manifest belongs beside the curated
result table for its particular run; the placeholder file is deliberately not
a runnable gate input.

### Reproduced RU median-NLB gate after evaluator-integrity fixes

The 128-bit median NLB control was retrained from the same RU document-only
materialization after stable train/validation ID hashes became mandatory
artifact provenance. The artifact was then median-calibrated from that exact
train split. The expected-identity manifest bound the artifact, materialization,
prepared study, train, validation, calibration, held-out document/query/qrels,
language (`ru`), protocol, oracle depth, scoring rule, and tie-break policy.

| Candidates | exact top-10 candidate coverage | exact-rerank nDCG@10 | Full-E5 nDCG@10 | nDCG retention | cosine to -Hamming correlation |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 512 | 0.8886 | 0.7826 | 0.8015 | 0.9765 | 0.4100 |
| 2,048 | 0.9670 | 0.7977 | 0.8015 | 0.9953 | 0.4100 |

The new gate passed every predeclared condition: coverage at 512/2,048,
rerank retention, correlation, zero constant document bits, and 0.999912
unique document-code fraction. The repeated quality values are close to the
historical rows, which is reassuring but not a license to reuse those rows for
selection: this is the first version bound to the complete identity contract
and ID-independent Hamming ties.

During this rerun the qrels evaluator exposed a separate memory defect: it
retained three full-corpus `RetrievalRun` objects for every query. On this
fixture that grew to roughly 7.5 GB before completion. The evaluator now
computes metrics one query at a time and releases each full ranking
immediately, preserving unbounded MRR while keeping the evaluation memory
bounded. The observed wall time of this comprehensive correctness evaluator
(about 86--101 seconds per candidate budget on the local machine) includes the
full cosine oracle, decoder ranking, and all-pair diagnostics; it is not a
candidate-search latency measurement.

### Post-hoc NLB quantile-threshold sweep

The median result raises a narrower question: whether the document median is
also the best fixed threshold, or merely a useful way to restore bit balance.
Without changing the frozen `nlb_paper_tied_v1` matrix, a calibrated artifact
was materialized for each per-bit document-only projection quantile. Non-median
policies are persisted as the separate `nlb_quantile_threshold_v1` family and
record both the selected quantile and its calibration-ID provenance. The
existing `nlb_median_threshold_v1` identity remains reserved for exactly
`q = 0.5`.

All seven artifacts use the same 23,801 training documents, the same held-out
22,607-document RU corpus, 1,252 queries, 13,100 qrels, 128-bit codes, and
512 binary candidates followed by exact float reranking. No query, qrel, or
held-out document contributes to threshold construction. As a compatibility
check, the new calibrator's `q = 0.5` encoder-bias file has the same SHA-256 as
the earlier median artifact:
`73282662bb8e4267e6ce35ab9f5374df8e8f9dc60815583fdfbf72e89ac700b0`.

| Train quantile | Coverage@512 | Exact-rerank nDCG@10 | Held-out entropy / 128 | Cosine vs. -Hamming correlation |
| ---: | ---: | ---: | ---: | ---: |
| 0.25 | 0.7837 | 0.7468 | 102.74 | 0.3559 |
| 0.35 | 0.8533 | 0.7755 | 118.66 | 0.3888 |
| 0.45 | 0.8839 | 0.7824 | 126.50 | 0.4062 |
| 0.50 (median) | 0.8886 | 0.7826 | 127.60 | 0.4100 |
| 0.55 | 0.8899 | 0.7854 | 126.84 | 0.4123 |
| 0.65 | 0.8612 | 0.7747 | 119.54 | 0.4045 |
| 0.75 | 0.7931 | 0.7555 | 103.94 | 0.3899 |

The broad conclusion is robust: moving the threshold far from the document
median damages code capacity and retrieval locality even though code uniqueness
stays above 99.99%. The narrow `0.45--0.55` region is comparatively stable.
The single best observed row is `q = 0.55`, but this is a descriptive
held-out-qrels observation, not a selection rule or a production default.
Choosing a non-median fixed threshold from these scores would overfit this one
RU evaluation fixture. Median remains the canonical no-query calibration; a
separate query-aware evaluation must decide whether continuous query evidence
can improve candidate selection without changing the document artifact.

This sweep changes neither the NLB objective nor decoder training. It rules out
the hypothesis that a large asymmetric threshold alone resolves the remaining
128-bit gap to PCA+sign, while leaving a small near-median operating region for
future document-only calibration studies. The next experiment therefore keeps
the document codes and artifacts fixed and evaluates asymmetric continuous-query
scoring as a distinct candidate-search policy.

### Asymmetric continuous-query scoring

The next post-hoc control keeps the canonical 128-bit median-calibrated NLB
artifact and every document code fixed. Hamming assigns every disagreeing bit
the same cost. The asymmetric alternative retains a document's packed bits but
uses the query's continuous pre-threshold affine projections:

```text
score(q, d) = sum_j sign(bit_j(d)) * (W_j * transform(q) + bias_j)
```

where a set document bit has sign `+1` and a cleared bit has sign `-1`. Thus a
document agreement on a high-confidence query projection counts more than an
agreement close to the threshold. This is a query-time scoring change only:
the query encoder, document artifact, calibration split, held-out RU corpus,
and exact float reranker are otherwise identical. No query, qrel, or relevance
loss is used to train or tune the artifact.

| Candidates | Hamming coverage | Asymmetric coverage | Hamming nDCG@10 | Asymmetric nDCG@10 |
| ---: | ---: | ---: | ---: | ---: |
| 128 | 0.7580 | 0.8873 | 0.7465 | 0.7916 |
| 512 | 0.8886 | 0.9596 | 0.7826 | 0.7986 |
| 2,048 | 0.9671 | 0.9924 | 0.7977 | 0.8011 |

At 512 candidates, continuous query evidence recovers an additional 7.10
percentage points of the original E5 top-10 and raises exact-rerank nDCG@10 to
99.65% of the full-E5 `0.80145` score. At 128 candidates it delivers a much
larger candidate-stage gain (12.93 points), showing that a binary document code
does retain more information than symmetric Hamming ranking can exploit.

This is not yet a production search result. The implementation is an in-memory
full scan that accumulates one float contribution per document bit, whereas the
Hamming path uses packed XOR plus popcount. It is therefore a quality upper
bound for a future cascade, not evidence that affine scoring should replace a
band/MDBX Hamming prefilter. A plausible serving design is: band or Hamming
coarse shortlist, asymmetric affine scoring over that bounded shortlist, then
exact float reranking. The next index experiment must measure that cascade's
quality/latency frontier. Before then, robustness work should compare seeds and
query-bootstrap intervals and test whether query-logit scaling changes this
result without qrels-based tuning.

### Retrieval-distilled NLB ablation on the current RU-25k split

This completed document-only ablation establishes the first
`nlb_retrieval_distilled_v1` artifacts. It asks whether a median-calibrated NLB
code improves when its geometry is fine-tuned without queries or qrels. All
variants use the same current MIRACL Russian E5 materialization: 23,801
document-only training vectors and 1,199 validation vectors (seed 42), followed
by evaluation on a disjoint qrels-closed corpus of 22,607 documents, 1,252
queries, and 13,100 qrels. Training uses 128 bits, ten epochs, batch size 256,
learning rate `1e-5`, a soft-to-hard `tanh` schedule from 1 to 8, and 18 CPU
threads. The source `nlb_median_threshold_v1` artifact SHA-256 is
`3e8c5194c74164163a7ea2dfd47d24af3b174ccd4abcd9ba91594fe94fe9516d`.

The artifact contract records source artifact and trainer hashes, requirements
lock, initialization, split hashes, objective, loss weights, schedule, code
health, and `queries_or_qrels_used: false`. Its loader rejects a retrieval
artifact unless these fields are structurally valid and its initialization
explicitly identifies the median artifact family.

| Variant | Initialization | Decorrelation / distillation | Artifact SHA-256 | Dense coverage@512 | Reranked nDCG@10 | Cosine vs. -Hamming correlation | Entropy / 128 |
| --- | --- | ---: | --- | ---: | ---: | ---: | ---: |
| Median NLB baseline | median source artifact | 0 / 0 | `3e8c…9516d` | 0.8886 | 0.7826 | 0.4100 | 127.59 |
| Learned bias | median source artifact | 0 / 0 | `d8b71a63d3e14383493b3b4a12e14eb6d19e571c91863593da0e135ba78ec34c` | 0.8260 | 0.7670 | 0.3833 | 106.49 |
| Learned bias + decorrelation | median source artifact | 0.01 / 0 | `a55c707fdcf43dda29435d44ed665a7df1d70bfebc78c68f0af4be2027c207fc` | 0.8245 | 0.7669 | 0.3825 | 106.48 |
| Learned bias + document-only distillation | median source artifact | 0.01 / 0.10 | `f9ca935da2c6d9be85702c736e931b2815798982a0ccbf2b082f80c462a6e3bd` | 0.8409 | 0.7724 | 0.4220 | 106.80 |
| ITQ control | ITQ + median, effectively frozen | 0 / 0 | `b57f898e67ba252588c5c2eb878098c95c69db93bacda382d921db99c83fd19f` | 0.9367 | 0.7932 | — | — |
| ITQ + decorrelation + distillation | ITQ + median | 0.01 / 0.10 | `0917a1261db47ecec489a9ac9c83e990f865b4f9703442529e11e388d88c3a1b` | 0.9442 | 0.7952 | 0.6254 | 122.03 |

Full E5 nDCG@10 is `0.80145`. For the selected ITQ artifact, full-corpus
asymmetric continuous-query scoring at K=512 reaches candidate coverage
`0.98762` and reranked nDCG@10 `0.80043` (99.87% of full E5). Its decoder-only
ranking reaches `0.5313`, better than the earlier failed decoder but not a safe
replacement for retained float vectors. The same artifact has document-code
uniqueness `0.999956`, duplicate-signature rate `0.000044`, mean absolute bit
correlation `0.0520`, and participation ratio `82.58 / 128`.

The negative controls are useful: making the median bias learnable causes
severe imbalance (validation maximum bit occupancy about 0.98) without creating
strictly constant bits, and the tested decorrelation loss does not repair it.
Document-only distillation partially recovers locality but remains below the
frozen median baseline. ITQ initialization is the dominant change: the
near-frozen ITQ control is already 4.8 coverage points above median NLB, while
the complete ITQ fine-tune adds another 0.75 points. These are single-seed
directional findings, so they do not yet select a production training family.

Raw artifacts and evaluator reports remain uncommitted under
`tmp/nlb-retrieval-*`; they are regenerated by
`tools/agent-memory-bench/fine-tune-nlb-retrieval.py` and
`agent-memory-autoencoder-cascade-eval`. Next, repeat the selected ITQ points
over seeds with query-bootstrap confidence intervals, run a controlled ITQ-only
versus ITQ-plus-each-loss ablation, and test a separately predeclared
median-anchor/bias-regularization study. Query/qrels-aware training remains a
distinct later experiment and must not be combined with this no-leakage result.

### Superseded retrieval-distillation pilot protocol

The table above is retained as a historical record only and must not be used
for a training-family decision. A later audit found two protocol defects: it
selected checkpoints at a temperature that changed with epoch, and labelled an
effectively frozen ITQ point although it still executed optimizer steps. The
replacement protocol uses a fixed final-temperature validation objective and an
explicit zero-epoch, zero-step initialization-only control. It also defines
document-geometry distillation as cosine similarity between L2-normalized
soft codes, rather than a bit-count-scaled dot product. The same held-out RU
fixture and document-only split are retained; replacement results are recorded
in the follow-up section after rerunning every comparison under this contract.

### Corrected retrieval-distillation ablation

The replacement run keeps the same 23,801 document-only training vectors,
1,199 document-only validation vectors, 22,607 held-out evaluation documents,
1,252 held-out queries, and 13,100 qrels. It uses 128 bits, ten epochs where
training is enabled, batch size 256, and candidate limit 512. Checkpoint
selection evaluates every epoch at the fixed final soft-code temperature `8`.
The frozen ITQ control is an explicit initialization-only export: zero epochs
and zero optimizer steps. Document-only geometry distillation compares the
cosine matrix of L2-normalized clipped E5 vectors with the cosine matrix of
L2-normalized soft codes. No query text, query vector, or qrel is used for
training or checkpoint selection.

| Variant | Optimizer steps | Coverage@512 | Reranked nDCG@10 | cosine/-Hamming correlation | Total bit entropy |
| --- | ---: | ---: | ---: | ---: | ---: |
| Median NLB source baseline | 0 | 0.8886 | 0.7826 | 0.4100 | 127.59 |
| Median initialization + reconstruction | 930 | 0.8260 | 0.7670 | 0.3833 | 106.49 |
| ITQ + median, frozen initialization-only control | 0 | 0.9367 | 0.7932 | 0.5813 | 127.11 |
| ITQ + median + reconstruction | 930 | 0.9369 | 0.7916 | 0.5580 | 124.40 |
| ITQ + median + reconstruction + decorrelation | 930 | 0.9368 | 0.7917 | 0.5577 | 124.40 |
| ITQ + median + reconstruction + soft-code-cosine distillation | 930 | 0.7047 | 0.7024 | 0.5259 | 65.44 |
| ITQ + median + reconstruction + decorrelation + soft-code-cosine distillation | 930 | 0.7051 | 0.7028 | 0.5259 | 65.44 |

The corrected result preserves the principal finding but narrows its claim:
the gain comes from deterministic ITQ-plus-median initialization, not from the
tested fine-tuning losses. Reconstruction leaves candidate coverage effectively
unchanged and modestly lowers final nDCG. The tested decorrelation weight has
no material additional effect. Under these temperatures and weights, soft-code
cosine distillation collapses effective code information and is a clear
stop/diagnose result rather than a candidate for promotion. The next
document-only training experiment therefore anchors median-calibrated decision
boundaries explicitly and studies a weaker local-neighbour objective; it must
not silently reuse this failed distillation setting.

### Median-preserving ITQ fine-tuning

The preceding failure isolates the decision boundary as a likely failure mode:
training an affine bias directly can turn otherwise useful projections into an
imbalanced code. This follow-up keeps the ITQ initialization but excludes the
bias from the optimizer. At initialization and after each training epoch, every
bias is set to the negative median of that row's projections over the same
23,801 document-only training vectors. Thus every train bit has occupancy
within one document of 50%; held-out query/qrel data are still absent from
training and selection.

| Variant | Coverage@512 | Reranked nDCG@10 | cosine/-Hamming correlation | Total bit entropy |
| --- | ---: | ---: | ---: | ---: |
| ITQ + median frozen control | 0.9367 | 0.7932 | 0.5813 | 127.11 |
| Median-preserving ITQ + reconstruction | 0.9439 | 0.7945 | 0.5580 | 127.23 |
| Median-preserving ITQ + reconstruction + decorrelation | 0.9436 | 0.7946 | 0.5576 | 127.23 |
| Median-preserving ITQ + reconstruction + weak soft-code-cosine distillation (`0.005`) | 0.9408 | 0.7928 | 0.5412 | 127.27 |

This is a positive document-only result: median projection preserves code
capacity while optimization gains about `0.72` coverage points over the frozen
ITQ control. The small decorrelation and weak distillation variants do not
improve it materially. These are still one seed and one held-out RU fixture;
the next gate is a local-neighbour/margin objective and then a separately
declared qrels-aware experiment, not promotion of this artifact as a production
default.

### Document-only local-neighbour margin control

The full soft-code cosine distillation above was too global and unstable. This
control uses the same median-preserving ITQ setup, but within each document
batch takes the teacher's nearest non-self clipped-E5 neighbour as positive and
its rank-8 neighbour as negative. It minimizes a soft margin between their
normalized soft-code cosine scores. The teacher vectors are document vectors
only; no query or qrel enters this objective.

| Local-margin weight | Coverage@512 | Reranked nDCG@10 | cosine/-Hamming correlation | Total bit entropy |
| ---: | ---: | ---: | ---: | ---: |
| `0` (median-preserving reconstruction control) | 0.9439 | 0.7945 | 0.5580 | 127.23 |
| `0.01` | 0.9439 | 0.7947 | 0.5582 | 127.23 |
| `0.05` | 0.9442 | 0.7949 | 0.5595 | 127.23 |

The effect is small but directionally positive at `0.05`, without reducing bit
entropy. It is not yet a robust winner: one seed, one margin and one in-batch
neighbour definition cannot establish a general training recipe. The remaining
separate experiment may use labeled MIRACL query--document pairs and hard
negatives, with a disjoint train query/qrel split and untouched dev evaluation.

### Qrels-supervised RU train-to-dev pilot

**Status: superseded protocol run.** The measurements in this section are
retained for auditability only and must not be used to compare supervision with
PCA or ITQ initialization. The trainer replicated each positive once per hard
negative but normalized its decorrelation term by the unreplicated batch size,
amplifying that loss. Its artifact also described all planned negative
consumption instead of the selected checkpoint's consumption. The replacement
matrix uses unique positive rows for reconstruction and decorrelation, records
per-epoch and selected-checkpoint consumption separately, includes an explicit
zero-step control, and evaluates matched PCA-plus-median and ITQ-plus-median
initializations before interpreting supervised effects. The supervised ITQ
initializer is a fresh matched implementation; its SVD-based PCA basis is not
claimed to be bitwise identical to the historical document-only ITQ baseline.

This pilot is a separate artifact family, `nlb_qrels_supervised_v1`, rather
than a relabelled document-only artifact. Its goal is to test whether a soft
Hamming triplet objective can improve a median-calibrated binary code while
keeping MIRACL RU dev completely outside training, hard-negative mining,
checkpoint selection, and hyperparameter selection.

The authoritative source roots were
`tmp/miracl-ru-supervised-disjoint-prepared-v2-authoritative` and
`tmp/miracl-ru-supervised-disjoint-e5-authoritative`. The E5 root has manifest
SHA-256 `fb5af79a70a8f61e27c9615c178203599ed5dc10f287d0741d132d97f0218856`,
uses `intfloat/multilingual-e5-small` at revision
`614241f622f53c4eeff9890bdc4f31cfecc418b3`, and contains 25,000 label-free
calibration documents, 37,538 supervised documents, 4,326 retained official
MIRACL train queries, and 30,004 qrels. The held-out root is the existing
22,607-document / 1,252-query RU dev materialization with manifest SHA-256
`cd1987fdef63f5f6b4fd595d312648ea58f85aa502ed982958ebf02e99290e86`.

The deterministic pre-mining query split was 3,477 train and 849 validation
queries. For each partition separately, 64 frozen E5 top-ranked non-positive
documents were mined after excluding every `grade > 0` qrel. The trainer uses
one shared affine projection for queries and documents and optimizes
`softplus(margin - mean(soft(q)*soft(d+)) + mean(soft(q)*soft(d-)))`. It
recalibrates every threshold to the label-free 25k document median after every
epoch. Checkpoint selection was fixed in advance: hard-code health, then
positive-qrel candidate coverage at K=512, reranked nDCG@10, lower occupancy
deviation, and earlier epoch. The artifact retains hashes of both query splits,
the qrels, hard-negative payload, E5 model revision, materialization outputs,
and trainer sources.

All eight checkpoints passed the health gate: zero constant bits, 25,000 unique
codes over the 25k calibration documents, and occupancy deviation zero at the
stored precision. Selection nevertheless chose the first post-update epoch;
later epochs did not improve the fixed validation objective.

| Checkpoint | Validation positive-qrel coverage@512 | Validation reranked nDCG@10 |
| ---: | ---: | ---: |
| epoch 0 (selected) | 0.98115 | 0.82415 |
| epoch 1 | 0.97762 | 0.82211 |
| epoch 4 | 0.97291 | 0.81580 |
| epoch 7 | 0.98115 | 0.82184 |

The selected artifact (`37b36cdb2f5730db51cb09af78adbea22042567a4b4294b2aed4b7722baf8d58`)
was evaluated once on held-out RU dev using scalar reference ranking and 512
Hamming candidates followed by exact E5 rerank. Training and evaluation roots
are explicitly distinct in the report; requiring their hashes to match would
invalidate a held-out train-to-dev experiment.

| Artifact / mode | exact-top-10 coverage@512 | reranked nDCG@10 | nDCG retention vs. full E5 | cosine vs. -Hamming | decoder nDCG@10 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Full E5 oracle | 1.00000 | 0.80145 | 1.0000 | — | — |
| Qrels-supervised NLB | 0.93482 | 0.79284 | 0.9893 | 0.4044 | 0.61962 |
| Historical frozen ITQ document-only control | 0.93666 | 0.79316 | 0.9897 | 0.5813 | 0.56818 |

The learned code is decisively non-random: the 512-document random expectation
is 0.02265 top-10 coverage, whereas the supervised artifact has 0.93482
(41.28x lift). Exact rerank retains 98.93% of the full-E5 nDCG@10 and decoder
ranking is still too weak to replace retained float vectors. However, this is
not evidence that the first supervised objective improves over the strongest
document-only recipe: its selected result is slightly below the historical
frozen ITQ control, and this pilot uses a separately declared PCA-plus-median
initialization on the authoritative disjoint root. The appropriate next control
is a fresh label-free PCA-plus-median export on exactly this root, followed by
a matched frozen-ITQ initialization when the same initialization can be made
without reusing held-out dev data. Only then should shuffled-qrel and random-
negative controls be interpreted as causal tests of supervision.

### Corrected matched qrels-supervised matrix

This replacement matrix was run after the unique-positive loss correction and
the fail-closed negative-pool/provenance contracts. It uses the same
authoritative RU train root (25,000 label-free calibration documents, 37,538
supervised documents, 4,326 retained train queries, and 30,004 qrels) and the
unchanged 22,607-document / 1,252-query RU dev root for its one final held-out
evaluation. E5 remains `intfloat/multilingual-e5-small` at revision
`614241f622f53c4eeff9890bdc4f31cfecc418b3`.

Every row uses 128 bits, seed 42, 64 frozen E5 non-positive documents per
train query, eight negative positions per query per epoch, candidate budget
512, and scalar reference exact reranking. The supervised rows use eight
epochs, batch size 128, AdamW learning rate `1e-4`, triplet margin `0.1`, and
reconstruction/decorrelation/row-orthogonality weights
`0.01 / 0.01 / 0.001`. The zero-step controls use exactly the corresponding
fresh initialization and select `epoch = -1`, with zero optimizer steps and
zero consumed negatives. The ITQ rows use the trainer's fresh deterministic
SVD-plus-50-rotation initialization; they are matched to each other but are
not claimed to be bitwise equivalent to the earlier covariance-based
document-only ITQ implementation.

| Initialization / regime | Selected epoch / steps | Held-out exact-top-10 coverage@512 | Held-out reranked nDCG@10 | Retention vs. full E5 | Cosine / -Hamming correlation |
| --- | ---: | ---: | ---: | ---: | ---: |
| PCA + median, zero-step | `-1 / 0` | 0.93347 | 0.79295 | 0.98939 | 0.4039 |
| PCA + median, qrels-supervised | `6 / 196` | 0.93435 | 0.79300 | 0.98946 | 0.3955 |
| ITQ + median, zero-step | `-1 / 0` | 0.93746 | 0.79005 | 0.98577 | 0.5823 |
| ITQ + median, qrels-supervised | `5 / 168` | 0.92548 | 0.79135 | 0.98740 | 0.5615 |

Full E5 nDCG@10 is `0.80145`. All four artifacts have zero constant document
bits; total document entropy ranges from `127.105` to `127.732` out of 128.
The PCA supervised row changes coverage by only `+0.00088` and reranked nDCG
by `+0.00006` relative to its exact zero-step control. The ITQ supervised row
changes coverage by `-0.01198` while changing reranked nDCG by `+0.00130`.
Those deltas are not evidence of a reliable supervised gain: this is one seed,
the validation split participates in checkpoint selection, and no paired query
bootstrap confidence interval has yet been calculated.

The raw artifacts and reports remain uncommitted under
`tmp/nlb-qrels-*-corrected-*`. The next causal controls are shuffled positive
qrels and random negatives under the same PCA and ITQ schedule, followed by
paired query bootstrap confidence intervals for any retained candidate. Only
then is it meaningful to tune the supervised loss or promote it over the
document-only family. A compact committed provenance record links every table
row to its artifact, training-history, and held-out report digest in
[`2026-08-05-qrels-supervised-matrix-manifest.json`](2026-08-05-qrels-supervised-matrix-manifest.json).

### Auxiliary-only attribution and ternary projection reference

The corrected binary matrix was re-evaluated query-by-query with the stable-ID
reference contract, then tested with a paired 10,000-replicate query bootstrap
(seed `20260806`). PCA supervised minus zero-step has coverage 95% CI
`[-0.00288, 0.00471]` and nDCG@10 CI `[-0.00270, 0.00305]`; neither effect is
resolved. ITQ supervised minus zero-step has coverage CI
`[-0.01637, -0.00767]`, while its nDCG@10 CI `[-0.00171, 0.00439]` crosses
zero. Thus the ITQ locality loss is robust to dev-query resampling, while the
claimed relevance gain is not.

The no-triplet auxiliary control used the same initialization, epochs, batch
order, reconstruction/decorrelation/orthogonality weights, and median
recalibration as qrels supervision, but set the triplet weight to zero. Against
that control, the triplet changes PCA coverage by `+0.00016` (95% CI
`[-0.00343, 0.00376]`) and nDCG@10 by `+0.00064` (CI
`[-0.00138, 0.00276]`). For ITQ it changes coverage by `-0.00966` (CI
`[-0.01366, -0.00567]`) and nDCG@10 by `+0.00098` (CI
`[-0.00190, 0.00398]`). The current triplet is therefore not a supported
quality improvement and is the direct cause of most remaining ITQ locality
loss, rather than the auxiliary optimizer updates alone.

The same label-free 25k calibration root was then used for ternary projection
codes. Documents use base-3 packing (five trits per byte); the k-means rows use
three per-coordinate Lloyd--Max centroids and an unquantized query with a
query-specific packed ADC LUT. The timing values below cover query preparation,
candidate scoring, and a stable full ordering in the NumPy reference harness
over all 1,252 queries; they are not production C++ latency measurements.

| Code / candidate scoring | Packed bytes | Coverage@512 | Reranked nDCG@10 | Reference candidate scoring + full sort seconds |
| --- | ---: | ---: | ---: | ---: |
| PCA binary, 128 bits, Hamming | 16 | 0.93347 | 0.79295 | 8.05 |
| ITQ binary, 128 bits, Hamming | 16 | 0.93746 | 0.79005 | 8.05 |
| PCA 80 trits, symmetric tertile distance | 16 | 0.94113 | 0.79312 | 12.87 |
| PCA 80 trits, 3-centroid packed ADC | 16 | 0.95871 | 0.79502 | 28.88 |
| ITQ 80 trits, 3-centroid packed ADC | 16 | 0.97061 | 0.79856 | 29.46 |
| PCA binary, 208 bits, Hamming | 26 | 0.96534 | 0.79994 | 11.73 |
| ITQ binary, 208 bits, Hamming | 26 | 0.98227 | 0.80072 | 11.54 |
| PCA 128 trits, 3-centroid packed ADC | 26 | 0.99201 | 0.80085 | 42.65 |
| ITQ 128 trits, 3-centroid packed ADC | 26 | 0.99577 | 0.80150 | 43.28 |

Full E5 nDCG@10 is `0.80145`. This is a strong label-free quality signal: the
equal-16-byte ITQ ternary ADC improves coverage by `+0.03315` and nDCG@10 by
`+0.00851` over matched binary-128, while the 26-byte ITQ ternary ADC reaches
full-E5 nDCG within this reference evaluation. Packed ADC LUT and direct scalar
ADC are covered by a deterministic parity self-test. These findings do not yet
establish a production speed win: an independently benchmarked C++ packed-LUT
implementation and multiple initialization seeds remain required.

### Initial scalar-quantization attribution and rate--distortion grid

The preceding ternary table is retained as a historical directional run. It did
not yet contain the matched two-centroid float-query ADC baseline, did not bind
bootstrap contributions to ordered query IDs, and did not retain a compact
source/provenance manifest. It must therefore not be interpreted as evidence
that a third document symbol alone beat binary coding.

The corrected reference harness is fail-closed for materialization output
hashes, embedding identity, artifact payload hashes, calibration/evaluation
disjointness, ordered query IDs, qrels identity, `K=512`, and `oracle_k=10`.
Every paired-bootstrap input stores the ordered query IDs themselves plus its
identity metadata. All final rows use the original label-free 25k calibration
and held-out RU evaluation fixture, manifest SHA-256
`cd1987fdef63f5f6b4fd595d312648ea58f85aa502ed982958ebf02e99290e86`:
22,607 evaluation documents, 1,252 queries, and the frozen E5 qrels. The
reference evaluator source SHA-256 is
`607467573725424da6bff49c917c684876b23863497616c67108db9f3a02fb91`.

Document codes use median-threshold binary codes (eight coordinates per byte),
tertile ternary codes (five coordinates per byte), or quartile quaternary codes
(four coordinates per byte). ADC leaves the query projection as float and
scores document symbols through per-byte LUTs of squared distances to their
conditional scalar centroids. Hamming and ternary symmetric rows quantize the
query as well. Ranking has stable-ID tie-breaking before the candidate cutoff
and after exact float reranking. The reported reference time includes query
projection/LUT construction, complete candidate scoring, and stable full
ordering; it is explicitly not a production scan/selection benchmark.

The seed-42 attribution matrix separates symmetric scoring, asymmetric scoring,
symbol count, coordinate count, and payload size. Full E5 nDCG@10 is
`0.801451`; a filtered result slightly above or below it is not a better or
worse embedding model, only the qrels result after removing some exact-E5
candidates.

| Projection / document code / scoring | Payload | Coverage@512 | Reranked nDCG@10 |
| --- | ---: | ---: | ---: |
| ITQ binary 128, symmetric Hamming | 16 B | 0.935383 | 0.792662 |
| ITQ binary 128, 2-centroid ADC | 16 B | 0.983307 | 0.800936 |
| ITQ ternary 80, symmetric | 16 B | 0.929073 | 0.791561 |
| ITQ ternary 80, 3-centroid ADC | 16 B | 0.965974 | 0.797702 |
| ITQ quaternary 64, 4-centroid ADC | 16 B | 0.952316 | 0.794559 |
| ITQ binary 208, symmetric Hamming | 26 B | 0.979553 | 0.799024 |
| ITQ binary 208, 2-centroid ADC | 26 B | 0.998243 | 0.801545 |
| ITQ ternary 128, symmetric | 26 B | 0.982109 | 0.800315 |
| ITQ ternary 128, 3-centroid ADC | 26 B | 0.995767 | 0.801425 |
| ITQ quaternary 104, 4-centroid ADC | 26 B | 0.993450 | 0.801484 |

For the causal ITQ comparisons, paired 10,000-replicate query bootstrap (seed
`20260806`) gives the following deltas. Each interval is a percentile 95% CI.

| Transition | Coverage delta | nDCG@10 delta |
| --- | ---: | ---: |
| 128-bit Hamming -> 128-bit binary ADC | +0.047923 [0.043051, 0.052955] | +0.008274 [0.005154, 0.011646] |
| 208-bit Hamming -> 208-bit binary ADC | +0.018690 [0.015735, 0.021725] | +0.002522 [0.001222, 0.003945] |
| 128-bit binary ADC -> 80-trit ADC, equal 16 B | -0.017332 [-0.021246, -0.013498] | -0.003234 [-0.005622, -0.000984] |
| 208-bit binary ADC -> 128-trit ADC, equal 26 B | -0.002476 [-0.003914, -0.001118] | -0.000120 [-0.000893, 0.000627] |
| 80-trit ADC -> 64-quaternary ADC, equal 16 B | -0.013658 [-0.017492, -0.009984] | -0.003143 [-0.005969, -0.000478] |
| 128-trit ADC -> 104-quaternary ADC, equal 26 B | -0.002316 [-0.003914, -0.000799] | +0.000059 [-0.000521, 0.000809] |

The same-coordinate controls distinguish symbol capacity from coordinate count:
at 80 coordinates ITQ binary ADC uses 10 B and reaches coverage `0.934425`,
whereas 80-trit ADC uses 16 B and reaches `0.965974` (delta `+0.031550`, CI
`[0.027476, 0.035783]`). At 128 coordinates, binary ADC uses 16 B and reaches
`0.983307`, while ternary ADC uses 26 B and reaches `0.995767` (delta
`+0.012460`, CI `[0.010064, 0.015016]`); its nDCG interval crosses zero. Thus
more scalar levels help when the coordinate count is held fixed, but their extra
payload does not beat binary ADC at either equal 16 B or equal 26 B in this
fixture. The equal-payload winner is therefore the asymmetric float-query ADC
scorer applied to a binary code, not ternary or quaternary coding itself.

The rate--distortion comparison generalizes directionally to PCA: at 16 B,
PCA binary/ternary/quaternary ADC gives coverage `0.969089 / 0.957428 /
0.944010`; at 26 B it gives `0.992572 / 0.992252 / 0.989617`. ITQ is stronger
than PCA at the ternary points, but neither projection makes four levels the
best fixed-payload choice in this grid.

Five independent ITQ rotation seeds (`42`--`46`) establish that the central
conclusion is not a single-rotation artifact:

| Code | Mean coverage@512 +/- population SD | Mean reranked nDCG@10 +/- population SD |
| --- | ---: | ---: |
| Binary Hamming 128 | 0.938626 +/- 0.001740 | 0.792771 +/- 0.002118 |
| Ternary ADC 80 | 0.967955 +/- 0.001426 | 0.798057 +/- 0.000651 |
| Binary Hamming 208 | 0.980942 +/- 0.000855 | 0.800185 +/- 0.000681 |
| Ternary ADC 128 | 0.995543 +/- 0.000301 | 0.801308 +/- 0.000205 |

This study validates scalar quantization only as candidate generation followed
by retained-float exact rerank. It neither claims a production latency win nor
selects a persistence/index layout. The next implementation-oriented stage is
an independently benchmarked C++ packed-LUT scorer and top-K selection; banded
MDBX access remains a later decision. The compact provenance record for every
reported scalar row is
[`2026-08-06-scalar-projection-rate-distortion-manifest.json`](2026-08-06-scalar-projection-rate-distortion-manifest.json).

### 2026-08-06 reproducibility correction: complete five-seed scalar grid

The preceding scalar section is retained as historical evidence, but its
multi-seed table was incomplete: seeds `43`--`46` covered Hamming and ternary
ADC rows only. It therefore could not support a five-seed ordering of binary,
ternary, and quaternary ADC. The evaluator and all scalar rows were rerun
after the methodology fixes. The final compact manifest is schema v2 and
contains 50 rows plus 32 linked paired-bootstrap reports. It recomputes every
reported aggregate from the referenced per-query NPZ contributions, validates
their schemas and ranges, and rejects a mixed source/runtime/fixture set.

The final evaluator source SHA-256 is
`5a3a408724ec5d390b29979d7038b8efee5a6d26ef939551539bdba998945e77`.
All rows use CPython `3.12.13`, NumPy `2.4.6`, the same 25k calibration and
held-out RU fixture as above, 22,607 evaluation documents, 1,252 queries,
`K=512`, and oracle `K=10`. Per-row code-health provenance records symbol
frequencies and total *marginal* symbol entropy; this is not a claim about the
joint entropy of a packed code.

For the following table, each value is the mean across the five ITQ rotation
seeds `42`--`46`, followed by population SD and seed range. The five-seed
result now covers all three equal-payload ADC families.

| Document code / scoring | Payload | Coverage@512: mean +/- SD [min, max] | Reranked nDCG@10: mean +/- SD [min, max] |
| --- | ---: | ---: | ---: |
| Binary 128, Hamming | 16 B | 0.938626 +/- 0.001740 [0.935383, 0.940096] | 0.792771 +/- 0.002118 [0.790214, 0.796060] |
| Binary 128, 2-centroid ADC | 16 B | 0.984313 +/- 0.001190 [0.982748, 0.986022] | 0.800762 +/- 0.000347 [0.800336, 0.801214] |
| Ternary 80, 3-centroid ADC | 16 B | 0.967955 +/- 0.001426 [0.965974, 0.969728] | 0.798057 +/- 0.000651 [0.797545, 0.799339] |
| Quaternary 64, 4-centroid ADC | 16 B | 0.951741 +/- 0.000989 [0.950479, 0.953275] | 0.795541 +/- 0.001147 [0.794385, 0.797277] |
| Binary 208, Hamming | 26 B | 0.980942 +/- 0.000855 [0.979553, 0.981949] | 0.800185 +/- 0.000681 [0.799024, 0.801127] |
| Binary 208, 2-centroid ADC | 26 B | 0.997764 +/- 0.000375 [0.997284, 0.998243] | 0.801465 +/- 0.000114 [0.801255, 0.801584] |
| Ternary 128, 3-centroid ADC | 26 B | 0.995543 +/- 0.000301 [0.994968, 0.995767] | 0.801308 +/- 0.000205 [0.800943, 0.801521] |
| Quaternary 104, 4-centroid ADC | 26 B | 0.992859 +/- 0.000433 [0.992173, 0.993450] | 0.801251 +/- 0.000259 [0.800927, 0.801586] |

The paired seed differences make the attribution explicit. Each cell is the
mean difference of right minus left over the five rotations, followed by
population SD and seed range.

| Transition | Coverage delta: mean +/- SD [min, max] | nDCG@10 delta: mean +/- SD [min, max] |
| --- | ---: | ---: |
| Hamming 128 -> binary ADC 128 | +0.045687 +/- 0.001229 [+0.044409, +0.047923] | +0.007991 +/- 0.001792 [+0.005154, +0.010161] |
| Binary ADC 128 -> ternary ADC 80 | -0.016358 +/- 0.000964 [-0.017492, -0.014936] | -0.002705 +/- 0.000555 [-0.003404, -0.001874] |
| Ternary ADC 80 -> quaternary ADC 64 | -0.016214 +/- 0.001941 [-0.018770, -0.013658] | -0.002517 +/- 0.001574 [-0.004954, -0.000586] |
| Hamming 208 -> binary ADC 208 | +0.016821 +/- 0.001153 [+0.015415, +0.018690] | +0.001280 +/- 0.000700 [+0.000342, +0.002522] |
| Binary ADC 208 -> ternary ADC 128 | -0.002220 +/- 0.000478 [-0.002955, -0.001597] | -0.000156 +/- 0.000201 [-0.000529, +0.000052] |
| Ternary ADC 128 -> quaternary ADC 104 | -0.002684 +/- 0.000588 [-0.003514, -0.001837] | -0.000058 +/- 0.000313 [-0.000594, +0.000352] |

These seed-to-seed summaries are different from query-bootstrap uncertainty.
For every transition and seed, the manifest links the exact left/right NPZ
hashes to a 10,000-replicate paired bootstrap with seed `20260806`. For
example, seed 42 gives binary-128 ADC minus Hamming coverage `+0.047923`, 95%
CI `[+0.043051, +0.052955]`, and nDCG@10 `+0.008274`, CI
`[+0.005154, +0.011646]`. Its equal-16-B binary-128 minus ternary-80 delta is
coverage `+0.017332`, CI `[+0.013498, +0.021246]`, and nDCG@10 `+0.003234`,
CI `[+0.000984, +0.005622]`. The CI is conditional on the fixed rotation and
the sampled query population; it is not a confidence interval over rotations.

The corrected same-coordinate seed-42 controls are also retained: binary ADC
at 80 coordinates (10 B) reaches coverage `0.934425`, whereas ternary ADC at
80 coordinates (16 B) reaches `0.965974` (delta `+0.031550`, bootstrap CI
`[+0.027476, +0.035783]`). At 128 coordinates, binary ADC is 16 B and has
coverage `0.983307`; ternary ADC is 26 B and has `0.995767` (delta
`+0.012460`, CI `[+0.010064, +0.015016]`). Thus extra symbols help when they
also consume extra bytes, but they lose at the two equal-payload operating
points. PCA directional controls show the same ordering: binary/ternary/
quaternary coverage is `0.969089 / 0.957428 / 0.944010` at 16 B and
`0.992572 / 0.992252 / 0.989617` at 26 B.

The metric named
`reference_candidate_search_seconds_including_query_encoding_and_full_ordering`
is the total across 1,252 queries. It includes query projection, symmetric
query quantization where applicable, ADC LUT construction, candidate scoring,
and stable full ordering. It excludes full-E5 reference scoring, exact float
reranking, document-code materialization, process startup, and warm-up; it is
therefore not a production latency claim. This is a two-payload
rate--distortion *grid*, not a curve.

The corrected conclusion is limited but strong: on this held-out RU fixture,
asymmetric two-centroid binary ADC consistently uses fixed document bytes more
effectively than the matched ternary and quaternary scalar codes. All scalar
codes remain candidate generators only; exact float reranking still requires
retained float vectors. A C++ packed-LUT plus top-K benchmark and a larger
multi-payload grid remain separate future work.
