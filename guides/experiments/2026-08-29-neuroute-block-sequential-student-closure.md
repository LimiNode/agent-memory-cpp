# NeuRoute block-sequential student activation closure

Date: 2026-08-29. Frozen implementation `0686ac4`; audit complete.

## Question

The independently frozen #225 diagnostic made block-sequential student training
conditional on state-dependent cascade greedy improving the privileged static
target-gain-density frontier. Did that gate pass?

## Parent result

#225 found enormous cheap-routing headroom versus learned static schedulers, but
the headroom was already captured by a privileged static `target gain / posting
count` order. Sequential cascade greedy changed candidate mass by only -0.30%
to +0.29% across the nine scale/seed checks while evaluating about five times
more actions and cascade states.

Its frozen decision is:

```text
sequential_teacher_headroom_supported = false
student_followup_licensed = false
production_selection_licensed = false
```

## Closure

The audit binds the exact #225 result and evidence bytes, requires the complete
54-row/76-query matrix and exact negative decision, and emits no student model
or measurement. The German internal-evaluation partition remains unopened.

The dormant protocol retains direct and centroid initializations, teacher
forcing, 16/32/64-address blocks, deployable state only, configuration-only
selection, separate internal evaluation, and scheduler/cascade runtime metrics.
No native implementation or production selection is licensed.

## Result

The audit passed twice with identical bytes:

```text
closure SHA-256: 5af661a3a96baf8aa1a0da7e2ec1a6540fc11b77f6baf0ba8ebc9955ffbe12ed
```

It bound #225 result `d8478ed65569...` and evidence `6857b3bbcab0...`,
confirmed all 54 rows and the negative gate, and emitted zero models and zero
student measurement rows. The internal-evaluation partition was not opened.
