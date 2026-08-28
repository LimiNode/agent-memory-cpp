# NeuRoute random overcomplete ADC ceiling

Date: 2026-08-28. Frozen protocol; measurements are intentionally absent.

## Question

Does the random Rademacher overcomplete ADC curve continue improving beyond
1024 bits, or was the observed 768-to-1024 plateau a local fluctuation?

The parent fixed-top64 result reported mean nDCG loss versus exact FP32 of
`.04564`, `.02647`, and `.02572` at 512, 768, and 1024 bits. This diagnostic
adds 1536, 2048, and 4096 bits under the identical projection seed,
document-sample threshold, conditional-mean centroids, frozen ADC256 top-64
pools, datasets, router seeds, and query partitions.

## Scope

This is quality-only. It does not add a native codec, storage layout, or
production candidate. Each width projects the same 384-dimensional E5 vectors
with a deterministic Rademacher matrix, calibrates bit thresholds and two
conditional means from the same deterministic sample of at most 100k
documents, then ranks only the already frozen 64-document pools.

The reported curve is:

```text
512 / 768 / 1024     frozen parent evidence
1536 / 2048 / 4096   new diagnostic rows
```

Mean loss is the unweighted mean of the DE-25k, FR-25k, JA-25k, and DE-1M
dataset mean losses. A gain of at most `.003` from 1024 to 4096 confirms the
predeclared practical plateau. A larger gain disproves that plateau but still
does not license a production implementation: random overcomplete measurements
remain distinct from the separately planned learned final reranker.

## Limitations

The projection family, projection seed, threshold estimator, and two-centroid
ADC decoder remain fixed. The experiment can close this random family at the
tested seed; it cannot bound a trained encoder, a multi-bit quantizer, or a
ranking-aware student. Runtime and storage costs deliberately remain
unmeasured because even 4096 bits is only a ceiling diagnostic here.
