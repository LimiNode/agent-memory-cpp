# E-means/O-means feasibility review

## Decision boundary

This note is a feasibility review only. It does not import Faiss code, add a
CUDA dependency, create an IVF class, or change an ANN/default retrieval
contract in `agent-memory-cpp`.

The reviewed upstream proposal is Faiss PR #5917, `Add o-means to contrib`,
currently open at head `9ecbf0c2a2f4ca7a771b552badc145584b09e1a6` against Faiss
base `65a4e3880ece1494c9d79e06f886c89810a1f85a`. The repository reports the MIT
license. The proposal describes GPU mini-batch k-means for large `k`, retaining
the E-means update schedule and using a two-level nearest-cell assignment.
These identities are recorded so a later replay can pin the exact source;
they are not a dependency approval.

## Current repository fit

The repository has exact vector and binary candidate controls and historical
prototype-IVF evidence, but it has no production IVF/HNSW implementation or
native centroid-training contract. The existing timeline therefore supports a
bounded prototype study, not a production choice.

The local machine exposes a CMP 50HX (20,480 MiB) and a GeForce GTX 1060
(3,072 MiB), with CUDA 13.3 available. The installed Faiss Python package is
1.15.0 and does not expose the proposed `OClustering` API. Consequently no
E-means/O-means measurement was executed in this stage: the reviewed PR is not
the installed package, and no matched corpus or native C++ serving path was
available. Status: `NOT_EXECUTED`.

## Required experiment before implementation

Use the same frozen, query-disjoint vector corpus and deterministic seed for
all arms. For each `k` in 1,024, 4,096, 16,384, and 65,536, record:

* training wall time, peak memory, epochs, batch law, and revival/empty-cluster
  counts;
* held-out assignment error and centroid norm/occupancy distributions;
* IVF candidate recall before and after exact rerank at a declared probe
  budget;
* native serving time split into centroid routing, list reads, scoring,
  rerank, and output materialization;
* retraining and snapshot compatibility cost.

The controls must include ordinary spherical/Euclidean k-means with the same
initialisation budget, a fixed-centroid replay, and the current exact scan.
Training and serving are separate measurements. A GPU result is marked
`OFFLINE_ONLY` until the trained centroid artifact can be consumed by a
portable native C++ serving experiment.

## Acceptance gates

The next research PR may implement only an offline harness after all of these
are frozen:

1. source commit and upstream license/provenance receipt;
2. corpus and query split digest, seed, dimension, and vector normalization;
3. exact oracle and mutation controls for centroid assignments and top-k;
4. explicit memory budget for both 3 GiB and 20 GiB device classes;
5. held-out recall/quality and empty-cluster thresholds;
6. separate training, index-build, lookup, exact-rerank, and retraining timing;
7. a portable artifact format and a native C++ replay plan.

Until those gates produce a representative result, the disposition is
`DEFER`: E-means/O-means is not a production ANN recommendation and must not
be compared with lexical or quantization methods by one undifferentiated
speed number.
