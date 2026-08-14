# Arbitrary-m fixed-radius MIH reference

## 2026-08-14 — m=19 radius-two partition versus current m=16 schedule

**Context.** Research branch `agent/mih-arbitrary-m-reference` (PR #142, source
commit `57dce6a4593e134436e049243020862198528f40`). This is a held-out,
five-seed comparison; neither partition nor radius was selected from the
held-out outputs.

**Question.** Can a 19-band partition reduce radius-56 candidate work while
retaining enough E5-oracle candidates to improve the current exact-radius
pipeline?

**Protocol.** Both arms use deterministic 256-bit ITQ-50 encoders for seeds
52--56, the fixed 25k training materialization and the untouched 22,607
document / 1,252 query evaluation materialization. Both retain the same
post-union stages: Hamming top-768, binary ADC top-256 and exact E5 rerank.

| Arm | Partition and local radii | Bucket probes/query | Exact radius-56 guarantee |
|---|---|---:|---|
| Control | 16 x 16; eight radius-4 then eight radius-3 bands | 25,712 | Yes: the local-radius sum is 56. |
| Challenger | 9 x 14 plus 10 x 13; all 19 bands radius 2 | 1,874 | Yes: if every band differed by at least 3 bits, full Hamming distance would be at least 19 x 3 = 57. |

**Result.** m=19 greatly lowers reference index work but loses a material,
consistent portion of the E5 oracle before Hamming. Values are means across
the five fixed seeds; candidate and posting counts are per query.

| Arm | Candidates | Posting visits | Raw-union oracle survival | ADC@256 oracle survival | nDCG@10 |
|---|---:|---:|---:|---:|---:|
| m=16 control | 8,599.8 | 11,126.7 | 0.9891 | 0.9847 | 0.8004 |
| m=19 radius-two | 4,349.4 | 4,943.8 | 0.9262 | 0.9249 | 0.7889 |

For every seed, paired bootstrap CIs exclude zero in the adverse direction for
raw-union and ADC@256 oracle survival. The challenger reduces candidates by
4,120--4,305/query and posting visits by 5,999--6,263/query, but loses
0.0621--0.0640 raw-union survival and 0.0590--0.0603 ADC survival.

**Interpretation.** The pigeonhole guarantee proves only coverage of documents
within full Hamming radius 56. It does not make the probe schedule quality
equivalent: the narrower m=19 bands with radius-two probing return a much
smaller union and exclude many E5-oracle documents whose full codes are not
within that guaranteed radius. This is a useful negative frontier point, not a
reason to replace the current m=16 schedule.

**Limitations.** This is a Python reference evaluator and reports reference
candidate generation work, not a native latency claim. It tests one
mathematically motivated m=19 partition; it is not an optimizer over all
partitions or radius schedules.

**Next checks.** Keep arbitrary-m reference partitions separate from algorithmic
execution changes. The next PR tests progressive exact Hamming top-K with
first-discovery dedup and a conformance proof against full Hamming rankings;
only after that should wider/sparser extraction indexes or corpus-scale sweeps
be compared.
