# NeuRoute R4 INT5 layout stress

## Context

- Date: 2026-08-31
- PR: stacked on the nonlinear INT5 physical-integration benchmark
- Status: long-trace resident/concurrency and hard working-set-cap matrix
  complete; evidence recomputation complete

## Question

Does the 33% smaller mixed nonlinear-INT5 store provide serving value when
the process working set cannot retain the homogeneous INT8 address-major
store, even though INT8 is faster in the fully resident single-query case?

## Protocol

The two treatments are the byte-identical physical parents from the preceding
study: homogeneous address-major INT8 and mixed nonlinear-INT5 prefixes with
INT8 remainders. Routing, scorer, candidate boundary, Hamming, ADC, and exact
E5 stages remain frozen.

Each native batch repeats the 76-query internal trace four times, for 304
queries. The matrix contains:

- three route seeds;
- resident and hard process-working-set-cap conditions;
- 1, 2, 4, 8, and 16 workers;
- one untimed warmup batch and two measured batches;
- 36,480 measured complete-cascade query executions.

The final pressure treatment applies a Windows hard 256 MiB process
working-set maximum and calls EmptyWorkingSet before warmup. An initial
384 MiB implementation calibration was discarded because the observed
resident INT8 working set was only about 312--326 MiB, so that cap did not
bind. No 384 MiB outcomes enter the confirmatory result or evidence.

The locked pressure decision uses eight workers and requires mixed INT5 to
have no worse p95, no lower throughput, and at most 80% of INT8 page faults.

## Results

All six seed/treatment result hashes are identical across resident/capped
conditions, every worker count, and both measured passes. Pressure and
concurrency therefore change timing and residency, not retrieval results.

Resident results:

| Workers | INT8 p95, ms | INT5 p95, ms | INT8 QPS | INT5 QPS |
|---:|---:|---:|---:|---:|
| 1 | **10.718** | 12.466 | **99.6** | 85.6 |
| 2 | **10.853** | 12.719 | **194.8** | 168.4 |
| 4 | **11.342** | 12.974 | **378.7** | 330.4 |
| 8 | **12.501** | 13.888 | **718.0** | 636.2 |
| 16 | **17.215** | 20.345 | **1139.6** | 1038.9 |

Homogeneous INT8 remains the resident winner throughout the concurrency
ladder.

Under the hard 256 MiB working-set cap:

| Workers | INT8 p95, ms | INT5 p95, ms | INT8 QPS | INT5 QPS | INT8 / INT5 faults per query |
|---:|---:|---:|---:|---:|---:|
| 1 | 14.968 | **12.465** | 81.0 | **85.4** | 421.5 / **31.2** |
| 2 | 16.180 | **12.642** | 153.8 | **167.5** | 599.7 / **187.8** |
| 4 | 21.869 | **12.979** | 268.3 | **331.3** | 658.5 / **189.7** |
| 8 | 39.995 | **14.029** | 369.8 | **642.2** | 714.4 / **186.3** |
| 16 | 98.085 | **19.824** | 320.6 | **1029.2** | 715.6 / **176.0** |

At the locked eight-worker headline:

- mixed p95 ratio versus INT8: .3508;
- mixed throughput ratio versus INT8: 1.7366;
- mixed page-fault ratio versus INT8: .2608.

All three pressure gates pass. Median ending working set is about 253.6 MiB
for INT8 and 238.9 MiB for mixed at eight workers. The cap forces repeated
faulting for the larger store while mixed remains close to its natural
resident footprint.

## Interpretation

The layout decision is workload-dependent rather than universal:

- with enough resident memory, homogeneous INT8 is 10--16% faster because it
  avoids 5-bit unpack;
- with a 256 MiB process budget, mixed INT5 is decisively faster and scales
  much better because its 260 MB complete store nearly fits while the 388 MB
  INT8 store thrashes.

Mixed INT5 is therefore selected for memory-constrained deployments under the
frozen pressure contract. Homogeneous INT8 remains selected for unconstrained
fully resident serving. This is a conditional research selection, not a
production merge authorization.

## Limitations

- The hard working-set API and measurement are Windows-specific.
- The 256 MiB boundary is a single capacity point, not a complete memory
  budget curve.
- Files are fixed benchmark stores rather than MDBX pages/transactions.
- The host has 128 GB physical RAM; the test constrains only the benchmark
  process working set, not system-wide memory.
- Timing is directional evidence from one AVX2 host.

## Evidence

    materialization SHA-256: b29993f929fcf29f2cb1470a3dbe202409abd44ad039262161adec8d8fcef0e3
    result SHA-256:          2c7f5de97f83619b0a86e28aa7dc9f196f5ed1e74bffaf91f082a7c84de570bc
    evidence SHA-256:        f789b675e4d9eea41c736fc0584e3bc66f9030362d38be180753a90b64f6852b

The evidence writer rehashes all 60 native reports, recomputes every summary
and cross-condition result identity, runs the native self-test, and replays
one independent capped eight-worker mixed batch.
