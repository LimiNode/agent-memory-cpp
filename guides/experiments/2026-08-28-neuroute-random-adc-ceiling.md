# NeuRoute random overcomplete ADC ceiling

Date: 2026-08-28. Frozen protocol and completed measurement.

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

## Results

The predeclared plateau was disproved for the frozen projection draws:

| Width | Mean nDCG loss vs FP32 | DE 25k | FR 25k | JA 25k | DE 1M |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 512 | .04564 | .03694 | .04164 | .03116 | .07281 |
| 768 | .02647 | .01450 | .01950 | .02590 | .04597 |
| 1024 | .02572 | .01435 | .01694 | .02044 | .05115 |
| 1536 | .02211 | .00644 | .03255 | .01547 | .03400 |
| 2048 | **.00381** | -.00862 | .01691 | .00108 | .00587 |
| 4096 | .00495 | -.01153 | .01508 | .01296 | .00327 |

The 1024-to-4096 improvement was `.02077`, far above the `.003` plateau
threshold. The best measured mean was 2048 bits; 4096 was slightly worse by
`.00114`, so the curve is not monotonic.

This does not make ADC2048 a production winner. The inherited projection
generator initializes each width from `seed + width`; widths are deterministic
but not nested prefixes of one matrix and are therefore different random
draws. The large 2048 jump can mix a true width effect with projection-seed
luck. In addition, its FR loss `.01691` remains well above the old `.0075`
per-dataset quality limit. The result reopens random overcomplete ADC as a
replication question, not as a native implementation task, while strengthening
the case for a learned ranking-aware code that should be less draw-sensitive.

## Evidence

```text
quality result SHA-256:       13d4d4dc3da2e12d7e175f0ab7171125a3ebf50f324b4f87d1e66e3a06295977
fail-closed evidence SHA-256: a333ad4fc7c916d261ca12539ad1bb06205baf84b4cc8472ce2c63a840f9d421
```

The evidence writer regenerated all 36 new dataset/seed/width rows and the
six-point parent-plus-diagnostic curve byte-for-byte, while retaining the
protocol's explicit prohibition on native or production selection.

## Limitations

The projection family, projection seed, threshold estimator, and two-centroid
ADC decoder remain fixed. The experiment can close this random family at the
tested seed; it cannot bound a trained encoder, a multi-bit quantizer, or a
ranking-aware student. Runtime and storage costs deliberately remain
unmeasured because even 4096 bits is only a ceiling diagnostic here.
