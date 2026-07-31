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
