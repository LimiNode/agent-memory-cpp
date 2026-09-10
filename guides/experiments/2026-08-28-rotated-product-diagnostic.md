# Rotated-product routing diagnostic

Date: 2026-08-28. Canonical status: **HISTORICAL MEASUREMENT / CORRECTED REPLAY PENDING**.

## Question

Does an OPQ rotation improve a contiguous product partition of E5 vectors at
the same approximately 5% document-candidate mass? This is a routing diagnostic,
not an OPQ reconstruction benchmark or a production index selection.

## Historical measurement

The retained source-bound run used ES-1M, 648 queries, the frozen downstream
Hamming768 -> ADC256 -> exact top-10 cascade, and two implicit cell budgets.

| treatment | cells | candidate fraction | E5 oracle survival after ADC | reranked nDCG@10 | routing p95, ms |
| --- | ---: | ---: | ---: | ---: | ---: |
| raw contiguous product | 16,384 | .05105 | .51451 | .49279 | 22.37 |
| raw contiguous product | 65,536 | .05034 | .57593 | .51607 | 97.30 |
| OPQ-rotated product | 16,384 | .05066 | .68580 | .58850 | 19.81 |
| OPQ-rotated product | 65,536 | .05034 | .76219 | .62100 | 73.21 |

The retained summary has SHA-256
`5f370753af008da7b3a95ff4c82b7b9f93165f6f609d44ee058e57646900f605`.
The serialized OPQ artifact has SHA-256
`006423c59272f855fefc8a43d7a69a626cf7c05274defb0c7455a094d09557b3`.
These hashes preserve the historical run but are not an independent replay
receipt.

## Interpretation

OPQ materially improves this specific product partition and the 65,536-cell
row exceeds the preregistered `.70` exploratory survival gate. It does not show
that OPQ is an optimal routing transform: Faiss OPQ optimizes reconstruction
error, while routing utility is only evaluated downstream after training.

The original runner seeded NumPy but did not explicitly seed Faiss PQ
clustering. The canonical runner now sets `opq.pq.cp.seed` and uses one Faiss
thread for deterministic future materialization. The historical numbers remain
source-bound until a fresh run produces identical artifacts twice and receives
an independent evidence receipt.

## Missing controls and limitations

- Only raw contiguous and OPQ-rotated products were measured. Random orthogonal,
  PCA/whitened, and routing-supervised transforms are absent.
- The run is a single ES-1M calibration fixture, not multilingual or held-out
  confirmation.
- Timings are directional Python/Faiss measurements, not native serving latency.
- Candidate fraction is matched approximately; bytes, page reads, and cold
  behavior are not measured.
- Confirmation, native backend selection, and production activation remain
  forbidden.

## Next gate

Replay raw, seeded random-orthogonal, PCA/whitened, and seeded OPQ controls at
matched candidate mass. Build each transform twice from an empty output root,
require identical artifact SHA-256 values, then publish a compact evidence
receipt before drawing a current routing conclusion.
