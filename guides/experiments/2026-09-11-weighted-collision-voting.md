# 2026-09-11 weighted collision voting

On the packed width-8/radius-one frontier, candidate union was held fixed at
9,885 mean candidates over 152 queries.  Raw collision count, inverse posting
frequency, and probe rank-decay voting were compared with exact ordinal-L1
rerank.  Weighting reduced top-256 teacher survival from the union's `.271711`
to `.069737`, `.036842`, and `.038816`, respectively (all worst-query
survival `.0`).  Thus these simple accumulator weights do not recover useful
ordering and are negative for this schedule.

This is an oracle-only result: postings are in memory, byte counts are
candidate-generation diagnostics, and `production_activation: false`.
