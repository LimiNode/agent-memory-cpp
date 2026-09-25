# External codec portability checks

Date: 2026-09-25  
Status: `BLOCKED` for official native quality/timing on this host; no product selection.

This note records environment checks for the two external families that cannot
be silently replaced by local imitations. The QINCo2 control is now running in
the isolated research workspace below; no quality number is promoted until its
checkpoint has been decoded and independently replayed.

## SAQ

Source: `howarlii/SAQ`, pinned snapshot `2163ebcedd0ad9c9f4de326e6ca7a860f9eafe52`,
Apache-2.0. The upstream README explicitly requires AVX-512. A clean CMake
configure was attempted from the pinned checkout with the repository's default
Release settings. MSVC reported all required AVX-512 feature tests as failed,
and the upstream configure stopped with:

```text
AVX-512 support requested but not available
```

Therefore no SAQ quality or native timing number is claimed. The SAQ gate is
not a negative algorithm result: it remains pending either an AVX-512 host or
an upstream-supported portable/AVX2 implementation whose semantics have been
validated against the official path. The current machine snapshot is recorded
in the LSQ/RaBitQ provenance helper (`Intel(R) Xeon(R) CPU E5-2696 v3 @ 2.30GHz`,
18 physical / 36 logical cores, Windows, no AVX-512).

## QINCo2

Source: `facebookresearch/QINCo`, pinned snapshot
`5a324954d5c9b3700d4407d6cc24c3db6e52890e`, CC-BY-NC-4.0. It is a
research-only external control and must not be presented as a product
dependency. The official pipeline requires its Hydra entry point and the
QINCo2 training configuration (M=16, K=256, official preselection/beam and
approximately 60 epochs). The current shared Python environment has
`torch`, `faiss`, and `omegaconf`, but no `hydra` package. An isolated runtime
now supplies the missing Hydra packages. A bounded official QINCo2-S control
has been run on the canonical 25k training subset (`L=2`, `M=16`, `K=256`,
`A=16`, `B=32`, CPU) in
`E:\\_repoz\\agent-memory-workspaces\\qinco2-canonical-25k-v1`. The upstream
scheduler completed three short epochs before the best checkpoint was frozen.
This remains an execution/provenance milestone only: it is deliberately
labelled `BOUNDED_UNDERTRAINED_CONTROL` and cannot support a family-level
conclusion.
The earlier 16-byte local pilot remains separate and is not silently replaced
by this run.

The remaining QINCo2 gates are stronger training on larger unsupervised pools
(100k/250k/1M), followed by the same independent decode, cosine top-10, and
storage audit. The official-model persisted-code audit already passes for all
152 queries: mean nDCG@10 is `0.616420` at 112 B/doc (16-byte QINCo2 code plus
the 96-byte THQ shell), with p05/worst-query nDCG equal to zero. No production
selection claim is made.
