# NeuRoute full-corpus codec I/O

Date: 2026-08-28. Frozen protocol and completed measurement.

## Question

Does the INT5/SIMDComp winner from the pool-local microbenchmark remain useful
when all one million document records are physically stored and the frozen
top-64 document positions are fetched through real random file reads? INT6 is
the quality-safe parent control; scalar BP128 isolates the value of SIMDComp at
the same physical byte count.

## Physical layouts

The benchmark builds four fixed-record files from the same frozen normalized
DE-1M E5 vectors:

| Representation | Layout | Record bytes | Full file bytes |
| --- | --- | ---: | ---: |
| INT5 | SIMDComp BP128 | 244 | 244,000,000 |
| INT5 | scalar BP128 | 244 | 244,000,000 |
| INT6 | SIMDComp BP128 | 292 | 292,000,000 |
| INT6 | scalar BP128 | 292 | 292,000,000 |

Each record contains three independently packed 128-value blocks followed by
its little-endian float32 scale. Thus one top-64 request performs 64 seeks and
reads exactly 15,616 bytes for INT5 or 18,688 bytes for INT6. The checked-in
SIMDComp submodule stays a benchmark adapter; this protocol does not commit the
library to a durable production format.

Before timing, the native harness verifies every physical file hash and
replays the exact expected top-10 for all 76 queries and three router seeds.
The 912 checks cover four representations over the same 228 requests.

## Cache-state definitions

The warm-page-cache treatment sequentially reads the entire selected physical
file, runs two untimed request passes, then measures 15 deterministic shuffled
passes. Fetch, decode-and-dot, top-10 selection, total time, logical bytes,
random-read count, and available process page-fault counters are reported
separately.

The process-cold treatment starts a fresh executable for each of 31
predeclared requests per representation. Timing begins after small query,
pool, and rank metadata is loaded and the physical file is opened; it measures
the first top-64 fetch in that process. Child launch wall time is also reported.
The OS page cache is deliberately uncontrolled, so this treatment must never be
described as cold-disk or OS-cache-cold evidence. A true cold-device study would
need an isolated host or privileged cache-reset procedure.

## Results

All four full physical files reproduced every expected top-10 sequence for all
228 frozen requests. INT5 therefore retains the quality result established in
#207 while using 244 MB rather than INT6's 292 MB, a 48 MB (16.4%) reduction at
one million documents.

Building all 1.072 GB of physical outputs took 62.39 seconds end to end:
29.30 seconds to hash the 1.536 GB source, 12.99 seconds to quantize and pack,
and 20.08 seconds to hash the four stored files.

Sequentially prefaulted warm-page-cache results were:

| Representation | Fetch p50/p95 ms | Decode+dot p50/p95 ms | Total p50/p95 ms |
| --- | ---: | ---: | ---: |
| INT5 SIMDComp | .5550 / .6379 | .0434 / .0532 | **.6011 / .6854** |
| INT5 scalar | .5694 / .6890 | .0861 / .1007 | .6588 / .7832 |
| INT6 SIMDComp | .5599 / .6555 | .0434 / .0526 | .6055 / .7016 |
| INT6 scalar | .5654 / .6723 | .0851 / .1014 | .6543 / .7617 |

SIMDComp roughly halves decode-and-dot time relative to the byte-equivalent
scalar implementation. At end-to-end final-stage level the 64 random reads
dominate, so INT5 SIMDComp improves total p50 by 8.8% and p95 by 12.5% over
INT5 scalar. INT5 versus INT6 SIMDComp is nearly tied in warm latency, but INT5
keeps the 16.4% storage and logical-fetch-byte advantage.

Fresh-process first-fetch results, with the shared OS page cache uncontrolled,
were:

| Representation | Fetch p50/p95 ms | Decode+dot p50/p95 ms | Total p50/p95 ms |
| --- | ---: | ---: | ---: |
| INT5 SIMDComp | .6318 / .7465 | .0578 / .0693 | **.6941 / .8163** |
| INT5 scalar | .6464 / .7432 | .0829 / .1166 | .7445 / .8432 |
| INT6 SIMDComp | .6576 / .7679 | .0596 / .0765 | .7208 / .8352 |
| INT6 scalar | .6518 / .9325 | .0843 / .1097 | .7453 / 1.0288 |

The complete child-process wall time was about 109.4--110.1 ms p50, so process
startup dominates a truly fresh executable even though the first measured
top-64 storage operation remains below one millisecond. The Windows process
counter observed only 4--7 page faults per first fetch at p50; it does not
separate minor and major faults and is not physical-device I/O evidence.

The engineering conclusion is narrower than "INT5 makes random I/O cheap".
Fixed-record file fetch is already the bottleneck, and SIMDComp still earns a
meaningful final-stage gain without adding bytes. INT5 SIMDComp is the best
measured file-layout candidate, principally because it matches the best timing
while reducing storage and fetched bytes. Production selection remains
deferred until MDBX page layout, transactions, concurrency, and the surrounding
router/Hamming/ADC stages are included.

## Evidence

```text
input manifest SHA-256:   304a7d01fa2595f2864a8e43263088006dae34dd9dac8e27efb06aba52357bd9
storage manifest SHA-256: 4513e744847792625b827ca108cc1ccdfed8ee6044e15c187d0035cff9074538
warm report SHA-256:      82f59178e6ac3c0a1f39e11d35223ec10566741c532d5612521dd18d966de313
result SHA-256:           09da09a04df56c5414158d115733be72b658ee583726f3beeb21f961d65bd7a6
evidence SHA-256:         5ab610bf3e762a0c0596502208fc9f9a1489bc7b9f0fd1ba0b9ed778bea43fd0
```

The fail-closed evidence rehashed all 1.072 GB, replayed all 912 full quality
checks, and started fresh
processes to independently replay the deterministic identity/top-10 fields of
all 124 process-cold receipts.

## Expected result and decision boundary

SIMDComp should retain its decode advantage, while real random reads may make
the 48-byte-per-record difference between INT5 and INT6 more important than it
was in the pool-local benchmark. The result is an engineering frontier only:
quality must replay exactly, but final production selection remains deferred
until the MDBX record/page layout and workload concurrency are measured.

## Evidence contract

The input manifest binds the #207 quality/evidence/native artifacts, the
parent INT6 result, the final-representation materialization, full DE-1M vector
bytes, query vectors, document-id ranks, pools, and all expected top-10
sequences. The evidence writer revalidates every storage hash and all 912 warm
quality rows, then starts a new process to replay the deterministic identity
and top-10 receipt for all 124 process-cold samples. Timing values are not
required to reproduce byte-for-byte.
