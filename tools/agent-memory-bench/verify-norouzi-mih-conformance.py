#!/usr/bin/env python3
"""Compile and canonicalize the pinned upstream MIH fixture result."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path


THIS = Path(__file__).resolve().parent
FIXTURE = THIS.parent.parent / "tests" / "eval" / "fixtures" / "mih-global-exact-conformance-v1.json"
MASK = (1 << 64) - 1


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def splitmix64(state: int) -> tuple[int, int]:
    state = (state + 0x9E3779B97F4A7C15) & MASK
    value = state
    value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & MASK
    value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & MASK
    return state, (value ^ (value >> 31)) & MASK


def words(seed: str, count: int) -> list[int]:
    state, result = int(seed, 16), []
    for _ in range(count):
        state, value = splitmix64(state)
        result.append(value)
    return result


HARNESS = r'''
#include <cstdint>
#include <iostream>
#include <vector>
#include "mihasher.h"

static std::uint64_t next(std::uint64_t& state) {
    state += 0x9e3779b97f4a7c15ULL;
    std::uint64_t value = state;
    value = (value ^ (value >> 30U)) * 0xbf58476d1ce4e5b9ULL;
    value = (value ^ (value >> 27U)) * 0x94d049bb133111ebULL;
    return value ^ (value >> 31U);
}
static void put(std::vector<UINT8>& values, const std::size_t row, const std::uint64_t value) {
    for(std::size_t byte = 0; byte < 8U; ++byte) values[row * 32U + byte] = static_cast<UINT8>((value >> (byte * 8U)) & 0xffU);
}
int main() {
    constexpr UINT32 documents = 800U, queries = 4U;
    std::vector<UINT8> database(documents * 32U, 0U), query(queries * 32U, 0U);
    std::uint64_t document_state = 0x6a09e667f3bcc909ULL, query_state = 0xbb67ae8584caa73bULL;
    for(UINT32 row = 0; row < documents; ++row) put(database, row, next(document_state));
    for(UINT32 row = 0; row < queries; ++row) put(query, row, next(query_state));
    mihasher index(256, 16); index.populate(database.data(), documents, 32);
    index.setK(documents); std::vector<UINT32> results(queries * documents), counts(queries * 257U); std::vector<qstat> stats(queries);
    index.batchquery(results.data(), counts.data(), stats.data(), query.data(), queries, 32);
    for(UINT32 row = 0; row < queries; ++row) {
        std::cout << row;
        for(UINT32 item = 0; item < documents; ++item) std::cout << ' ' << results[row * documents + item];
        std::cout << '\n';
    }
}
'''


def output_digest(lines: list[str], fixture: dict[str, object]) -> str:
    documents = words(str(fixture["document_seed"]), int(fixture["document_count"]))
    queries = words(str(fixture["query_seed"]), int(fixture["query_count"]))
    expected_ks = tuple(fixture["ks"])
    required_cutoff_max = int(dict(fixture["upstream_reference"])["required_cutoff_max"])
    require(all(int(value) <= required_cutoff_max for value in fixture["expected_cutoff_at_k768_per_query"]), "upstream MIH fixture cutoff contract differs")
    values: dict[int, list[int]] = {}
    for line in lines:
        try:
            fields = [int(item) for item in line.split()]
        except ValueError:
            continue
        if len(fields) < 2:
            continue
        query, *positions = fields
        require(query not in values and len(positions) == len(documents), "upstream MIH output shape differs")
        values[query] = positions
    require(set(values) == set(range(len(queries))), "upstream MIH output coverage differs")
    digest = hashlib.sha256(b"agent-memory-global-exact-mih-fixture-output-v1")
    for query, code in enumerate(queries):
        positions = values[query]
        require(all(1 <= position <= len(documents) for position in positions) and set(positions) == set(range(1, len(documents) + 1)), "upstream MIH complete candidate set differs")
        ordered = sorted(((code ^ document).bit_count(), position) for position, document in enumerate(documents))
        for k in expected_ks:
            prefix = ordered[:k]
            require(prefix[-1][0] <= required_cutoff_max, "upstream MIH fixture exceeds the reference cutoff limit")
            digest.update(query.to_bytes(4, "little")); digest.update(k.to_bytes(4, "little")); digest.update(k.to_bytes(8, "little"))
            for distance, position in prefix: digest.update(distance.to_bytes(4, "little")); digest.update(position.to_bytes(4, "little"))
    return digest.hexdigest()


def run(upstream: Path, compiler: str, docker_image: str | None) -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    reference = fixture["upstream_reference"]
    require(reference == {"repository": "https://github.com/norouzi/mih", "commit": "96a629de834c1b974b0c5e378ab1037ee42120ab", "binary_encoding": "N_by_B_uint8_bits_lsb_first_v1", "tie_rule": "canonicalize_complete_cutoff_candidates_by_hamming_distance_then_document_position_v1", "required_cutoff_max": 128, "build": {"compiler": "g++ -std=c++17 -O2", "container_image": "gcc@sha256:056fa682471704249f619f65ccec87d671ad5f1b20878da54d60b0b863486621", "runner": "tools/agent-memory-bench/verify-norouzi-mih-conformance.py"}}, "upstream MIH fixture contract differs")
    commit = subprocess.check_output(["git", "-C", str(upstream), "rev-parse", "HEAD"], text=True).strip()
    require(commit == reference["commit"], "upstream MIH commit differs")
    sources = [upstream / "src" / name for name in ("mihasher.cpp", "sparse_hashtable.cpp", "bucket_group.cpp", "array32.cpp")]
    require(all(path.is_file() for path in sources), "upstream MIH sources are incomplete")
    with tempfile.TemporaryDirectory() as temporary:
        executable = Path(temporary) / ("mih-conformance.exe" if sys.platform == "win32" else "mih-conformance")
        if docker_image is None:
            subprocess.run([compiler, "-std=c++17", "-O2", "-I", str(upstream / "include"), "-x", "c++", "-", *map(str, sources), "-o", str(executable)], input=HARNESS, text=True, check=True)
            completed = subprocess.run([str(executable)], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        else:
            temporary_path = Path(temporary); harness_path = temporary_path / "mih-conformance.cpp"; harness_path.write_text(HARNESS, encoding="utf-8", newline="\n")
            linux_sources = [f"/upstream/src/{path.name}" for path in sources]
            mounts = ["--mount", f"type=bind,source={upstream.resolve()},target=/upstream,readonly", "--mount", f"type=bind,source={temporary_path},target=/work"]
            subprocess.run(["docker", "run", "--rm", *mounts, "-w", "/work", docker_image, "g++", "-std=c++17", "-O2", "-I", "/upstream/include", "/work/mih-conformance.cpp", *linux_sources, "-o", "/work/mih-conformance"], check=True)
            completed = subprocess.run(["docker", "run", "--rm", *mounts, "-w", "/work", docker_image, "/work/mih-conformance"], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    digest = output_digest(completed.stdout.splitlines(), fixture)
    require(digest == fixture["canonical_outputs_sha256"], "upstream MIH canonical output differs")
    print("pinned upstream norouzi/mih conformance fixture passed")


def self_test() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    require(words(str(fixture["document_seed"]), 1)[0] == 0x63CFC62A2B097592, "upstream MIH fixture generator differs")
    require(fixture["expected_cutoff_at_k768_per_query"] == [39, 39, 38, 39], "upstream MIH fixture cutoff differs")
    print("pinned upstream norouzi/mih conformance runner self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--upstream", type=Path); parser.add_argument("--compiler", default="g++"); parser.add_argument("--docker-image"); parser.add_argument("--self-test", action="store_true"); args = parser.parse_args()
    try:
        if args.self_test: self_test(); return 0
        if args.upstream is None: parser.error("--upstream is required unless --self-test is used")
        run(args.upstream, args.compiler, args.docker_image); return 0
    except (OSError, ValueError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
        print(f"verify-norouzi-mih-conformance: {error}", file=sys.stderr); return 1


if __name__ == "__main__":
    raise SystemExit(main())
