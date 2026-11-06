#!/usr/bin/env python3
"""Validate the publication-stack disposition matrix as a research artifact."""

from __future__ import annotations

import argparse
from pathlib import Path


AUDIT = Path(__file__).resolve().parents[1] / "guides/experiments/2026-11-06-publication-stack-reuse-supersession.md"
REQUIRED_PR_MARKERS = ("#544", "#549", "#550", "#551", "#552", "#553", "#554", "#555")
REQUIRED_TERMS = (
    "coordinator owns the write transaction",
    "single-writer fencing/ownership",
    "atomic canonical/unit/projection/manifest publication",
    "lexical participation",
    "pinned reads",
    "target-level vs scope-wide guarantee",
    "independent file cas publication",
    "source/body digest and content-token binding",
    "crash recovery",
    "legacy writer admission and deployment quiescence",
)
DISPOSITIONS = (
    "REUSE AS-IS AFTER RESTACK",
    "REUSE SELECTIVELY",
    "SUPERSEDED",
    "REQUIRES REDESIGN",
    "RESEARCH-ONLY",
)


def validate(text: str) -> None:
    lowered = text.lower()
    for marker in REQUIRED_PR_MARKERS:
        if marker not in text:
            raise AssertionError(f"missing historical PR marker: {marker}")
    for term in REQUIRED_TERMS:
        if term not in lowered:
            raise AssertionError(f"missing required invariant: {term}")
    if "#555 | `REUSE SELECTIVELY" not in text:
        raise AssertionError("#555 must be updated and revalidated")
    if not any(disposition in text for disposition in DISPOSITIONS):
        raise AssertionError("missing disposition vocabulary")
    if "production activation" not in lowered or "blocked" not in lowered:
        raise AssertionError("audit must keep production activation blocked")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--audit", type=Path, default=AUDIT)
    args = parser.parse_args()
    text = args.audit.read_text(encoding="utf-8")
    validate(text)
    if args.self_test:
        try:
            validate(text.replace("#555", "#999"))
        except AssertionError:
            pass
        else:
            raise AssertionError("mutation must invalidate the audit")
    print(f"publication_stack_audit=PASS bytes={len(text.encode('utf-8'))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
