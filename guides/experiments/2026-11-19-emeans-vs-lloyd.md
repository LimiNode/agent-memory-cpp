# Faiss Lloyd versus CPU E-means

## Execution status

`faiss-cpu==1.16.0` was installed in an isolated Python 3.11.9 environment.
`from faiss.contrib.e_means import NumpyEmeans` imported successfully. The
experiment is therefore `EXECUTED`, not a Lloyd substitute and not an
O-means result.

The run is a synthetic smoke because this checkout has no frozen vector corpus
for the ANN R4 decision. Train, base, and query arrays are generated
independently from the same deterministic mixture; query rows never enter
training. The exact retrieval oracle is `faiss.IndexFlatL2`. Both methods use
the same eight-centroid, ten-epoch configuration and the same deterministic
initial centroids. IVF quality is reported against exact top-10, with mean
scanned-document count beside `nprobe`; `nprobe` itself is not treated as an
equal-work budget.

Environment: Python 3.11.9, NumPy 2.4.6, Faiss 1.16.0, one thread, CPU
`Intel64 Family 6 Model 63 Stepping 2, GenuineIntel`.

## Results

| Method | Train MSE | Training ms | `nprobe=1` recall@10 | `nprobe=2` recall@10 | Centroid digest |
| --- | ---: | ---: | ---: | ---: | --- |
| Faiss Lloyd | 8.116625 | 2.90 | 0.9512 / 621 scanned | 1.0000 / 1,190 scanned | `7ced7fa0…` |
| `NumpyEmeans` CPU | 8.110682 | 102.57 | 0.9590 / 621 scanned | 1.0000 / 1,193 scanned | `c256508e…` |

At `nprobe=4` and `nprobe=8`, both methods reached recall@10 `1.0`; the
mean scanned counts were approximately 2,423/4,096 for Lloyd and
2,427/4,096 for E-means. In this smoke, E-means has a marginally lower
training MSE and slightly higher one-probe recall, but its training time is
about 35 times higher. This is directional evidence only.

The complete raw output, receipt, and SHA-256 sidecar are retained under
`artifacts/r5-emeans-vs-lloyd/`. The receipt records `production_activation:
false` and the exact Faiss/NumPy/Python versions.

## Decision and limits

No production ANN runtime or default changes follow from this run. The result
is `DEFER`: E-means is a valid optional CPU research method, but the synthetic
smoke does not establish a quality or cost advantage on the real R4 corpus.
PR #638 remains the Faiss additive-fitting control; PR #641 remains the
feasibility and ownership record. Their historical receipts are not rewritten.

O-means (PR #5917) remains a separate optional stage and is not represented by
the E-means rows above. A real decision requires a frozen corpus, a declared
train/base/query split, equal work budgets, repeated receipts, and the same
exact-rerank path.

Reproduction:

```text
python -m venv .venv-r5-emeans
.venv-r5-emeans/Scripts/python -m pip install faiss-cpu==1.16.0 numpy==2.4.6
.venv-r5-emeans/Scripts/python tools/agent-memory-bench/run_emeans_vs_lloyd.py \
  --output emeans.raw.json --receipt emeans.receipt.json \
  --sidecar emeans.raw.sha256 --threads 1
```
