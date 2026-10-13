# Native document-routing full-cascade bake-off

## Question

Do the simple PCA/E5 centroid routers remain useful after a native
Hamming@768 → ADC@64 → exact@10 document cascade, and is their coarse cost
worth the quality difference?

## Protocol

The materializer freezes the same DE-1M document vectors and exact E5 teacher
labels used by the PCA12 studies.  It builds the frozen PCA12 partition,
PCA-centroid K=1, E5 centroid tables K=1/2/4/8, and three Direct4096 model
checkpoints (seeds 13/37/101).  Native C++ then routes directly to document
postings, scans the selected documents with the existing 256-bit codes, applies
ADC@64, and exact-reranks the final 64 documents to top-10.  Budgets are 32k
and 64k candidates; one warm-up and three measured passes are used.

Materializer: `tools/agent-memory-bench/materialize-native-document-routing-bakeoff.py`.
Native runner: `tools/agent-memory-bench/native_document_routing_bakeoff.cpp`.
The executable is wired as `agent-memory-native-document-routing-bakeoff` in
`tools/agent-memory-bench/CMakeLists.txt`.

Materialization manifest:
`tmp/native-document-routing-bakeoff-v1/manifest.json`.
Manifest SHA-256: `6f2cef0002d74e204b749d268c8746e2d0b8ef1a16a99ce38ec2797ba81171c0`.
Native result:
`tmp/native-document-routing-bakeoff-v1/native-result.json`.
Result SHA-256: `0ad694257ee67ebf29c09d9b8b7bbb9135687cf0962acd4ac43d0ec557115eb1`.

## Result

| Router | 32k final overlap / nDCG | 64k final overlap / nDCG | p95 total, 32k / 64k (ms) |
| --- | ---: | ---: | ---: |
| PCA threshold | .547 / .518 | .668 / .565 | 19.16 / 37.53 |
| PCA centroid K=1 | .424 / .372 | .559 / .473 | 19.68 / 36.82 |
| E5 centroid K=1 | .366 / .339 | .482 / .408 | 20.87 / 38.79 |
| E5 centroid K=2 | .455 / .417 | .573 / .481 | 22.75 / 40.90 |
| E5 centroid K=4 | .541 / .479 | .660 / .551 | 26.50 / 44.51 |
| E5 centroid K=8 | **.656 / .581** | **.738 / .596** | 33.80 / 51.98 |
| Direct4096 (best seed) | .572 / .569 | .730 / .638 | 21.06 / 38.83 |

The native stage diagnostics show the expected monotone losses: candidate
coverage is higher than Hamming coverage, which is higher than ADC coverage;
the exact top-10 overlap equals the ADC shortlist overlap on this cascade.

## Interpretation

E5 centroid K=8 gives the best quality among the centroid methods, but costs
roughly 2.7× the 32k p95 of the PCA threshold route and 1.4× the p95 of
Direct4096.  Direct4096 is competitive at 64k and has a small additional
quality advantage over E5 K=8 on qrels in this particular native replay, but
requires a learned MLP and three model variants.  PCA threshold remains the
best low-cost control.

These are not the earlier routing-ceiling numbers: the Hamming and ADC stages
materially reduce the overlap before exact reranking.  Therefore router choice
must be made on this composed cascade, not on cell-recall alone.

## Limitations

The benchmark uses native in-memory postings built from frozen cell assignments;
it does not open MDBX pages or measure production queueing.  The query set is
the 152-query DE-1M config/internal split, and the Direct4096 row is a
pretrained checkpoint control.  The result is a native full-document cascade
measurement, not a claim that these routes are already production-integrated.

## Decision

Keep PCA threshold as the minimum-cost baseline.  E5 K=4 is the natural
balanced candidate only if its qrels gain survives an MDBX-backed replay; E5 K=8
is a quality profile with a clear latency cost.  Direct4096 merits no further
optimization until its model inference and posting representation are measured
against E5 K=4/K=8 in the same storage backend.  MMR, weighted-kNN, and the
current Hungarian hybrid remain closed without native porting.
