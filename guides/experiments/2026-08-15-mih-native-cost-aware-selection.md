# Native MIH calibration-only cost-aware selection

## 2026-08-15 - predeclared protocol

**Question.** Which member of the fixed-r56, near-equal `m=15..21` sparse-MIH
candidate family is admissible under a quality and binary-memory contract and
has the lowest measured native candidate-generator latency on calibration data?

**Why this is a separate study.** The native arbitrary-`m` matrix measured a
latency frontier only. Its 1,252-query result did not select a production
configuration, and exact fixed-r56 inclusion does not itself guarantee equal
ADC survival or reranked quality. This selection treats the previously exposed
authoritative supervised-disjoint root as calibration data. It makes no claim
on a new untouched split or dataset.

**Candidate family.** The contract fixes ITQ-256 seed 52 and 50 iterations,
contiguous near-equal widths, `m=15..21`, and the deterministic
minimum-enumerated-key radius schedule for each `m`. Every candidate has

```text
sum_b (local_radius_b + 1) = 57
```

and the native benchmark independently checks the fixed-r56 candidate union
and Hamming@768 shortlist for every calibration query. This selects a frozen
pair `(m, radius schedule)` from the declared family; it does not claim a
global optimum over all possible partitions or schedules.

**Quality gate.** The same 4,326 calibration queries run the full common
funnel: exact Hamming@768, binary ADC@256, and exact E5 rerank@256. For every
candidate, 10,000 deterministic query-bootstrap replicates provide the lower
95% bound for both:

```text
ADC oracle survival@256                 >= 0.9000
reranked nDCG@10 / full-E5 nDCG@10      >= 0.9800
```

The bootstrap uses separate recorded RNG seeds per metric and treatment. A
candidate failing either lower bound is ineligible regardless of latency.

**Memory gate.** Memory is reported before selection as three distinct values:

```text
shared ITQ-256 code store = document_count * 32 bytes
backend-specific index    = sorted keys + offsets + postings logical bytes
total resident binary     = shared code store + backend-specific index
```

The frozen limits are 8 MiB backend-specific and 10 MiB total resident binary
memory. Dense E5 vectors, process allocator overhead, and build-time scratch
space are intentionally outside this binary-index feasibility accounting and
must not be silently added to one backend only.

**Objective and tie-break.** Among configurations passing exactness, both
quality lower bounds, and both memory limits, select minimum warm native
candidate-generator p50. Ties use cascade p50, then total resident binary
bytes, then lexicographically smallest treatment id. The measured generator
is the whole sparse path from key enumeration through stable HammingTopK; it is
not a sum of separately timed components.

**Protocol.**

```text
py tools/agent-memory-bench/run-mih-native-cost-aware-selection.py run \
  --calibration-root <authoritative calibration root> \
  --executable <agent-memory-mih-native-sparse-arbitrary-m> \
  --output-root tmp/mih-native-cost-aware-selection-v1
```

The script materializes the native input from the same manifest in both roles;
that root's ITQ training ids are disjoint from its indexed documents. It writes
per-treatment Python quality reports and NPZ contributions, native configs and
reports, bootstrap reports, and a SHA-bound selection manifest.

**Next gate.** This step freezes only the MIH candidate configuration. Before
any backend or production conclusion, calibration must also freeze BinaryFlat
common-cascade limits and Binary HNSW parameters under comparable quality and
memory budgets. Then one new untouched benchmark evaluates Flat versus frozen
MIH versus frozen HNSW. A coarse locator is not admitted from this future
untouched result: such a trigger is post-hoc and requires another untouched
evaluation set.

## Result

The complete seven-row calibration replay passed the per-query fixed-r56 union
and Hamming shortlist conformance checks. The lower bounds below are the
predeclared 95% query-bootstrap bounds; binary memory is shared ITQ-256 codes
plus the backend-specific immutable sparse directory.

| m | ADC survival lower | nDCG retention lower | backend MiB | total MiB | generator p50 ms | cascade p50 ms | gate |
|---:|---:|---:|---:|---:|---:|---:|:---|
| 15 | 0.8397 | 0.9663 | 5.612 | 6.757 | 1.2292 | 1.4559 | fail quality |
| 16 | 0.8688 | 0.9749 | 5.418 | 6.564 | 1.0073 | 1.2447 | fail quality |
| 17 | 0.8879 | 0.9778 | 5.048 | 6.194 | 0.7625 | 0.9735 | fail quality |
| 18 | 0.9020 | 0.9826 | 4.608 | 5.754 | 0.6028 | 0.8120 | admissible |
| 19 | 0.9034 | 0.9852 | 4.234 | 5.380 | **0.4652** | **0.6667** | **selected** |
| 20 | 0.9260 | 0.9890 | 3.938 | 5.083 | 0.5276 | 0.7432 | admissible |
| 21 | 0.9426 | 0.9933 | 3.774 | 4.919 | 0.5798 | 0.8009 | admissible |

The locked rule selects `m19-minimum-probe-r56`: nine 14-bit bands, ten
13-bit bands, and local radius two in every band. `m20` and `m21` improve the
quality and binary-index footprint, but their additional posting, deduplication
and Hamming work makes the measured candidate generator slower on this sorted
directory representation. Conversely, `m15..17` would look attractive to a
cost-only selector but fail the independent quality gate. This is a calibration
selection result, not a production or cross-backend claim.

The frozen `m19` configuration is now the MIH arm for the later locked
confirmation. The next predeclared study still has to tune BinaryFlat/common
cascade and Binary HNSW on calibration under comparable contracts, then use a
new untouched evaluation dataset or split once for the Flat-versus-MIH-versus-
HNSW comparison. Raw reports and NPZ contributions remain external evidence;
the release archive binds their hashes, source snapshots, contract, and this
note.
