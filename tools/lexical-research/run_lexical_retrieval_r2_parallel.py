#!/usr/bin/env python3
"""Run two R2 harnesses concurrently and verify owned-directory cleanup."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path


class ParallelRunError(RuntimeError):
    """Raised when a child exits unsuccessfully or misses the deadline."""


def _reap_processes(processes: list[subprocess.Popen[bytes]], timeout: float) -> None:
    """Terminate and reap every child, including children left after a failure."""

    for process in processes:
        if process.poll() is None:
            process.terminate()
    deadline = time.monotonic() + timeout
    for process in processes:
        if process.poll() is not None:
            continue
        remaining = max(0.0, deadline - time.monotonic())
        try:
            process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            process.kill()
    for process in processes:
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:  # pragma: no cover - defensive
            raise ParallelRunError("failed to reap R2 child process") from exc


def _wait_for_processes(
    processes: list[subprocess.Popen[bytes]], timeout: float
) -> list[int]:
    """Wait with one shared deadline and always reap all children."""

    deadline = time.monotonic() + timeout
    try:
        while any(process.poll() is None for process in processes):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ParallelRunError("parallel R2 harness timed out")
            time.sleep(min(0.02, remaining))
        return [int(process.returncode) for process in processes]
    finally:
        _reap_processes(processes, timeout)


def _run_process_supervision_self_test() -> None:
    """Exercise unsuccessful and hung children without invoking the harness."""

    error_child = subprocess.Popen([sys.executable, "-c", "raise SystemExit(7)"])
    try:
        try:
            codes = _wait_for_processes([error_child], 1.0)
        except ParallelRunError:
            raise AssertionError("error child was not reaped")
        if codes != [7]:
            raise AssertionError(f"unexpected error-child status: {codes}")
    finally:
        _reap_processes([error_child], 1.0)

    hung_child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        try:
            _wait_for_processes([hung_child], 0.05)
        except ParallelRunError:
            pass
        else:
            raise AssertionError("hung child was not rejected")
        if hung_child.poll() is None:
            raise AssertionError("hung child survived timeout cleanup")
    finally:
        _reap_processes([hung_child], 1.0)

    print('{"status":"PASS","process_supervision":"negative_controls"}')


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("binary", type=Path, nargs="?")
    parser.add_argument("corpus", type=Path, nargs="?")
    parser.add_argument("queries", type=Path, nargs="?")
    parser.add_argument("parent", type=Path, nargs="?")
    args = parser.parse_args()
    if args.self_test:
        _run_process_supervision_self_test()
        return 0
    if any(value is None for value in (args.binary, args.corpus, args.queries, args.parent)):
        parser.error("binary, corpus, queries and parent are required unless --self-test is used")

    args.parent.mkdir(parents=True, exist_ok=True)
    sentinel = args.parent / "external-sentinel.txt"
    if sentinel.exists():
        if not sentinel.is_file():
            raise SystemExit("parallel R2 external sentinel is not a regular file")
        sentinel_contents = sentinel.read_bytes()
    else:
        try:
            with sentinel.open("xb") as stream:
                stream.write(b"must-survive\n")
            sentinel_contents = b"must-survive\n"
        except FileExistsError:
            sentinel_contents = sentinel.read_bytes()
    before = set(args.parent.iterdir())
    failure: BaseException | None = None
    try:
        with tempfile.TemporaryDirectory(prefix="r2-parallel-", dir=args.parent) as output_dir:
            outputs = [Path(output_dir) / "one.json", Path(output_dir) / "two.json"]
            processes: list[subprocess.Popen[bytes]] = []
            try:
                for output in outputs:
                    processes.append(subprocess.Popen([
                        str(args.binary), str(args.corpus), str(args.queries), str(output), str(args.parent / "requested-root")
                    ]))
                return_codes = _wait_for_processes(processes, 60.0)
            except OSError as exc:
                _reap_processes(processes, 5.0)
                raise ParallelRunError(f"failed to start parallel R2 harness: {exc}") from exc
            if return_codes != [0, 0]:
                raise ParallelRunError(f"parallel R2 harness failed: {return_codes}")
            for output in outputs:
                receipt = json.loads(output.read_text(encoding="utf-8"))
                if receipt.get("schema_version") != 2:
                    raise ParallelRunError("parallel R2 receipt schema mismatch")
    except BaseException as exc:
        failure = exc
    finally:
        if not sentinel.exists() or sentinel.read_bytes() != sentinel_contents:
            raise SystemExit("parallel R2 run modified external sentinel")
        after = set(args.parent.iterdir())
        if after != before:
            raise SystemExit("parallel R2 run leaked outside its owned directories")
    if failure is not None:
        raise failure
    print('{"status":"PASS","parallel_processes":2}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
