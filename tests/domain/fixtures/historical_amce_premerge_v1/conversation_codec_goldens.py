#!/usr/bin/env python3
"""Independent AMCE v1 fixture authoring oracle; never calls the C++ codec.

The C++ test retains literal hex and SHA-256 expectations. This small stdlib
script documents how those independently authored fixtures can be reproduced;
it is not a generator for production data or a replacement round-trip test.
"""

import hashlib
import json
import struct


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
    # Existing unchanged AMXR: signed parent, unsigned object scalar.
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


def record(payload=None, ref=None, scope=b"s", binding=None, participant=None,
           role=None, observer=None, source_time=None, observed_time=None,
           links=()):
    return (b"AMCE\0\1\1" + (reference() if ref is None else ref)
            + b"\2" + blob(scope) + b"\3" + optional(binding)
            + b"\4" + optional(participant)
            + b"\5" + optional(None if role is None else bytes([role]))
            + b"\6" + optional(observer)
            + b"\7" + optional(None if source_time is None else struct.pack(">q", source_time))
            + b"\x08" + optional(None if observed_time is None else struct.pack(">q", observed_time))
            + b"\x09" + (b"\1" + content() if payload is None else payload)
            + b"\x0a" + struct.pack(">H", len(links)) + b"".join(links))


def vectors():
    maximum = 2**64 - 1
    return {
        "minimal": record(),
        "snapshot": record(payload=b"\2" + content("snapshot", ("a",)), binding=source()),
        "external_event": record(
            payload=b"\1" + content("external"),
            binding=source(1, external(), external(-2, 13, "e")),
            participant=actor("a", 7), role=4, observer=actor("o", 8),
            source_time=-1, observed_time=0),
        "object_revision": record(
            payload=b"\1" + content("revision"),
            binding=source(2, external(), revision=b"\xff\0r", marker=b"\xff\0m",
                           retained=evidence(b"\xff\0b", "l\0\u00e9"))),
        "edit": record(payload=b"\3" + reference(entry=1, revision=9)
                       + content("edit", ("x", "y", "x"))),
        "tombstone": record(payload=b"\4" + reference(entry=1, revision=9), role=5),
        "restore": record(payload=b"\5" + reference(entry=1, revision=9)
                          + reference(entry=1, revision=7)),
        "cross_links": record(payload=b"\1" + content("links"), links=(
            b"\4\1" + reference("z", 99, 44, 8),
            b"\5\1" + reference("z", 99, 45, 9),
            b"\1\2\0" + evidence(),
            b"\2\1" + reference(entry=1, revision=2),
            b"\3\1" + reference("z", 99, 46, 10))),
        "attachment_only": record(
            payload=b"\1" + content(None, (b"x\0\xff", "y")),
            participant=actor("a", 7), role=1, observer=actor("o", 8),
            source_time=-(2**63), observed_time=2**63 - 1),
        "boundaries": record(
            ref=reference("a", maximum, maximum, maximum), scope=b"\0\xffs",
            payload=b"\1" + content("\u042f\0\u00e9", (b"\0\xff",)),
            participant=actor("a", maximum), role=2, observer=actor("o", maximum),
            source_time=2**63 - 1, observed_time=-(2**63)),
        "present_empty": record(payload=b"\1" + content("", ("x",))),
    }


if __name__ == "__main__":
    print(json.dumps([{ "name": name, "size": len(raw), "hex": raw.hex(),
                        "sha256": hashlib.sha256(raw).hexdigest() }
                      for name, raw in vectors().items()], indent=2))
