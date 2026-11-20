#!/usr/bin/env python3
"""Run the opt-in flat Markdown consumer in independently bounded processes."""

import argparse
import pathlib
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", required=True, type=pathlib.Path)
    parser.add_argument("--fixture", required=True, type=pathlib.Path)
    parser.add_argument("--artifacts", required=True, type=pathlib.Path)
    args = parser.parse_args()
    args.artifacts.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="consumer-", dir=args.artifacts) as temporary:
        root = pathlib.Path(temporary)
        for mode in ("create", "reopen", "corrupt", "verify-corrupt"):
            result = subprocess.run(
                [str(args.executable.resolve()), mode, str(root / "consumer.mdbx"),
                 str(root / "cas"), str(args.fixture.resolve())],
                capture_output=True, text=True, timeout=25, check=False,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"{mode} failed ({result.returncode}):\n{result.stdout}\n{result.stderr}"
                )
            if result.stdout.strip() != "PASS " + mode:
                raise RuntimeError(f"unexpected {mode} observation: {result.stdout!r}")
            print(result.stdout.strip())


if __name__ == "__main__":
    main()
