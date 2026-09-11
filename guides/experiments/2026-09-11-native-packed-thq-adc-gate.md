# Native packed THQ-ADC gate (2026-09-11)

## Question

Can the strong exhaustive THQ4-384 locality result be measured with a native
scanner while comparing the 144 B/document thermometer payload with a derived
96 B/document packed ordinal payload and query-conditioned interval ADC?

## Harness

`tools/agent-memory-bench/native_packed_thq_adc_benchmark.cpp` is a C++17
benchmark target.  It consumes the existing native THQ manifest, derives the
2-bit ordinal payload from the stored three-threshold thermometer code, and
evaluates all available queries with deterministic document-id tie breaking.
It reports separate scan p50/p95 values and teacher survival@256 for:

* 144 B thermometer Hamming;
* 96 B packed ordinal L1;
* 96 B packed ordinal interval squared ADC.

The executable is deliberately an exhaustive validation harness.  It does not
build postings, skip pages, or activate a production route.

## Review status

The target and CMake wiring compile in the authoritative GitHub matrix.  A
local Windows configure was attempted, but this checkout could not remove a
stale FetchContent `nlohmann_json` directory; therefore no local timing number
is claimed here.  The DE-1M manifest and raw payloads are external artifacts,
consistent with the experiment archive policy.

## Interpretation boundary

This PR establishes the native measurement gate and parity checks needed before
selecting a codec.  Existing Python evidence remains the scientific result:
interval squared ADC reaches `1.0 @256` teacher survival, while corrected
progressive replay shows substantial algorithmic pruning.  Native throughput,
memory bandwidth, and page-skip benefit remain unmeasured until the external
DE-1M manifest is supplied to this executable.

`production_activation: false`.

## Next check

Run the executable on the frozen 1M/152-query manifest with repeated warm and
cold runs, then record bytes read, p50/p95/p99 latency, and exact top-k parity
before considering a vertical/transposed physical layout.
