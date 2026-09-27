# 2026-09-27 fidelity-heavy batch

The heavy numeric closure jobs were launched against the recovered canonical
DE-1M bundle and the 152-query frozen quality split. They run outside Git under
`E:\\_repoz\\agent-memory-workspaces\\fidelity-heavy-batch-v1` so that large
models and code arrays are not committed.

## Active jobs

| gate | configuration | output | status at launch |
| --- | --- | --- | --- |
| Faiss LSQ | payloads 32/48, 25k rows, `train_iters=50`, `train_ils_iters=32`, `encode_ils_iters=32`, seed `20260927` | `lsq-50-32/` | running |
| official Faiss OPQ/PQ | OPQ32x4 strong arm, `niter=50`, `niter_pq=40`, `niter_pq_0=40`, PQ k-means 40, seed `20260927` | `opq-official/` plus corrected replay | completed; first scorer superseded |

The input conversion is deterministic: the existing canonical `.npy`
evaluation arrays were written as raw little-endian streams required by the
replay runners. Their values are not regenerated or subsampled.

## Evidence policy

An output directory is considered executed only when the runner writes its
result plus model/code artifacts and the corresponding independent audit is
run. Empty logs or a terminated process are not receipts and must not be
quoted as quality evidence. QINCo2 convergence and Rust TurboQuant parity will
be started after these CPU-heavy jobs release the host; they remain pending
until their own persisted artifacts and audits pass.
