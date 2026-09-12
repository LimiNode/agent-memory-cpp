# Semantic routing scale gate and verified R4 comparator

Date: 2026-09-12  
Branch: `research/semantic-routing-scale-r4-gate`  
Frozen fixture: `thq-full-scan-v2/manifest.json` (SHA-256
`f58e074e481dc910ca7b12b35bc27dc51097640704fb2b0c749018bcf2edd57b`)  
Queries: 152; documents: 1,000,000; teacher: frozen top-10 IDs (evaluation only)

## Question and protocol

The primary gate asks whether a generic full-dimensional semantic router can
reach approximately `.99` teacher recall by 50k candidates.  The pilot uses a
100k training sample, seed `20260912`, MiniBatchKMeans (`batch_size=4096`,
`max_iter=20`, `n_init=1`) and L2 arms at `K={1024,2048,4096}` with
replication `r={1,2,4}`.  Each query is traversed in stable centroid-score
order (cell ID is the tie-break).  `whole_posting` records the physical cost
of reading complete postings and may overshoot a candidate budget;
`hard_cap` is a mathematical exact-candidate prefix and is not a physical
posting-read implementation.

The R4 comparator replays three already materialized seeds
(`2026082701..2026082703`).  The 152×384 R4 query matrix is byte-identical to
the frozen query matrix (SHA-256 `abe14a8790bd488fc91b01b4b1d6ab664db1d2f2d69e67147ed8439f54c73191`).
Address postings are validated by manifest size/SHA, cover all one million
documents exactly once, and are traversed in the frozen model-ranked order
within the 1024-address shortlist.  The model order is reconstructed from the
materialized scalar features, representative maxima, and frozen scorer
parameters; the coarse shortlist remains a separate input set.  No payload
rerank, OS/MDBX page accounting, or production activation is claimed.

## Semantic pilot result

The best whole-posting row at 50k is `K=1024,r=4`: mean recall `.9230`, p05
`.70`, minimum `0.0`, with mean 55.7k unique candidates.  At 100k the same
configuration reaches mean `.9638`, p05 `.80`, minimum `.60`, with mean 105.0k
unique candidates.  Increasing K does not improve the frontier: the best
100k alternatives are below `.953` mean recall, and `K=4096,r=4` is `.9408`.

**Gate: NO-GO for this tested MiniBatchKMeans scale pilot.**  The result is
below `.97` at 100k with a non-trivial tail.  This is not a ceiling for every
possible K-means training regime: multiple seeds, larger samples, convergence
and boundary-selective replication were not tested here.  Nevertheless, a
blind generic-K-means convergence campaign is not justified as the next
product step.

## Verified R4 comparator

Aggregate over the three seeds (mean of per-seed query means):

| mode | requested budget | mean unique candidates | mean teacher recall | minimum recall across seed means |
| --- | ---: | ---: | ---: | ---: |
| hard cap | 5,000 | 5,000 | .8987 | .8954 |
| hard cap | 10,000 | 10,000 | .9022 | .9000 |
| hard cap | 20,000 | 19,421 | .9037 | .9000 |
| hard cap | 50,000 | 20,975 | .9037 | .9000 |
| hard cap | 100,000 | 20,975 | .9037 | .9000 |
| whole posting | 5,000 | 5,021 | .8987 | .8954 |
| whole posting | 10,000 | 10,014 | .9022 | .9000 |
| whole posting | 20,000 | 19,425 | .9037 | .9000 |

The materialized shortlist contains only 1024 addresses and therefore exposes
about 21k documents per query.  Rows at 50k and 100k are explicit exhaustion
rows, not evidence that R4 can serve those budgets.  R4 is stronger than the
generic semantic pilot at comparable 5k–20k budgets, but remains far from the
`.99` gate in this frozen configuration.  The narrow conclusion is that this
verified R4 address stream is a better candidate generator than generic
K-means here, while its 1024-address shortlist is insufficient for a
50k-quality claim.

## Reproducibility and limitations

Compact receipts are:

- `2026-09-12-semantic-routing-scale-r4-gate-result.json`
- `2026-09-12-semantic-routing-r4-comparator-result.json`

Per-query rows are kept outside Git under
`E:\_repoz\agent-memory-workspaces\semantic-routing-scale-r4-gate-raw.json` and
`...-r4-raw.json`; each receipt records their SHA-256.  The comparator validates
all mapping files against the R4 materialization manifest before traversal.
`posting_entries_touched` is a logical complete-posting read proxy, not a
measurement of MDBX/OS pages or latency.  Teacher IDs are used only for recall
and worst-query diagnostics.

## Next gate

Do not start THQ physical-layout or MDBX claims from this result alone.  The
next focused experiment is to regenerate/verify a semantic R4 shortlist with a
larger address frontier (or a full K8/R4/K32 cascade) and repeat the same
teacher-recall protocol.  Only if that candidate generator reaches the target
frontier should THQ-ADC reranking and page-level materialization be evaluated.
