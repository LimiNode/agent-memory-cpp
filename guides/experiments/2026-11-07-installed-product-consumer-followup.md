# Installed MDBX/Raw CAS consumer follow-up

This follow-up extends the installed C++17 consumer smoke from PR #599. It
continues to exercise only public package APIs and remains an integration
qualification, not a release claim for retrieval or hydration.

The executable now verifies:

- successful full-body materialization when the configured budget is sufficient;
- rejection of an over-budget full-body read while a bounded range remains readable;
- two bindings attached to one source revision;
- two source revisions reusing one SHA-256 artifact identity through CAS
  deduplication;
- binary image bytes surviving publication and catalog reopen;
- digest verification rejecting a tampered image body with zero returned bytes.

The test still does not claim source-authorized `Hydrated` retrieval, Zstd
support, or cross-domain atomicity. Those capabilities require their own
approved public APIs and executable gates.
