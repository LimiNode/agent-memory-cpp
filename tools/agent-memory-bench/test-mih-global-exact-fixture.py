"""Exercise native fixture rejection without modifying the frozen golden file."""

import argparse
import json
from pathlib import Path
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    args = parser.parse_args()
    fixture = json.loads(args.fixture.read_text(encoding="utf-8"))
    cases = (
        ("unknown construction", "construction", "unknown", "contract is invalid"),
        ("obsolete construction", "construction", "splitmix64_word0_only_zero_extend_to_256_v1", "contract is invalid"),
        ("corrupt digest", "canonical_outputs_sha256", "0" * 64, "canonical output differs"),
        ("corrupt cutoff", "expected_cutoff_at_k768_per_query", [106, 114, 110, 108], "cutoff differs"),
    )
    with tempfile.TemporaryDirectory(prefix="mih-fixture-negative-", dir=Path.cwd()) as temporary:
        path = Path(temporary) / "mutated.json"
        for label, field, replacement, diagnostic in cases:
            changed = dict(fixture)
            changed[field] = replacement
            path.write_text(json.dumps(changed), encoding="utf-8")
            result = subprocess.run(
                [str(args.binary.resolve()), "--global-exact-fixture", str(path)],
                capture_output=True, text=True, check=False, timeout=120,
            )
            if result.returncode != 1 or diagnostic not in result.stderr:
                raise RuntimeError(f"{label} was not rejected by its native gate: {result}")
    print(f"native MIH fixture mutation controls: PASS ({len(cases)} controls)")


if __name__ == "__main__":
    main()
