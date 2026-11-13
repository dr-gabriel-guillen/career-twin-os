"""Schema-boundary fuzzing.

``mutations`` derives, from a schema and one valid document, single-fault
documents that a correct Draft 2020-12 validator MUST reject. ``random_json``
produces arbitrary JSON for crash-robustness checks of validators and linters.
"""

from __future__ import annotations

import random
from collections.abc import Iterator
from typing import Any

MALFORMED_DATETIMES = (
    "2026-01-05", "2026-01-05T09:00:00", "2026-01-05 09:00:00Z", "2026-02-30T09:00:00Z",
    "2026-01-05T24:00:00Z", "2026-01-05T09:60:00Z", "05/01/2026", "1767603600", "", "Z",
    "2026-01-05T09:00:00+2", "2026-01-05T09:00:00Z ",
)  # fmt: skip

_SAMPLES: dict[str, Any] = {
    "string": "fuzz", "number": 1.5, "integer": 7, "boolean": True, "null": None, "array": [], "object": {},
}  # fmt: skip


def _types(spec: dict[str, Any]) -> list[str]:
    declared = spec.get("type")
    if declared is None:
        return []
    return list(declared) if isinstance(declared, list) else [declared]


def _wrong_type_values(types: list[str]) -> list[tuple[str, Any]]:
    if not types:
        return []
    out: list[tuple[str, Any]] = []
    for kind, sample in _SAMPLES.items():
        if kind in types:
            continue
        if kind == "integer" and "number" in types:
            continue  # 7 is a valid number
        if kind == "number" and "integer" in types and "number" not in types:
            out.append(("type=non-integer", 1.5))
            continue
        out.append((f"type={kind}", sample))
    return out


def value_mutations(spec: dict[str, Any], current: Any) -> Iterator[tuple[str, Any]]:
    """Invalid replacement values for one property described by ``spec``."""
    types = _types(spec)
    yield from _wrong_type_values(types)
    if "enum" in spec:
        yield "enum-outside", "__not_in_enum__"
        first = spec["enum"][0]
        if isinstance(first, str):
            yield "enum-case", first.upper() if first.upper() != first else first.lower()
    if "const" in spec:
        yield "const-other", f"{spec['const']}_x"
    if "minimum" in spec:
        yield "below-minimum", spec["minimum"] - 1
    if "maximum" in spec:
        yield "above-maximum", spec["maximum"] + 1
    if spec.get("minLength", 0) > 0:
        yield "too-short", ""
    if spec.get("format") == "date-time":
        for bad in MALFORMED_DATETIMES:
            yield f"datetime={bad!r}", bad
    if "array" in types:
        items = spec.get("items", {})
        sample = current if isinstance(current, list) and current else None
        if spec.get("minItems", 0) > 0:
            yield "too-few-items", (sample or [])[: spec["minItems"] - 1]
        if "maxItems" in spec:
            yield "too-many-items", [(sample or [0])[0]] * (spec["maxItems"] + 1)
        if spec.get("uniqueItems"):
            item = sample[0] if sample else "dup"
            yield "duplicate-items", [item, item]
        if isinstance(items, dict):
            for label, bad in _wrong_type_values(_types(items)):
                yield f"item-{label}", [*(sample or []), bad]
            if "properties" in items and sample and isinstance(sample[0], dict):
                for label, bad_item in mutations(items, sample[0]):
                    yield f"item0.{label}", [bad_item, *sample[1:]]
    if "object" in types and "properties" in spec and isinstance(current, dict):
        yield from mutations(spec, current)


def mutations(schema: dict[str, Any], doc: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    """Single-fault variants of ``doc`` that must fail validation against ``schema``."""
    for prop in schema.get("required", []):
        if prop in doc:
            yield f"remove:{prop}", {k: v for k, v in doc.items() if k != prop}
    if schema.get("additionalProperties") is False:
        yield "add:unknown-property", {**doc, "__fuzz_unknown__": 1}
    for prop, spec in schema.get("properties", {}).items():
        if not isinstance(spec, dict):
            continue
        for label, bad in value_mutations(spec, doc.get(prop)):
            yield f"{prop}:{label}", {**doc, prop: bad}


_ALPHABET = "abc XYZ 019 _-/:.@=+\t\n\"'<>&%$#{}[]\\éñ\u4e2d\u6587\u202e\u200b\u2066\U0001f600\ud800\x00"


def random_json(rng: random.Random, depth: int = 0) -> Any:
    """Arbitrary JSON-like value (may include huge numbers and hostile strings)."""
    roll = rng.random()
    if depth > 4 or roll < 0.45:
        kind = rng.randrange(8)
        if kind == 0:
            return None
        if kind == 1:
            return rng.random() < 0.5
        if kind == 2:
            return rng.choice((0, -1, 1, 2**63, -(2**63), 10**30))
        if kind == 3:
            return rng.choice((0.0, -0.0, 1e308, -1e308, 5e-324, 0.1))
        return "".join(rng.choice(_ALPHABET) for _ in range(rng.randrange(0, 24)))
    if roll < 0.7:
        return [random_json(rng, depth + 1) for _ in range(rng.randrange(0, 5))]
    return {
        "".join(rng.choice(_ALPHABET) for _ in range(rng.randrange(1, 8))): random_json(rng, depth + 1)
        for _ in range(rng.randrange(0, 5))
    }


def scramble(rng: random.Random, doc: dict[str, Any], faults: int = 2) -> dict[str, Any]:
    """Replace ``faults`` random properties of a valid document with random JSON."""
    out = dict(doc)
    keys = list(out) or ["x"]
    for _ in range(faults):
        out[rng.choice(keys)] = random_json(rng, depth=2)
    return out
