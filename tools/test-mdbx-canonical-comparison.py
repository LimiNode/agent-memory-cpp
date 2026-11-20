#!/usr/bin/env python3
"""Exercise fail-closed launch orchestration without running a full benchmark."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "canonical_comparison_runner", Path(__file__).with_name("run-mdbx-canonical-comparison.py"))
assert SPEC is not None and SPEC.loader is not None
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)
HEAD = "1" * 40
TREE = "2" * 40


class LaunchControls(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.source = Path(self.directory.name) / "source"
        self.source.mkdir()
        self.executable = self.source / "benchmark.exe"
        self.executable.write_bytes(b"fixed executable fixture")
        self.raw = self.source / "raw.tsv"
        self.receipt = self.source / "receipt.json"
        self.head = HEAD
        self.dirty = False
        self.commands: list[list[str]] = []

    def git(self, command: list[str], **kwargs: object) -> str:
        self.assertEqual(command[:3], ["git", "-C", str(self.source)])
        self.assertTrue(kwargs["text"])
        if command[3:] == ["rev-parse", "HEAD"]:
            return self.head + "\n"
        if command[3:] == ["rev-parse", "HEAD:src"]:
            return TREE + "\n"
        self.assertEqual(command[3:6], ["status", "--porcelain", "--untracked-files=normal"])
        return " M src/changed.cpp\n" if self.dirty else ""

    def run_process(self, command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        self.commands.append(command)
        self.assertEqual(kwargs["cwd"], self.source)
        self.assertTrue(kwargs["check"])
        if len(self.commands) == 1:
            self.assertEqual(command[0], str(self.executable.resolve()))
            self.assertEqual(kwargs["timeout"], 7)
            self.raw.write_text("fixture observations\n", encoding="utf-8")
            self.receipt.write_text(json.dumps({
                "execution_status": "completed", "decision": "evidence_only",
                "source_revision": HEAD, "source_dirty": False,
            }), encoding="utf-8")
        else:
            self.assertEqual(Path(command[1]).name, "validate-mdbx-canonical-flat-benchmark.py")
            self.assertEqual(kwargs["timeout"], 30)
            launch = json.loads(self.receipt.read_text(encoding="utf-8"))["launch_provenance"]
            self.assertEqual(launch["source_commit"], HEAD)
            self.assertEqual(launch["source_tree"], TREE)
            self.assertTrue(launch["source_unchanged"] and launch["executable_unchanged"])
        return subprocess.CompletedProcess(command, 0)

    def invoke(self, effect=None, extra: list[str] | None = None) -> int:
        arguments = ["runner", "--executable", str(self.executable), "--raw", str(self.raw),
                     "--receipt", str(self.receipt), "--timeout", "7", *(extra or [])]
        with patch.object(RUNNER, "__file__", str(self.source / "tools" / "runner.py")), \
                patch.object(sys, "argv", arguments), \
                patch.object(RUNNER.subprocess, "check_output", side_effect=self.git), \
                patch.object(RUNNER.subprocess, "run", side_effect=effect or self.run_process), \
                contextlib.redirect_stderr(io.StringIO()):
            return RUNNER.main()

    def assert_failed(self, effect=None) -> None:
        self.assertEqual(self.invoke(effect), 1)
        receipt = json.loads(self.receipt.read_text(encoding="utf-8"))
        self.assertEqual(set(receipt), {"execution_status", "failure"})
        self.assertEqual(receipt["execution_status"], "FAIL")
        self.assertTrue(receipt["failure"])

    def test_success_records_checked_launch(self) -> None:
        self.assertEqual(self.invoke(), 0)
        self.assertEqual(len(self.commands), 2)
        self.assertEqual(json.loads(self.receipt.read_text())["execution_status"], "completed")

    def test_dirty_launch_does_not_execute(self) -> None:
        self.dirty = True
        self.assert_failed()
        self.assertFalse(self.commands)

    def test_stale_or_dirty_configure_stamp(self) -> None:
        for field, value in (("source_revision", "3" * 40), ("source_dirty", True)):
            with self.subTest(field=field):
                def stale(command, **kwargs):
                    result = self.run_process(command, **kwargs)
                    receipt = json.loads(self.receipt.read_text())
                    receipt[field] = value
                    self.receipt.write_text(json.dumps(receipt))
                    return result
                self.assert_failed(stale)
                self.assertEqual(len(self.commands), 1)
                self.raw.unlink(); self.receipt.unlink(); self.commands.clear()

    def test_source_or_executable_changes(self) -> None:
        for mutation in ("dirty", "head", "executable"):
            with self.subTest(mutation=mutation):
                def changed(command, **kwargs):
                    result = self.run_process(command, **kwargs)
                    if mutation == "dirty":
                        self.dirty = True
                    elif mutation == "head":
                        self.head = "4" * 40
                    else:
                        self.executable.write_bytes(b"changed executable fixture")
                    return result
                self.assert_failed(changed)
                self.assertEqual(len(self.commands), 1)
                self.raw.unlink(); self.receipt.unlink(); self.commands.clear()
                self.dirty = False; self.head = HEAD

    def test_worker_timeout_or_nonzero_exit(self) -> None:
        for timeout in (True, False):
            with self.subTest(timeout=timeout):
                def failed(command, **kwargs):
                    self.run_process(command, **kwargs)
                    if timeout:
                        raise subprocess.TimeoutExpired(command, kwargs["timeout"])
                    raise subprocess.CalledProcessError(17, command)
                self.assert_failed(failed)
                self.assertEqual(len(self.commands), 1)
                self.raw.unlink(); self.receipt.unlink(); self.commands.clear()

    def test_validator_failure_removes_completed_proof(self) -> None:
        def failed(command, **kwargs):
            result = self.run_process(command, **kwargs)
            if len(self.commands) == 2:
                raise subprocess.CalledProcessError(1, command)
            return result
        self.assert_failed(failed)
        self.assertEqual(len(self.commands), 2)

    def test_non_object_receipt_fails_closed(self) -> None:
        def malformed(command, **kwargs):
            result = self.run_process(command, **kwargs)
            self.receipt.write_text("[]\n", encoding="utf-8")
            return result
        self.assert_failed(malformed)
        self.assertEqual(len(self.commands), 1)

    def test_existing_raw_or_receipt_is_not_overwritten(self) -> None:
        for output in (self.raw, self.receipt):
            with self.subTest(output=output.name):
                output.write_text("previous artifact\n", encoding="utf-8")
                with self.assertRaises(SystemExit) as rejected:
                    self.invoke()
                self.assertEqual(rejected.exception.code, 2)
                self.assertEqual(output.read_text(), "previous artifact\n")
                self.assertFalse(self.commands)
                output.unlink()

    def test_output_paths_must_be_distinct(self) -> None:
        with self.assertRaises(SystemExit) as rejected:
            self.invoke(extra=["--receipt", str(self.raw)])
        self.assertEqual(rejected.exception.code, 2)
        self.assertFalse(self.commands)
        self.assertFalse(self.raw.exists() or self.receipt.exists())

    def test_recorded_repeat_count_is_bounded(self) -> None:
        for count in (4, 101):
            with self.subTest(count=count), self.assertRaises(SystemExit) as rejected:
                self.invoke(extra=["--repeats", str(count)])
            self.assertEqual(rejected.exception.code, 2)
        self.assertFalse(self.commands)


if __name__ == "__main__":
    unittest.main()
