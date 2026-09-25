# AAQ / score-aware LSQ / QINCo2 / residual-hybrid wave

Date: 2026-09-23

This wave uses the recovered canonical E5/R4 bundle and frozen THQ top-128
shell. Executed arms below are bound to candidate stream SHA
`d76cabd553bbd1453908a9cd28fe3578895cf2cd3876026a5b1fd5813839bc79`.

The first AAQ/QINCo2 artifacts from this wave used an invalid `(3,D)` threshold
view and are superseded. The corrected rerun uses the canonical `(D,3)` layout.
The superseded values are retained only in the receipt's explicit
`superseded_results` section and are not active evidence.

## Query-local LSQ diagnostics

The runner freezes Faiss LSQ32/LSQ48 codebooks. The historical control is now
named `query_dot_greedy_lsq`: it independently maximizes `q·codeword` in a
local neighbourhood. A separate `oracle_score_error_lsq` arm evaluates the
complete reconstructed cosine for every replacement and minimizes squared
error against the exact document score. Both are query-dependent diagnostics,
not AAQ and not deployable document codes.

| arm | side bytes before norm | mean nDCG@10 | mean score MSE | changed-code fraction |
| --- | ---: | ---: | ---: | ---: |
| frozen Faiss LSQ32 | 32 | 0.657264 | 1.97e-5 | 0.000000 |
| query-dot greedy LSQ32 | 32 | 0.630466 | 2.64e-4 | 0.875071 |
| oracle score-error LSQ32 | 32 | 0.654201 | 1.01e-10 | 0.181331 |
| frozen Faiss LSQ48 | 48 | 0.661515 | 1.53e-5 | 0.000000 |
| query-dot greedy LSQ48 | 48 | 0.621929 | 4.61e-4 | 0.867880 |
| oracle score-error LSQ48 | 48 | 0.654201 | 3.86e-11 | 0.128446 |

The independent audit is `PASS` with zero mismatches over 912 persisted rows.
Oracle score fidelity therefore does not imply qrels improvement: the exact
score MSE collapses, while nDCG still falls to `.654201`. This closes the
simple query-local post-hoc hypothesis without claiming that trained AAQ/AVQ
objectives are equivalent.

Final-norm accounting is explicit. FP32 norm is a 4-byte oracle sidecar;
FP16 is a 2-byte candidate. In this shell FP16 changed ordered top-10 for
146/456 LSQ32 rows and 166/456 LSQ48 rows across the three diagnostic arms,
so a production direct-cosine gate must retain FP32 or test another
approximation explicitly.

## Official AAQ bounded pilot

The first-author repository
`jzhang-0/Anisotropic-Additive-Quantization` is source-pinned and not vendored
because no explicit license file was observed. The runner invokes its official
`ScannAQ`, `CoordinateDes`, and score-aware codebook update. The bounded PCA32
residual pilot uses M=8, K=16, 4096 training rows and one official fit
iteration. After correcting the threshold layout, the official PCA32 arm is
`.649382` and the PCA32 projection upper control is `.654714` at the same
108 B / 96 B totals. Independent persisted decode audit: PASS, 304/304
top-10. This is a mechanics/provenance gate, not a full-dimensional AAQ32/48
capacity result.

The requested full-dimensional control uses the same bounded protocol with
`D=384`, `M=8`, `K=16`, 4096 training rows, one fit iteration, and three
coordinate passes. It obtains `.529736` nDCG at 108 B/doc, while the direct
full-dimensional reconstruction upper control is `.654201` at 96 B/doc; the
independent decode audit is PASS. The large gap to the upper control is a
bounded-training/4-byte-capacity negative diagnostic, not a converged family
claim. It removes the PCA32 bottleneck as an explanation for the earlier
result.

## QINCo2 source and 16-byte pilot

The official Facebook Research QINCo2 checkout at revision
`5a324954d5c9b3700d4407d6cc24c3db6e52890e` is exercised by a strengthened
source smoke with K=256, A=8, B=4, `model.eval()`, and repeated encode/decode
determinism checks. `quality_status` remains `NOT_EXECUTED` for the smoke.

A separate bounded CPU fit uses the official model with 16 stages, K=256,
A=8, B=4, 4096 training rows, 5 epochs and 640 optimizer steps. Final loss
falls from 27.9266 to 10.2852; the corrected canonical-threshold mean nDCG is
`.640309` at 116 B/doc, with a 14.76 MB global model. The persisted model/code
replay is independently audited (`PASS`, 152/152). This is explicitly an
undertrained pilot, not a family-level negative result.

The official QINCo2-S control was subsequently run from the pinned checkout
with `L=2`, `M=16`, `K=256`, `A=16`, `B=32`, `dh=256`, and `de=128` on the
canonical 25k training subset. The upstream scheduler completed three short
epochs before the best checkpoint was frozen. The source-bound replay on the
same 152-query THQ top-128 shell obtains mean nDCG@10 `0.616420` at 112 B/doc
(16-byte QINCo2 code plus THQ4), with p05/worst-query nDCG `0.0`. The official
model persisted-code decode audit is `PASS` with zero top-10 mismatches over
152 queries. This is a bounded undertrained control, not a converged QINCo2
result and not a production selection.

## Residual-hybrid matrix

The matrix writer now fails closed: `paired_candidate_stream` is true only
when every row carries the same candidate hash. Each emitted row also carries
the exact `candidate_stream_hash` used for the pairing decision. It reports normalized
`side_bytes`, `thq_plus_side_bytes`, `global_model_bytes`, and
`full_1m_footprint_bytes`; direct cosine arms are charged a FP32 norm unless
their payload already includes one. The regenerated matrix has
`paired_candidate_stream=true` for the canonical SHA above.

| hybrid/control | total bytes/doc | mean nDCG@10 | status |
| --- | ---: | ---: | --- |
| THQ + Faiss LSQ32 + FP32 norm | 132 | 0.657264 | source-bound, audited; 12.58 MB model |
| THQ + Faiss LSQ48 + FP32 norm | 148 | 0.661515 | source-bound, audited; 18.87 MB model |
| THQ + TQ+ exact-wide composite | 156 | 0.654486 | corrected canonical replay |
| THQ + official AAQ PCA32 bounded | 108 | 0.649382 | source-pinned bounded pilot |
| THQ + official AAQ full384 bounded | 108 | 0.529736 | source-pinned bounded pilot; negative capacity diagnostic |
| THQ + QINCo2 16B bounded pilot | 116 | 0.640309 | source-bound local pilot; audited |
| THQ + official QINCo2-S 16B bounded | 112 | 0.616420 | official source-bound control; decode audit PASS |

## Provenance and next gate

Large generated arrays remain outside Git, while committed runners, receipts,
source hashes, and independent audits bind all evidence. Full-dimensional
AAQ32/48 remains a bounded negative diagnostic rather than a converged capacity
result; converged QINCo2 remains open. No production codec is selected.
The next gate is the compressed-native complete cascade on the canonical
stream, using real codes/layouts, direct scoring, FP32 norm accounting,
native p50/p95/p99, cold/warm behavior, pages, and independent top-10 parity.
