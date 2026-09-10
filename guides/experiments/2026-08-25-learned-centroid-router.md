# Routing-aware learned centroid router

Date: 2026-08-25. Canonical status: **PROTOCOL-ONLY**.

## Question and planned protocol

This calibration-only follow-up was preregistered because strict and explicitly
overcomplete unsupervised ITQ codes remained materially below the predeclared
selective centroid-coverage gate. The intended experiment would train a shared
binary projection only on the separate Spanish train-query bundle and frozen
float semantic-IVF centroids. Teacher positives would be each query's exact
float top-16 centroids; hard negatives would be current binary top-64 centroids
outside that teacher set.

The objective is a routing margin, not vector reconstruction: it increases the
binary similarity of teacher-positive centroids over binary-ranked distractors.
Spanish dev queries/qrels remain unavailable during fitting and selection.

## Evidence status and limitations

This PR contains a preregistration contract only. It does not contain a runner,
trained projection, measured result, or independent evidence receipt. It
therefore makes no claim about learned-router quality, latency, candidate mass,
or product suitability. In particular, the title's historical “calibration”
wording must not be read as evidence that calibration was executed.

## Completion gate

A measured continuation must add the training implementation, deterministic
artifact hashes, pre/post ITQ controls, rank-weighted teacher recall, iterative
hard-negative re-mining, and a held-out evaluation receipt. Confirmation and
production selection remain forbidden until those artifacts exist.
