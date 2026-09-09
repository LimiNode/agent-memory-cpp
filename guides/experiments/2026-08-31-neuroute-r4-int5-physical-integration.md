# NeuRoute R4 nonlinear INT5 physical integration

## Context

- Date: 2026-08-31
- PR: stacked on the nonlinear representative-quantization frontier
- Status: full DE-1M materialization, warm-page-cache and fresh-process
  native cascade measurements, and evidence recomputation complete

## Question

Does the selected nonlinear INT5 power-.5 representative codec become a
production-like system improvement when it is served either from a separate
representative side-store or from a heterogeneous address-major store with
INT5 FF32 prefixes and uniform-INT8 remainders?

## Frozen protocol

The 16-bit partition, K8 top-1024 shortlist, FF32 IDs, learned R0 plus
normalized max-cosine scorer, .005 candidate boundary, and
Hamming768 -> ADC64 -> exact10 cascade are unchanged. The three physical
treatments are:

| Treatment | Active representative source | Complete physical footprint |
|---|---|---|
| homogeneous_int8 | Full address-major uniform-INT8 corpus | INT8 corpus |
| int5_side_store | Address-major nonlinear-INT5 FF32 side-store | INT8 corpus + side-store |
| int5_mixed | Nonlinear-INT5 FF32 prefix in each bucket | One mixed INT5/INT8 corpus |

The mixed store uses an external per-address byte-offset directory and the
frozen representative count as its codec boundary; it has no per-record tags
or duplicate documents. Side-store and mixed read byte-identical INT5 records.
The native kernel uses SIMDComp to unpack each 5-bit record and the exact
square inverse implied by power gamma=.5; no pow, LUT gather, or float
workspace is present in the measured hot path.

Warm-page-cache treatment uses one untimed pass and three measured passes over
all 76 internal queries for each of three route seeds. Fresh-process treatment
uses 15 preregistered paired requests per seed and leaves the shared OS page
cache uncontrolled. All treatments run the complete native cascade.

## Results

Side-store and mixed produced identical scorer and every downstream sequence
hash for all 2,052 warm samples and 135 fresh-process samples. The uniform
INT8 treatment replayed the frozen parent candidate counts and nDCG.

Warm-page-cache latency:

| Treatment | Total p50 / p95 / p99, ms | Representative dot p95, ms | Logical representative bytes p50 |
|---|---:|---:|---:|
| Homogeneous INT8 | **10.031 / 10.504 / 10.911** | **2.387** | 7.11 MB |
| INT5 side-store | 11.554 / 12.272 / 12.671 | 4.184 | 4.47 MB |
| INT5 mixed | 11.474 / 12.094 / 12.496 | 4.185 | 4.47 MB |

The mixed path reduces representative bytes touched by 37.11%, but its
unpack and inverse-transform arithmetic increase complete-cascade p95 by
15.14% and p99 by 14.53%. It therefore fails the preregistered 1.10x
warm p95/p99 latency-neutrality gates.

Physical footprint:

| Treatment | Mean active store | Mean complete footprint |
|---|---:|---:|
| Homogeneous INT8 | 388.0 MB | 388.0 MB |
| INT5 side-store | 216.88 MB | 604.88 MB |
| INT5 mixed | 260.01 MB | **260.01 MB** |

Mixed saves 32.99% of the complete corpus store. Side-store has the smallest
active mapping, but duplicates most corpus documents and grows the complete
footprint by 55.90%.

Final quality remains inside the frozen gates:

| Treatment | Mean internal nDCG@10 |
|---|---:|
| Homogeneous INT8 | .650652 |
| INT5 side-store | .649780 |
| INT5 mixed | .649780 |

The INT5 mean loss versus INT8 is .000872; per-seed losses are
0, .002689, and -.000074. The .002/.004 mean/every-seed gates pass.

Fresh-process native-query p95 is 25.685 ms for homogeneous INT8,
25.593 ms for side-store, and 26.697 ms for mixed. These samples show
substantially fewer page faults for INT5, but process setup and uncontrolled
shared page-cache state dominate; they are not cold-media evidence.

## Interpretation

The nonlinear INT5 codec is still the selected algorithmic representation,
and the mixed layout realizes its storage benefit without duplication. It is
not the selected default physical layout on this host: after vectorizing the
specialized square inverse, SIMDComp unpack remains expensive enough that the
fully warm homogeneous INT8 path is about 1.15x faster.

The physical-layout branch is therefore not closed. The lower INT5 fault count
and 33% smaller complete store license a separately frozen pressure and
concurrency experiment. That experiment must test whether the compact mixed
layout becomes useful when cache residency, multiple indexes, or worker count
make memory traffic more important than single-query unpack cost.

The current research selection remains homogeneous_int8. No production
selection or merge is licensed.

## Limitations

- Measurements are directional single-host Windows/AVX2 results.
- Mixed records are fixed-file benchmark records, not MDBX pages or
  transactions.
- Warm execution has the full working set available and does not model
  competing indexes.
- Fresh process does not reset the OS page cache and is not a cold-disk test.
- The result is specific to power-.5 INT5 and the frozen FF32/K32 router.

## Evidence

    materialization SHA-256: 120f960d9284108312d9797d8588a6ef86fbf26f3acdeebd063fc1702ab6ab32
    result SHA-256:          316ed5836e46a547c5e24b038916163f213a58119023ea555cb9e08f132fa76f
    evidence SHA-256:        0730694af14b614f49dd1a60935a51f4721cd44211eccccf62df833a94f1f633

The evidence writer rehashes the three mixed stores and every referenced
parent layout, recomputes warm, quality, fresh-process, and footprint
summaries, runs the native self-test, and independently replays one paired
fresh-process request across all treatments.
