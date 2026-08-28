# NeuRoute full-corpus codec I/O

Date: 2026-08-28. Frozen protocol; measurement pending.

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
