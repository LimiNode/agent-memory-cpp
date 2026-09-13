# Native packed representative control for R4 K=16 (2026-09-14)

## Question

Does the K=16 representative prefix have a credible native packed-scoring
cost, and how does that arithmetic cost scale against K=8 and K=32?

This is a component control for the K=16 cascade. It is not a full native
route replay: the measured address set is the frozen 1,024-address shortlist
per query, not all occupied R4 addresses.

## Setup

- the same three frozen R4 seeds and DE-1M materializations used by the K=16
  cascade;
- symmetric unsigned INT8 representative records with one FP32 scale;
- first K representatives per address, with K in `{8, 16, 32}`;
- 152 queries and 1,024 frozen shortlist addresses per query;
- one untimed warm-up pass and three measured passes per seed/prefix;
- native executable `agent-memory-neuroute-r4-representative-codec.exe`,
  `--benchmark-dot 8 uniform 0`;
- receipt: `2026-09-14-r4-k16-native-representative-control-result.json`.

The native command measures INT8 decode, dot products, and per-address maximum
for the selected 1,024 addresses. It does not measure route fusion, posting
reads, THQ reranking, MDBX, or OS pages.

## Result

Values below average the three seed-level summaries. Timing is the native
`decode_dot_max_ms` distribution over 456 measured query/pass samples per
prefix (p50/p95 are averages of the seed-level quantiles).

| prefix | mean representatives scored/query | p50 ms | p95 ms | relative p95 vs K=16 |
| ---: | ---: | ---: | ---: | ---: |
| K=8 | 8,020 | 4.611 | 4.874 | 0.605 |
| K=16 | 13,842 | 7.398 | 8.061 | 1.000 |
| K=32 | 18,586 | 9.701 | 11.215 | 1.391 |

The measured work is sublinear in the nominal prefix because many addresses
have fewer than 32 representatives. K=16 is materially cheaper than K=32 in
this native component control, while retaining the stronger logical route
frontier established by the preceding K=16 cascade experiment.

## Interpretation

The dense representative scorer is not inherently limited to the Python/NumPy
implementation: an existing native packed INT8 kernel evaluates the measured
1,024-address component in single-digit milliseconds at p95. This supports
continuing to a full native route execution experiment. It does **not** show
that the full 65k-address K=16 route is cheap; scaling the same kernel to all
occupied addresses, route fusion, and candidate handling remains unmeasured.

The result is therefore a native arithmetic control, not a production
selection. The executable was built outside this checkout; its SHA and source
SHA are bound in the receipt and the fail-closed audit.

## Provenance and limitations

The receipt binds the codec and layout manifests, executable, source, clipped
K-prefix count sidecars, and nine native result files. The audit reports
`PASS` for all nine matrix rows. Physical page bytes, memory high-water marks,
cache state beyond the native warm-up, and end-to-end query latency were not
measured. INT8 score fidelity for the full K=16 route is inherited from the
earlier representative-codec evidence; this control measures cost only.

## Next check

Use the same packed kernel in a full occupied-address native route replay for
K=16, preserving deterministic address-score ordering and three-seed candidate
fusion. Only after that end-to-end replay should physical posting/page and
MDBX measurements be considered.
