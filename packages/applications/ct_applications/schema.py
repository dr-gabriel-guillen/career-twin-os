"""Minimal JSON Schema checker for the frozen contracts in /contracts.

Supports exactly the keywords those contracts use (type, enum, const, required,
properties, additionalProperties, items, minItems, maxItems, uniqueItems,
minLength, minimum, maximum, format=date-time). An unknown keyword is an error,
so a future contract change cannot be silently under-validated.
The test-suite cross-checks this implementation against `jsonschema` when installed.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from .core import ValidationError, parse_ts

_ANNOTATIONS = {"$schema", "$id", "title", "description"}
_KNOWN = _ANNOTATIONS | {
    "type", "enum", "const", "required", "properties", "additionalProperties", "items",
    "minItems", "maxItems", "uniqueItems", "minLength", "minimum", "maximum", "format",
}
_TYPES = {
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "integer": lambda v: (isinstance(v, int) and not isinstance(v, bool)) or (isinstance(v, float) and v.is_integer()),
}


def contracts_dir() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "contracts" / "application-event.schema.json").exists():
            return parent / "contracts"
    raise FileNotFoundError("frozen contracts/ directory not found above " + str(here))


def check_supported(schema: Any, where: str = "$") -> None:
    """Refuse a schema that uses anything this checker would silently ignore."""
    if not isinstance(schema, dict):
        raise ValidationError(f"{where}: sub-schema must be an object")
    unknown = set(schema) - _KNOWN
    if unknown:
        raise ValidationError(f"{where}: schema keyword(s) not supported by lane B checker: {sorted(unknown)}")
    if not isinstance(schema.get("additionalProperties", True), bool):
        raise ValidationError(f"{where}: schema-valued additionalProperties is not supported")
    for key, child in schema.get("properties", {}).items():
        check_supported(child, f"{where}.{key}")
    if "items" in schema:
        check_supported(schema["items"], f"{where}[]")


@lru_cache(maxsize=None)
def load_contract(name: str) -> dict[str, Any]:
    schema = json.loads((contracts_dir() / f"{name}.schema.json").read_text(encoding="utf-8"))
    check_supported(schema, name)
    return schema


def errors(instance: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    out: list[str] = []
    unknown = set(schema) - _KNOWN
    if unknown:
        raise ValidationError(f"schema keyword(s) not supported by lane B checker: {sorted(unknown)}")
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_TYPES[t](instance) for t in types):
            return [f"{path}: expected {'|'.join(types)}, got {type(instance).__name__}"]
    if "enum" in schema and instance not in schema["enum"]:
        out.append(f"{path}: {instance!r} not in {schema['enum']}")
    if "const" in schema and instance != schema["const"]:
        out.append(f"{path}: must equal {schema['const']!r}")
    if isinstance(instance, str):
        if len(instance) < schema.get("minLength", 0):
            out.append(f"{path}: shorter than minLength {schema['minLength']}")
        if schema.get("format") == "date-time":
            try:
                parse_ts(instance)
            except ValidationError:
                out.append(f"{path}: not an RFC 3339 date-time")
    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            out.append(f"{path}: below minimum {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            out.append(f"{path}: above maximum {schema['maximum']}")
    if isinstance(instance, list):
        if len(instance) < schema.get("minItems", 0):
            out.append(f"{path}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            out.append(f"{path}: more than {schema['maxItems']} items")
        if schema.get("uniqueItems"):
            seen = [json.dumps(i, sort_keys=True) for i in instance]
            if len(seen) != len(set(seen)):
                out.append(f"{path}: items are not unique")
        if "items" in schema:
            for i, item in enumerate(instance):
                out.extend(errors(item, schema["items"], f"{path}[{i}]"))
    if isinstance(instance, dict):
        props = schema.get("properties", {})
        for req in schema.get("required", []):
            if req not in instance:
                out.append(f"{path}: missing required property {req!r}")
        for key, value in instance.items():
            if key in props:
                out.extend(errors(value, props[key], f"{path}.{key}"))
            elif schema.get("additionalProperties") is False:
                out.append(f"{path}: additional property {key!r} not allowed")
    return out


def validate(instance: Any, contract: str) -> None:
    problems = errors(instance, load_contract(contract))
    if problems:
        raise ValidationError(f"{contract} contract violation: " + "; ".join(problems))
