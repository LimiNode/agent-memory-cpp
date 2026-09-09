# Prototype teacher cache and global student-hard negatives

Date: 2026-09-03. PR #284 continues the nonlinear prototype metric from
#283 and addresses the mismatch identified in review.

## Question

Does the shared nonlinear binary metric improve when its loss includes the
false positives produced by the current hard-Hamming student over the entire
K8 prototype pool, rather than only teacher ranks 64, 256, and 1,023?

## Protocol

`materialize-neuroute-prototype-teacher.py` computes a deterministic FP32 dot
product top-1,024 for exactly the frozen 8,141-query pool. It writes a compact
NPZ containing `teacher_top_prototypes` and a manifest binding the source hash,
dimensions, block size, score, and tie-break rule. The neural runner accepts
that cache through `--teacher-cache`; the production input still supplies the
query and prototype vectors.

The runner now exposes four diagnostic policies:

* `fixed_teacher_ranks` — the original three negatives;
* `global_random` — eight deterministic corpus negatives outside the teacher
  top-1,024;
* `student_hard` — eight globally closest Hamming prototypes that are absent
  from the teacher top-1,024, followed by one retraining pass;
* `student_hard_x2` — repeat mining and retraining once more.

`run-neuroute-prototype-binary-neural-frontier.py` executes all four policies
with the same loaded source and teacher cache, producing one provenance-bound
frontier report. Each policy/width cell is atomically checkpointed and bound
to the input, teacher, contract, and runner hashes. Interrupted runs resume
without accepting stale cells. The optional Numba path accelerates only the
same exhaustive XOR+popcount calculation; the NumPy implementation remains
the dependency-free fallback.

`materialize-neuroute-prototype-real-source.py` reconstructs the frozen real
8,141-query pool used by the preceding router studies: 153 German queries are
selected from the 305-query DE E5 materialization by the established seeded
SHA-256 ordering, then concatenated with the manifest-bound 7,988 MIRACL
ES/FR/RU queries. The source materialization binds those queries to one frozen
K8 prototype geometry. `materialize-neuroute-prototype-synthetic-source.py`
exists only as a controlled geometry stress fixture and explicitly records
`real_corpus_evidence: false`.

Mining is an offline exhaustive XOR+popcount operation over K8 prototypes. It
is a teacher/ceiling diagnostic and does not license a 65K-address scan at
runtime.

Hamming Top-M uses `(distance, prototype_id)` ordering. In particular, a large
tie at the boundary is resolved by ascending prototype id before the shortlist
is emitted; sorting an arbitrary `argpartition` subset after the fact is not a
valid deterministic tie-break.

## Diagnostics

Each trained width records soft expected-Hamming ranking accuracy, hard-sign
Hamming ranking accuracy, mean absolute logit, the fraction of logits with
absolute value below 0.1, and soft/hard rank correlation. These metrics make
the surrogate-to-production quantization gap explicit.

For mined policies, the report also records post-retrain survival of the
mined examples in student top64, top256, top1024, and top4096. The
`global_random` treatment is intentionally standalone; it does not trigger a
student-mining pass.

## Full real-corpus frontier

The authoritative run used all 8,141 frozen queries and all 454,322 frozen K8
prototypes. The reconstructed source NPZ has SHA-256
`21c6c265a8110cc6c209ae480caa72b8dee95693299afb6fd07df18d1d3f6bce`.
The exact top-1,024 FP32 teacher cache has SHA-256
`bdc5972054a4a32f1fb9b142a9ef1510b08ffb6e169dc12d97c31e5a15bf9f87`.
The combined frontier report, `tmp/real-neural-frontier-8141.json`, has
SHA-256
`5e5f43fb81dbe2337c72e624894cddfbbb84a4ecb0653d501c4aa26db3a96be9`.

Internal teacher-prototype recall at a fixed 4,096-prototype shortlist was:

| policy | 16 bits | 24 bits | 32 bits | 48 bits | 64 bits | 96 bits | 128 bits |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fixed teacher ranks | 0.00125 | 0.00022 | 0.00026 | 0.00021 | 0.00021 | 0.00022 | 0.00020 |
| global random | 0.18065 | 0.21745 | **0.25039** | 0.23436 | 0.11565 | 0.15509 | 0.17592 |
| student hard | 0.00342 | 0.00434 | 0.00314 | 0.00269 | 0.00019 | 0.00319 | 0.00166 |
| student hard x2 | 0.00346 | 0.00472 | 0.00077 | 0.00411 | 0.00032 | 0.00162 | 0.00030 |

The strongest `global_random` treatment produced this internal shortlist
curve:

| bits | recall@1,024 | recall@2,048 | recall@4,096 | recall@8,192 | bit entropy |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 16 | 0.03794 | 0.07002 | 0.18065 | 0.35809 | 0.228 |
| 24 | 0.04281 | 0.11300 | 0.21745 | 0.41505 | 0.165 |
| 32 | 0.04836 | 0.12634 | **0.25039** | 0.45277 | 0.146 |
| 48 | 0.04577 | 0.11895 | 0.23436 | 0.42434 | 0.157 |
| 64 | 0.03256 | 0.06140 | 0.11565 | 0.30369 | 0.315 |
| 96 | 0.03487 | 0.06723 | 0.15509 | 0.32476 | 0.282 |
| 128 | 0.03756 | 0.06875 | 0.17592 | 0.34749 | 0.315 |

The 32-bit result is not a config-only effect: recall@4,096 is 0.24459 on
config and 0.25039 on internal queries. It is still far below a usable
geometry, and its worst internal query recovers only 17 of 1,024 teacher
prototypes. Random selection would recover about
`4096 / 454322 = 0.00902`, so the model learns signal, but not enough to act as
the product address selector.

Student-hard retraining generally removes the particular mined false
positives: their post-retrain top-4,096 survival is approximately
0.006%-0.193%. That local success does not preserve the global teacher
geometry. Soft ranking accuracy remains around 0.73 while hard-sign recall is
poor, and the useful 32-bit cell has only 0.146 mean bit entropy. Together
these observations identify a surrogate-to-hard-sign mismatch and bit
collapse rather than insufficient bit width alone.

## Decision

The predeclared continuation condition is not met. Recall peaks at 32 bits.
Although the corrected tail rises from 64 to 96 to 128 bits
(`0.11565 → 0.15509 → 0.17592`), it remains only 70% of the 32-bit peak and
does not make the late-width frontier promising. Therefore this study does
not run 192/256-bit variants.

The study also stops before prototype-to-address deduplication, local K8, and
the full R4 cascade. Replaying a selector that preserves only 25.04% of the
teacher top-1,024 at 4,096 prototypes, with poor worst-query behavior,
cannot license a product path. Global K8 scanning across all R4 addresses
remains outside the intended product line.

This closes the tested `384 -> 96 -> 64 -> B` architecture and current pairwise
objective as the present implementation ceiling. It does not establish that
all learned binary prototype metrics are impossible. A materially different
architecture or quantization-aware objective must first pass the frozen
prototype-geometry gate; only then should it be replayed through
prototype-to-address deduplication, local K8, and the complete R4 cascade.

## Limitations

The matrix uses one deterministic training seed and one fixed half-pool
training/internal split. The configuration rows reuse the training half;
the internal rows are the held-out geometry check. This licenses the stopping
decision for this implementation rather than a broad impossibility claim.
Four frontier jobs ran concurrently; their wall-clock and throughput figures
are directional and must not be used as stable latency evidence. Exhaustive
Hamming scanning is an offline quality ceiling, not the proposed runtime
selector. The synthetic source fixture is a stress-test utility and contributes
no real-corpus evidence to the result above.
