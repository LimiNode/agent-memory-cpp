# External codec portability checks

Date: 2026-09-25  
Status: `BLOCKED` for official native quality/timing on this host; no product selection.

This note records environment checks for the two external families that cannot
be silently replaced by local imitations.

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
`torch`, `faiss`, and `omegaconf`, but no `hydra` package; no official QINCo2
fit was therefore started, and the old short pilot remains explicitly
undertrained smoke evidence only.

The next executable QINCo2 gate is: install the pinned upstream environment in
an isolated research workspace, adapt the canonical E5 train/database/query
files without changing vector order or metric, train first on a 25k control,
then repeat at 100k/250k/1M unsupervised pool sizes, and independently replay
decode, cosine top-10 and storage accounting. Until that is done, QINCo2 has
no source-bound quality claim in this repository.

