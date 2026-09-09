# NeuRoute storage-format and execution separation

## Question

Can the selected NeuRoute record bytes remain stable across portable, SSE2,
and AVX2 execution while the default build is safe on CPUs that do not support
AVX2?

## Contract

Storage selection is explicit user configuration:

- `int8`: 384 biased signed bytes followed by one little-endian IEEE-754
  binary32 scale, 388 bytes per record;
- `nonlinear_int5_power_half`: three 128-value little-endian BP5 blocks
  followed by one little-endian IEEE-754 binary32 amplitude, 244 bytes per
  record. Reconstruction is the signed square implied by power-0.5
  companding.

Exactly one representation store is materialized for a new index. An existing
index is always opened according to its manifest fields: format version,
dimensions, codec, record width, code layout, scale layout, and compander.
Build defaults never reinterpret existing bytes.

Execution is a separate axis: `portable`, `sse2`, or `avx2`. The public
benchmark-owned codec contract dispatches only to a compiled kernel supported
by the current CPU. No broad core retrieval API is introduced by this research
PR.

## Build policy

`AGENT_MEMORY_NEUROUTE_ENABLE_AVX2` defaults to `OFF`. The safe build contains
the portable implementation and, on x86, an isolated SSE2 translation unit.
The AVX2 decoder is a separate translation unit compiled only when the option
is enabled. The historical R4 benchmark target also stops receiving global
AVX2 compiler flags in the default configuration.

The older 244-byte routing repack with a 256-value AVX2 block and a 128-value
SSE tail remains historical kernel-frontier evidence. It is not the production
persisted format. Optimized production execution must consume the canonical
three-block stream directly.

## Compatibility result

The portable packer first reproduced SIMDComp BP128 bytes exactly for a
deterministic synthetic record. Safe and opt-in builds then independently
validated the retained 1,000,000-record physical store with SHA-256
`a49da89c1d79815af718fb3a41d8d2fb3e9644e98f48ac5b4323cf561b5bbbbb`.

Each build decoded the same 65,536 deterministic records. Portable, SSE2, and
AVX2 produced the identical decoded-code digest:

`3e116fe2a5f62ae954ea08cf05a6ac661c2c28be2d01b4d965ef48fa8cdde99b`.

The safe report exposed only portable and SSE2. The opt-in report exposed
portable, SSE2, and AVX2 after a successful runtime capability check. Both
reported one configured store.

## Decision

The canonical record bytes are execution-independent, the default build is
AVX2-free, and AVX2 is an explicit CMake opt-in with runtime dispatch gating.
INT8 versus nonlinear INT5 power 0.5 remains a user choice; no automatic
memory-based selector is introduced.

Raw safe/AVX2 reports, the compact result, and replay evidence are under
`tmp/neuroute-storage-execution-separation/` in the experiment worktree and
are not committed.
