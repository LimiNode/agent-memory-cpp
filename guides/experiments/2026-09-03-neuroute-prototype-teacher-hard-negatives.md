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
frontier report.

Mining is an offline exhaustive XOR+popcount operation over K8 prototypes. It
is a teacher/ceiling diagnostic and does not license a 65K-address scan at
runtime.

## Diagnostics

Each trained width records soft expected-Hamming ranking accuracy, hard-sign
Hamming ranking accuracy, mean absolute logit, the fraction of logits with
absolute value below 0.1, and soft/hard rank correlation. These metrics make
the surrogate-to-production quantization gap explicit.

For mined policies, the report also records post-retrain survival of the
mined examples in student top64, top256, top1024, and top4096. The
`global_random` treatment is intentionally standalone; it does not trigger a
student-mining pass.

## Limitations and next checks

The current runner still uses the fixed `384 → 96 → 64 → B` bottleneck and
exhaustive prototype evaluation. Results are not a final-cascade claim. Run
the full 8,141-query cache for all widths and policies, inspect global hard
recall, then replay prototype→address dedup, local K8, and the complete R4
cascade before considering 192/256 bits or MIH.
