# Task-aware static locator protocol

Date: 2026-08-23. This is a separate, predeclared successor to random static
locator calibration. It is not permitted to use French confirmation data.

## Split discipline

The frozen Spanish 25k materialization is split deterministically by the first
eight bytes of `SHA-256("task-aware-static-locator-v1\\0" + UTF-8 query_id)`,
ordered ascending: first 324 query IDs are selector-training, next 162 are
configuration-selection, and final 162 are internal evaluation. No bit, band
order, width, or radius choice may inspect the internal-evaluation queries.
Any subsequently frozen choice requires a new untouched confirmation split.

## Comparator and objective

Keep ITQ-256 as the ranking representation and 16-bit MIH routing bands. The
random 64–128 bit r3/r4 frontier is the non-neural baseline. On selector
training queries, score each bit by its E5-oracle pair discrimination; choose
a nested bit subset and band assignment using only that score. On the
selection partition, choose one predeclared width/radius point by E5 survival
subject to the same 25% candidate and fresh-full-code latency budget. The
internal evaluation then reports the frozen point once.

## Executable selector definition

For each selector-training query `q`, let `P(q)` be its deterministic exact
E5 top-10 document positions, ordered by `(descending score, document ID)`.
Let `N(q)` be 128 unique non-positive document positions drawn without
replacement by NumPy PCG64 from the seed encoded by
`SHA-256("task-aware-static-locator-v1-negative\\0" + query_id)`; the first
eight digest bytes are a little-endian unsigned seed. The document corpus and
frozen ITQ codes are otherwise unchanged.

For bit `b`, define its primary score as:

```text
mean over q [ mean over p in P(q) match(q_b, p_b)
              - mean over n in N(q) match(q_b, n_b) ]
```

where `match` is one for equal binary values and zero otherwise. Ties are
broken by ascending bit position. Select bits greedily: at each step choose the
remaining bit maximizing `primary_score - 0.25 * mean_abs_correlation` with
the already selected document-code bits; an empty selected set has redundancy
zero. The document-bit correlation is the absolute Pearson correlation over
all 25k frozen document codes. This produces one 128-bit ordered sequence;
the nested 64, 80, 96, and 112-bit subsets are its prefixes. For a selected
width, fill 16-bit bands in that sequence order.

The only treatments are all five widths and the nested r3-to-r4 prefix
schedule, including the observed first row exceeding either candidate fraction
or latency budget. Do not censor a row from an independent-null estimate.
For each width, the fresh full-code `m19` comparator uses 162 selection-query
positions, one warm-up, seven repeats, and the same selected executable.

Among rows whose observed candidate fraction is at most 25% and whose p50 is
at most that fresh comparator, choose the lexicographic maximum of
`(E5 survival after ADC, reranked nDCG@10, -candidate fraction, -p50, -width,
-r4-prefix)`. If no row is feasible, emit an explicit no-selection result and
do not evaluate an invented fallback. The frozen selected row is evaluated
once on the 162 internal-evaluation queries.

The evidence archive must bind all three query-ID sets, objective inputs,
selected positions, selection decision, native source/config manifests, and
per-query E5/nDCG replay. Since these Spanish queries have appeared in earlier
exploratory evidence, this is an internal holdout rather than an external
confirmation set. A selector that cannot beat the random frontier on the
internal evaluation does not authorize a learned locator implementation.
