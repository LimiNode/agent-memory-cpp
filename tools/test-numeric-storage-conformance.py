#!/usr/bin/env python3
"""Reject unrelated native failures while admitting prefixed MDBX logging."""
import importlib.util
from pathlib import Path
import unittest


SPEC = importlib.util.spec_from_file_location(
    "numeric_conformance", Path(__file__).with_name("run-numeric-storage-conformance.py"))
assert SPEC is not None and SPEC.loader is not None
DRIVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DRIVER)


class FailureDiagnostic(unittest.TestCase):
    def test_expected_check_is_required(self):
        with self.assertRaises(RuntimeError):
            DRIVER.require_failure_diagnostic("FAIL: unrelated failure\n", "")

    def test_fixture_failure_with_optional_mdbx_logging(self):
        for stderr in ("FAIL: canonical flat block row missing\n",
                       "dxb_set_readahead:20195 readahead ON 0..80\n"
                       "FAIL: canonical flat block row missing\n",
                       "diagnostic\r\nFAIL: canonical flat block row missing\r\n"):
            with self.subTest(stderr=stderr):
                DRIVER.require_failure_diagnostic(stderr, "missing")

    def test_unrelated_missing_or_ambiguous_failure_is_rejected(self):
        for stderr in ("", "dxb_set_readahead: readahead ON\n", "FAIL: \n",
                       "prefix FAIL: canonical flat block row missing\n",
                       "missing\nFAIL: unrelated failure\n",
                       "FAIL: canonical flat block row missing\nFAIL: another failure\n"):
            with self.subTest(stderr=stderr):
                with self.assertRaises(RuntimeError):
                    DRIVER.require_failure_diagnostic(stderr, "missing")


if __name__ == "__main__":
    unittest.main()
