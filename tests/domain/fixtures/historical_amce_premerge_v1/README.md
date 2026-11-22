# Historical AMCE pre-merge v1 fixtures

These files preserve the closed experimental AMCE candidate from Draft PR #690
at `4ec7866f31e0d0b2e3d2ca1933b27c3e78f767ec`. They are not the normative
extensible-envelope vectors, are not compiled as current tests, and do not
promise compatibility with persisted records. That candidate was never an
integrated persisted contract.

The original eleven literal hex/SHA-256 vectors remain in the archived test;
the accompanying independent Python oracle reproduces those experimental bytes.
The current oracle and current codec test one directory above own qualification
of the corrected AMCE envelope v1.
