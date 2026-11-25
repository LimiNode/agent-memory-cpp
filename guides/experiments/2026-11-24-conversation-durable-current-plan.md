# Durable Conversation Current: predeclared decision screen

- Date: 2026-11-24. Status: **planned; NOT RUN**.
- Owner: conversation storage. Context: P4-0 documentation-only, based on
  `main@77a278bef338074227efa05365e83e967334f429`.
- Contract: [physical proposal](../conversation-durable-physical-contract.md),
  [reference semantics](../conversation-reference-store.md).
- Earlier [in-memory pagination screen](2026-11-24-conversation-current-pagination.md)
  is evidence of repeated-fold amplification, not a durable serving result.

## Question and hypothesis

Can a bounded full fold meet the selected durable serving envelope, or must
P4-4 separately propose a versioned Current projection/checkpoint? Hypothesis:
full fold is the smallest conformant baseline but its repeated input decoding
will exceed the long-history pagination budget. This is a testable expectation,
not a selected production design or authorization to add a DBI.

## Fixed semantic and measurement setup

Compare full fold, materialized Current and checkpoints at 128/1024 commits
under identical canonical AMCE, source admission order, retained frontiers,
host authorization and **synchronous durable commit**. No comparison that
weakens sync, omits old frontiers or drops ambiguity is same-contract evidence.
Reference oracle is unchanged in-memory Conversation Store. Require exact
semantic parity at every tested prefix/frontier before publishing timings.

Workloads: 128, 2048 and 32768 message roots; linear chains of 1/8/64 source
changes, four sibling branches per selected root, 10% tombstone/restore roots,
20% snapshot roots, 10% late tool results. Include representation corrections
of old occurrences at generations before/between/after output pages and
unresolved results followed by late calls. Separate regimes rather than
multiply all worst cases into one inadmissible store. Use 65536 total Entry,
1024 revisions/Entry and 256 MiB canonical/map ceilings for the extended
screen; these are fixture quotas for reference-oracle feasibility, NOT supported
durable totals or implementation ceilings. Add a durable-only streaming regime
beyond 65536 rows / 256 MiB with bounded working memory; validate selected pages
against independently partitioned oracle fixtures where a whole reference store
cannot admit the history. Record rejected workloads, not fast successful reads.
Pin authored data/seed/manifest SHA before measurement. Test 1/32/128-item pages,
latest and 25%/50%/75%/100% frontiers, retained exact targets and policy refusal.

Record hardware/RAM/device/filesystem, OS, compiler/version/flags, dependency
SHAs, map geometry, flags, profile quotas, frame-size distribution and complete
command/config/seed. Keep fixture construction, admission, checkpoint build,
reopen and query timing separate. Use ten warmups and at least 100 timed full
pagination sequences per regime; report median/p95/p99 per page and sequence.
Use at least ten fresh-process reopen runs. Distinguish warm process, cold
process with warm OS cache and genuinely controlled cold-cache runs. If cache
control is unavailable mark cold-cache NOT RUN, not cold by implication.

Collect complete frame decodes/bytes, raw rows/bytes inspected, allocations,
process peak RSS, transaction duration, persisted bytes, map/file growth,
bytes written and write amplification per accepted observation/correction.
RSS high-water comparisons use independent processes. MDBX reused pages and
allocated-map size are separate from live logical bytes. Report refused reads
and zero/output/input-budget boundaries as well as successful latency.

## Decision criteria fixed before implementation

These are proposed screen thresholds, **not current product SLAs**:

- Zero semantic mismatches at all declared frontiers. Any mismatch disqualifies
  that strategy regardless of timing.
- Default read limits remain 64 items / 1 MiB output / 4096 complete-frame
  visits / 32 MiB decoded. Hard caps remain 65536 items / 262144 visits /
  256 MiB per byte budget. Internal raw scan default 32 MiB, cap 256 MiB and
  262144 rows. Zero never means unlimited. A 32768-root regime may require
  explicit extended budgets; never silently raise defaults.
- At 2048 roots / page 32: warm-page p95 <=20 ms, p99 <=50 ms;
  fresh-process first-page p95 <=100 ms; all thresholds reported on named
  hardware, not generalized to arbitrary devices.
- At 32768 roots / page 32: repeated page after first must inspect <=4096
  rows, decode <=32 MiB and have warm p95 <=50 ms. Incremental working-set
  peak above mapped file + input fixture must be <=64 MiB. Failure means no
  long-history serving claim for that strategy.
- Projection/checkpoint candidate must reduce full-pagination p95 by at least
  2x versus conformant fold in the same accepted regime, with <=2x persisted
  live bytes and <=3x written bytes per imported/corrected occurrence. Also
  report synchronous write p95; >25% regression needs an explicit owner
  trade-off, not silent adoption.

If no candidate passes, retain a bounded baseline and explicit BudgetExhausted
outcomes, report limits and propose the smallest next experiment. Do not infer
that a cache or production schema migration is mandatory from one failing run.
Any change to thresholds/workload after observing results must be dated and
reported as a new screen, not silently retuned acceptance.

## Results, limitations and next check

Actual durable measurements: **NOT RUN**. No durable adapter, checkpoints,
Current DBI or benchmark implementation is introduced in P4-0. The thresholds
are reviewable intent only. Limits include synthetic representativeness,
hardware dependence, cache controls, mapped-file RSS attribution and profile
capacity. First check after P4-1/P4-3 qualification: run the conformant fold on
the frozen 2048-root/page-32 workload with counters; use that observation to
decide whether separate projection/checkpoint prototypes are justified.
