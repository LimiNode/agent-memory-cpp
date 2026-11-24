#!/usr/bin/env python3
"""Independent corrected AMCE envelope v1 oracle; never imports/calls C++ code.

The grammar is authored from the conversation contract. The old experimental
v1 oracle is retained in historical_amce_premerge_v1, not a compatibility claim.
Output documents literal fixtures in conversation_codec_goldens.inc.
"""

import hashlib
import json
from pathlib import Path
import re
import struct
import sys


def blob(value):
    value = value.encode("utf-8") if isinstance(value, str) else value
    return struct.pack(">I", len(value)) + value


def optional(value):
    return b"\0" if value is None else b"\1" + value


def reference(namespace="a", conversation=1, entry=2, revision=3):
    return blob(namespace) + struct.pack(">QQQ", conversation, entry, revision)


def actor(namespace, identity):
    return blob(namespace) + struct.pack(">Q", identity)


def evidence(artifact=b"b", locator="l"):
    return blob(artifact) + blob(locator)


def external(parent=-1, key=42, kind="k"):
    return (b"AMXR\0\1" + blob("p") + blob("s") + blob("d") + blob(kind)
            + b"\1\1\0\0\0\x08" + struct.pack(">q", parent)
            + b"\2\0\0\0\x08" + struct.pack(">Q", key))


def source(mode=3, obj=None, event=None, revision=b"r", marker=None,
           recipe="v", retained=None):
    return (bytes([mode]) + optional(None if obj is None else blob(obj))
            + optional(None if event is None else blob(event)) + blob(revision)
            + optional(None if marker is None else blob(marker)) + blob(recipe)
            + (evidence() if retained is None else retained))


def content(text="t", attachments=()):
    return (optional(None if text is None else blob(text))
            + struct.pack(">H", len(attachments))
            + b"".join(blob(item) for item in attachments))


def record(payload=None, payload_type=1, schema=1, ref=None, scope=b"s",
           binding=None, participant=None, role=None, observer=None,
           source_time=None, observed_time=None, links=(), extensions=()):
    metadata = (b"\1" + (reference() if ref is None else ref)
                + b"\2" + blob(scope) + b"\3" + optional(binding)
                + b"\4" + optional(participant)
                + b"\5" + optional(None if role is None else bytes([role]))
                + b"\6" + optional(observer)
                + b"\7" + optional(None if source_time is None else struct.pack(">q", source_time))
                + b"\x08" + optional(None if observed_time is None else struct.pack(">q", observed_time))
                + b"\x09" + struct.pack(">H", len(links)) + b"".join(links)
                + struct.pack(">H", len(extensions)))
    for identity, critical, value in extensions:
        metadata += struct.pack(">IB", identity, critical) + blob(value)
    payload = content() if payload is None else payload
    header = b"AMCE" + struct.pack(">HHIIIHI", 1, 0,
                                  26 + len(metadata) + len(payload),
                                  len(metadata), payload_type, schema, len(payload))
    assert len(header) == 26
    return header + metadata + payload


def vectors():
    maximum = 2**64 - 1
    return {
        "minimal": record(),
        "snapshot": record(payload_type=2, payload=content("snapshot", ("a",)), binding=source()),
        "external_event": record(payload=content("external"),
            binding=source(1, external(), external(-2, 13, "e")),
            participant=actor("a", 7), role=4, observer=actor("o", 8),
            source_time=-1, observed_time=0),
        "object_revision": record(payload=content("revision"),
            binding=source(2, external(), revision=b"\xff\0r", marker=b"\xff\0m",
                           retained=evidence(b"\xff\0b", "l\0\u00e9"))),
        "edit": record(payload_type=3, payload=reference(entry=1, revision=9)
                       + content("edit", ("x", "y", "x"))),
        "tombstone": record(payload_type=4, payload=reference(entry=1, revision=9), role=5),
        "restore": record(payload_type=5, payload=reference(entry=1, revision=9)
                          + reference(entry=1, revision=7)),
        "cross_links": record(payload=content("links"), links=(
            b"\4\1" + reference("z", 99, 44, 8),
            b"\5\1" + reference("z", 99, 45, 9),
            b"\1\2\0" + evidence(),
            b"\2\1" + reference(entry=1, revision=2),
            b"\3\1" + reference("z", 99, 46, 10))),
        "attachment_only": record(payload=content(None, (b"x\0\xff", "y")),
            participant=actor("a", 7), role=1, observer=actor("o", 8),
            source_time=-(2**63), observed_time=2**63 - 1),
        "boundaries": record(ref=reference("a", maximum, maximum, maximum), scope=b"\0\xffs",
            payload=content("\u042f\0\u00e9", (b"\0\xff",)),
            participant=actor("a", maximum), role=2, observer=actor("o", maximum),
            source_time=2**63 - 1, observed_time=-(2**63)),
        "present_empty": record(payload=content("", ("x",))),
        "tool_call": record(payload_type=256,
            payload=blob(b"\0\xffc") + blob("shell") + blob(b"request@1"), binding=source()),
        "tool_result": record(payload_type=257,
            payload=b"\1" + reference(entry=1, revision=7) + blob(b"\0\xffc")
                    + b"\3" + optional(blob(b"result@1")), binding=source()),
        "unknown_type": record(payload_type=70000, schema=5, payload=b"\0\xffopaque"),
        "unknown_schema": record(payload_type=1, schema=2, payload=b"\xff\0next"),
        "extensions": record(extensions=((7, 0, b"\0\xff"), (9, 0, b""))),
        "critical_extension": record(extensions=((7, 1, b"\0\xff"),)),
        "tool_result_unresolved_cancelled": record(payload_type=257,
            payload=b"\2\0" + evidence(b"orphan@1", "record/3")
                    + blob(b"\0\xffc") + b"\4\0", binding=source()),
        "tool_result_unresolved_external": record(payload_type=257,
            payload=b"\2" + optional(blob(external())) + evidence(b"orphan@1", "record/3")
                    + blob(b"\0\xffc") + b"\1" + optional(blob(b"\0\xff")), binding=source()),
        "tool_result_resolved_no_body": record(payload_type=257,
            payload=b"\1" + reference(entry=1, revision=7) + blob(b"\0\xffc")
                    + b"\2\0", binding=source()),
    }


if __name__ == "__main__":
    records = [{"name": name, "size": len(raw), "hex": raw.hex(),
                "sha256": hashlib.sha256(raw).hexdigest()}
               for name, raw in vectors().items()]
    if sys.argv[1:] == ["--check"]:
        fixture = Path(__file__).with_suffix(".inc").read_text(encoding="utf-8")
        literals = re.findall(r'\{"([^"]+)",\s*((?:"[0-9a-f]+"\s*)+),\s*"([0-9a-f]{64})"\s*\}', fixture)
        actual = [(name, "".join(re.findall(r'"([0-9a-f]+)"', raw)), digest)
                  for name, raw, digest in literals]
        expected = [(item["name"], item["hex"], item["sha256"]) for item in records]
        if actual != expected:
            raise SystemExit("independent oracle and committed literal fixtures differ")
        print(f"{len(records)} independent AMCE envelope literals match")
    elif sys.argv[1:]:
        raise SystemExit("usage: conversation_codec_goldens.py [--check]")
    else:
        print(json.dumps(records, indent=2))
