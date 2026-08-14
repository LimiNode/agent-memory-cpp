# Arbitrary-m fixed-radius MIH reference

## Historical v1 — unmatched wide-control diagnostic

The original v1 compared m=19 radius-two bands with an `8 x r4 + 8 x r3`
m=16 control. That 25,712-probe control is deliberately wider than canonical
minimum r56 coverage. The raw archive remains historical evidence, but its
former negative m=19 interpretation is withdrawn and must not be used as a
matched-r56 frontier conclusion.

## 2026-08-14 — matched canonical-r56 replay (v2)

**Question.** How does the predeclared `m=19`, `9 x 14 + 10 x 13`, all-r2
partition compare with the actual canonical m=16 r56 schedule?

**Protocol.** Both arms use fixed ITQ-256/50 seeds 52--56, the 25k training
materialization, untouched 22,607 documents / 1,252 queries, Hamming top-768,
binary ADC top-256 and exact E5 rerank. There is no held-out selection.

Both schedules have the same pigeonhole guarantee: after probing the declared
local radii, an undiscovered document has full Hamming distance at least 57.

| Arm | Local schedule | Probes/query | Candidates/query | Posting visits/query |
|---|---|---:|---:|---:|
| m=16 canonical | 9 x 16-bit r3, 7 x 16-bit r2 | 7,232 | 3,061.0 | 3,356.3 |
| m=19 | 9 x 14-bit r2, 10 x 13-bit r2 | 1,874 | 4,349.4 | 4,943.8 |

| Arm | Raw-union oracle survival | ADC@256 survival | nDCG@10 |
|---|---:|---:|---:|
| m=16 canonical | 0.8923 | 0.8917 | 0.7819 |
| m=19 | 0.9262 | 0.9249 | 0.7889 |

**Result.** m=19 makes 74% fewer bucket probes, but has 42% more unique
candidates and 47% more posting visits. It gains 0.0293--0.0369 raw-union
survival and 0.0288--0.0362 ADC survival per seed; every paired 95% bootstrap
interval excludes zero in that direction.

**Interpretation.** This is a three-dimensional frontier, not a no-go. m=19
trades fewer random index lookups for more sequential posting/rerank work and
better E5-oracle preservation. A native storage benchmark is needed before any
latency decision; neither probe count nor candidate count alone is a latency
proxy.

### Evidence replay hardening (v4)

The v4 evidence packager reconstructs each of the five deterministic paired
bootstrap reports directly from the preserved control and challenger NPZ files
and requires exact JSON equality, including all confidence intervals. It also
requires byte-identical contract bytes from the measured source commit and the
exact contract-bound calibration and evaluation materialization manifests. The
bootstrap reports retain the source-file bundle identity used for that replay.
This hardening changes neither matrix rows nor the reported frontier.

Draft evidence v4 archives measured source
`5ec7c933c4410fc028a4c1db7f010fc903cd5786`: archive SHA-256
`efa3bcd584a952bbfc74a623ad5a1ef3778acb6b632e7c46e4f155c5f8946e78`,
bundle-root SHA-256
`ce178946f236df820bddcb53a852bd2ecec09622a18b46496bcb90515dd0de1d`.

**Limitations.** The evaluator is a Python reference harness and tests one
predeclared m=19 partition. It does not optimize m, layouts, or a native MDBX
lookup implementation.

**Next checks.** Predeclare a minimal-schedule `m=15..21` sweep and report
probes, postings, candidates and quality together. Keep that separate from the
global-bound progressive execution study.
