# North Star reset: quality and persistent-footprint gates

Status: executed corrective quality gate and storage controls; production
activation remains disabled pending native end-to-end confirmation.

## Why this wave exists

The earlier FP32-removal gate measured qrels only after restricting the corpus
to the routed candidate set. That is useful for ranking diagnostics but cannot
answer whether the production retriever returns relevant documents. This wave
separates full-corpus direct THQ quality from candidate-local reranking and
counts all persistent representations instead of advertising one layer's size.

## Gate A: product quality

Runner: `run-thq-quality-gate.py`.

Compared for every 152 frozen queries:

* direct packed-ordinal THQ top-10 over the full 1M corpus;
* THQ top-10 within the routed whole-posting candidate stream;
* THQ → FP32 top-10 within that candidate stream (offline control only);
* exact E5 teacher top-10.

The receipt must report qrels nDCG@10 as primary, candidate survival
separately, and teacher overlap only as a diagnostic. No candidate slab is a
persistent index.

The executed 152-query result (`quality.receipt.json`, raw SHA
`62517d74e9741bb71c2e443ef78a2f62ef7da9f1c89b2146d6a0167b4142b641`) was:

| output | mean qrels nDCG@10 | mean teacher overlap | candidate survival |
| --- | ---: | ---: | ---: |
| direct packed THQ, full 1M corpus | 0.63665 | 0.81447 | 0.99276 |
| candidate-local THQ | 0.63797 | 0.81316 | 0.99276 |
| candidate-local FP32 rerank | 0.65420 | 0.99276 | 0.99276 |
| exact E5 teacher | 0.65403 | 1.00000 | 0.99276 |

Lower THQ teacher overlap therefore does not produce the same-sized product
quality loss: direct THQ retains about 97.3% of teacher mean nDCG (absolute loss
0.01738, relative loss about 2.66% versus the teacher). This is a quality
diagnostic, not authorization for a THQ-only product path: it does not
measure a THQ shortlist followed by a compact final reranker, native latency,
or held-out qrels.

The predeclared acceptance gate for a production candidate is stricter than
this exploratory result: paired mean nDCG loss must be explicitly capped,
paired bootstrap confidence intervals must be reported, and p05/minimum
per-query quality plus an untouched-domain confirmation must pass. Historical
gates treated losses in the 0.005--0.008 range as material; this receipt does
not retroactively declare 0.01738 acceptable.

## Gate B: canonical versus duplicated THQ

`materialize-thq-layout-bakeoff.py` and
`native-thq-layout-bakeoff.cpp` compare a shared `doc_id -> 96 B` table with
the same packed codes in posting order. This is an explicit native gather
control, not a claim that the candidate stream is a complete three-seed
persistent store. Full posting duplication and MDBX page accounting remain a
follow-up before production selection.

The native control over the current 762,082-entry routed workload measured
canonical gather p50/p95 0.254/0.310 ms versus sequential duplicated payload
0.029/0.042 ms. These are warm resident-RAM one-byte-per-record touch metrics,
not full 96-byte ADC scoring, mmap/MDBX page-fault latency, or production
retrieval latency. Its duplicated file is only this routed control stream (76.2
MB), not a complete three-seed persistent store, so it cannot justify
duplicating production THQ.

## Gate C: K16 representative storage

`audit-k16-shared-representations.py` validates the frozen layout manifest and
computes the complete persistent footprint for duplicated INT8 vectors versus
`rep_doc_id` references plus one shared document-major compact table. Quality
and latency are fail-closed as `PENDING_NATIVE_REPLAY` until a native receipt
for both representations is supplied.

The manifest-bound storage accounting is decisive about scale: three separate
INT8 representative layers plus sidecars total 1,245,127,530 bytes, while
`rep_doc_id` references plus one shared 388,000,000-byte document table total
479,793,758 bytes (61.5% less). Native quality/latency replay is still needed
before selecting the shared form. The currently measured subtotal of shared
INT8, K16 sidecars, and canonical 96-byte THQ is 575,793,758 bytes (549.1 MiB);
persistent R4 postings and MDBX overhead remain explicitly pending. The K1
AoSoA-32 table and its scale sidecars are counted separately when the K1
materialization manifest is supplied; until then this remains a partial
measured subtotal, never a total index footprint.

## Interpretation rule

No gate licenses production merely because one layer is smaller or faster.
The decision requires full footprint, qrels quality, persistent pre-query
materialization, and native physical work (entries, duplicates, bytes/pages,
and overshoot).
