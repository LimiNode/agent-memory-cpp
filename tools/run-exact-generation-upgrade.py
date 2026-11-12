#!/usr/bin/env python3
"""Build two exact revisions and qualify a real installed OLD -> NEW MDBX upgrade.

No existing database/output is removed. Use a fresh artifact directory each run.
Build roots should be under the canonical checkout's .build/, not a worktree.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path

OLD_COMMIT = "52004324bc0d2b07c3657245195db349dd391774"


def git(source: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(source), *args], text=True).strip()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-source", required=True, type=Path)
    parser.add_argument("--new-source", required=True, type=Path)
    parser.add_argument("--new-commit", required=True)
    parser.add_argument("--build-root", required=True, type=Path)
    parser.add_argument("--artifacts", required=True, type=Path)
    parser.add_argument("--mingw", action="store_true", help="Use installed GCC/Ninja on Windows")
    args = parser.parse_args()
    old, new = args.old_source.resolve(), args.new_source.resolve()
    for source, expected in ((old, OLD_COMMIT), (new, args.new_commit)):
        if git(source, "rev-parse", "HEAD") != expected:
            raise RuntimeError(f"revision mismatch: {source}")
        if git(source, "status", "--porcelain", "--untracked-files=normal"):
            raise RuntimeError(f"source must be clean for exact-revision proof: {source}")
    if git(old, "ls-tree", "HEAD", "external") != git(new, "ls-tree", "HEAD", "external"):
        raise RuntimeError("dependency pins differ; this receipt requires an explicit dependency migration audit")
    root, artifacts = args.build_root.resolve(), args.artifacts.resolve()
    artifacts.mkdir(parents=True, exist_ok=True)
    db, cas, snapshot = artifacts / "knowledge.mdbx", artifacts / "cas", artifacts / "canonical.snapshot"
    if db.exists() or snapshot.exists():
        raise RuntimeError("artifact database already exists; use a fresh artifact directory")
    root.mkdir(parents=True, exist_ok=True)
    flags = ["-DCMAKE_CXX_FLAGS=/EHsc /Zc:__cplusplus /DNOMINMAX /MP4"] if os.name == "nt" else []
    if args.mingw:
        if os.name != "nt":
            raise RuntimeError("--mingw requires Windows")
        flags = ["-G", "Ninja", "-DCMAKE_C_COMPILER=gcc", "-DCMAKE_CXX_COMPILER=g++", "-DCMAKE_CXX_FLAGS=-DNOMINMAX"]
    receipt = {
        "schema": "agent_memory.exact_generation_upgrade.v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "old_commit": OLD_COMMIT, "new_commit": args.new_commit,
        "old_src_tree": git(old, "rev-parse", "HEAD:src"),
        "new_src_tree": git(new, "rev-parse", "HEAD:src"),
        "dependencies_tree": git(new, "rev-parse", "HEAD:external"),
        "platform": platform.platform(),
        "cmake": subprocess.check_output(["cmake", "--version"], text=True).splitlines()[0],
        "options": {"cxx_standard": 17, "mdbx": True, "json": False, "config": "Release", "extra_flags": flags},
        "canonical_format": "existing knowledge_unit.v1 / projection.v1; all preexisting DBI rows and CAS hashed",
        "derived_format": "exact_generation schema=1, completion=1, fp32-le-v1",
        "recipe": "SHA256(exact canonical primary_text UTF-8), first two unsigned bytes -> FP32",
        "purpose": "deterministic identity/lifecycle fixture, NOT semantic embeddings or ANN performance",
        "processes": [], "commands": [],
    }
    with (artifacts / "upgrade.log").open("w", encoding="utf-8") as log:
        def run(command: list[str], expected: int = 0, capture: bool = False) -> str:
            # CMake embeds -D paths in generated install scripts; Windows
            # backslashes there become escapes (for example \a). Use CMake paths.
            command = [str(x).replace("\\", "/") for x in command]
            receipt["commands"].append(command)
            print("RUN", subprocess.list2cmdline(command), flush=True)
            log.write("\n$ " + subprocess.list2cmdline(command) + "\n"); log.flush()
            result = subprocess.run(command, stdout=subprocess.PIPE if capture else log,
                                    stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
            output = result.stdout or ""
            if capture:
                log.write(output); print(output, end="", flush=True)
            log.flush()
            if result.returncode != expected:
                raise RuntimeError(f"exit {result.returncode}, expected {expected}; inspect {log.name}")
            return output

        def configure(source: Path, build: Path, options: list[str]) -> None:
            run(["cmake", "-S", source, "-B", build, "-DCMAKE_BUILD_TYPE=Release", *flags, *options])

        def build_install(build: Path, prefix: Path, target: str | None = None) -> None:
            run(["cmake", "--build", build, "--config", "Release", "--parallel", "4", *(["--target", target] if target else [])])
            run(["cmake", "--install", build, "--config", "Release", "--prefix", prefix])

        dep_prefix, containers_prefix = root / "provider-prefix", root / "containers-prefix"
        configure(new / "packaging/test-dependencies/mdbx-provider", root / "provider",
                  [f"-DMDBX_SOURCE_DIR={new / 'external/libmdbx'}"])
        build_install(root / "provider", dep_prefix, "mdbx-static")
        configure(new / "external/mdbx-containers", root / "containers",
                  [f"-DCMAKE_PREFIX_PATH={dep_prefix}", "-DMDBXC_DEPS_MODE=SYSTEM", "-DMDBXC_BUILD_TESTS=OFF",
                   "-DMDBXC_BUILD_EXAMPLES=OFF", "-DMDBXC_BUILD_BENCHMARKS=OFF", "-DMDBXC_USE_ASAN=OFF"])
        build_install(root / "containers", containers_prefix)
        executables = {}
        for label, source in (("old", old), ("new", new)):
            prefix, build = root / f"{label}-prefix", root / f"{label}-library"
            configure(source, build,
                      ["-DAGENT_MEMORY_ENABLE_MDBX=ON", "-DAGENT_MEMORY_ENABLE_JSON=OFF",
                       "-DAGENT_MEMORY_BUILD_TESTS=OFF", "-DAGENT_MEMORY_BUILD_EXAMPLES=OFF",
                       "-DAGENT_MEMORY_BUILD_BENCHMARKS=OFF", "-DAGENT_MEMORY_BUILD_RESEARCH_TESTS=OFF",
                       "-DAGENT_MEMORY_MDBX_DEPS_MODE=SYSTEM", f"-DCMAKE_PREFIX_PATH={dep_prefix}"])
            build_install(build, prefix)
            consumer = root / f"{label}-consumer"
            configure(new / "tests/installed-exact-generation", consumer,
                      [f"-DCMAKE_PREFIX_PATH={prefix};{containers_prefix};{dep_prefix}",
                       f"-DUPGRADE_NEW={'ON' if label == 'new' else 'OFF'}"])
            run(["cmake", "--build", consumer, "--config", "Release", "--parallel", "4"])
            executable = consumer / ("exact_generation_upgrade.exe" if os.name == "nt" else "exact_generation_upgrade")
            if os.name == "nt" and not args.mingw:
                executable = consumer / "Release/exact_generation_upgrade.exe"
            executables[label] = executable
            receipt[f"{label}_executable_sha256"] = sha(executable)
            receipt[f"{label}_cmake_cache_sha256"] = sha(build / "CMakeCache.txt")
            receipt[f"{label}_toolchain"] = [line for line in (build / "CMakeCache.txt").read_text().splitlines()
                                            if any(line.startswith(key + ":") for key in
                                                   ("CMAKE_CXX_COMPILER", "CMAKE_GENERATOR", "CMAKE_CXX_FLAGS"))]
        for label, mode, expected in (("old", "create", 0), ("new", "upgrade", 0),
                                       ("new", "interrupt", 24), ("new", "verify", 0),
                                       ("new", "crash", 23), ("new", "verify", 0), ("old", "verify", 0)):
            output = run([executables[label], mode, db, cas, snapshot], expected, capture=True)
            receipt["processes"].append({"library": label, "mode": mode, "exit": expected, "output": output})
        receipt["canonical_snapshot_sha256"] = sha(snapshot)
        receipt["canonical_snapshot"] = snapshot.read_text()
        receipt["fixture_source_sha256"] = sha(new / "tests/installed-exact-generation/consumer.cpp")
        receipt["fixture_helper_sha256"] = sha(new / "tests/infrastructure/mdbx/exact_generation_fixture.hpp")
        receipt["result"] = "PASS"
        (artifacts / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        print("PASS receipt:", artifacts / "receipt.json", flush=True)


if __name__ == "__main__":
    main()
