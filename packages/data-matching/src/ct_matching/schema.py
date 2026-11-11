"""Dependency-free validator for the JSON Schema subset used by ``contracts/`` and lane A's own schemas.

Supported keywords: type, enum, const, required, properties, additionalProperties (bool),
items, uniqueItems, minItems, maxItems, minLength, maxLength, minimum, maximum, pattern,
format (date-time only). Unknown keywords raise, so a contract change cannot silently pass.
Tests cross-check this validator against the ``jsonschema`` library when it is installed.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"
_KNOWN = {"$schema", "$id", "title", "description", "type", "enum", "const", "required", "properties",
          "additionalProperties", "items", "uniqueItems", "minItems", "maxItems", "minLength", "maxLength",
          "minimum", "maximum", "pattern", "format"}
_TYPES = {"string": str, "object": dict, "array": list, "boolean": bool}
_DATE_TIME = re.compile(r"\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(\.\d+)?([Zz]|[+-]\d{2}:\d{2})")


class SchemaError(ValueError):
    """Raised by :func:`check` with every violation listed."""

    def __init__(self, name: str, errors: list[str]):
        super().__init__(f"{name}: " + "; ".join(errors[:8]) + (f" (+{len(errors) - 8} more)" if len(errors) > 8 else ""))
        self.errors = errors


@lru_cache(maxsize=None)
def load(name: str) -> dict[str, Any]:
    """Load a vendored schema by short name, e.g. ``job-posting`` or ``a-job-record``."""
    return json.loads((SCHEMA_DIR / f"{name}.schema.json").read_text(encoding="utf-8"))


def _type_ok(value: Any, name: str) -> bool:
    if name == "null":
        return value is None
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool) and value == value and abs(value) != float("inf")
    if name == "integer":
        return (isinstance(value, int) and not isinstance(value, bool)) or (isinstance(value, float) and value.is_integer())
    if name == "string":
        return isinstance(value, str)
    return isinstance(value, _TYPES[name])


def _is_date_time(value: str) -> bool:
    if not _DATE_TIME.fullmatch(value):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00").replace("z", "+00:00").replace("t", "T"))
        return True
    except ValueError:
        return False


def errors(value: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    unknown = set(schema) - _KNOWN
    if unknown:
        raise NotImplementedError(f"unsupported schema keywords at {path}: {sorted(unknown)}")
    out: list[str] = []
    types = schema.get("type")
    if types is not None:
        names = [types] if isinstance(types, str) else list(types)
        if not any(_type_ok(value, n) for n in names):
            return [f"{path}: expected {'/'.join(names)}, got {type(value).__name__}"]
    if "enum" in schema and not any(value == e and type(value) is type(e) for e in schema["enum"]):
        out.append(f"{path}: {value!r} not in enum")
    if "const" in schema and not (value == schema["const"] and type(value) is type(schema["const"])):
        out.append(f"{path}: must equal {schema['const']!r}")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            out.append(f"{path}: shorter than {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            out.append(f"{path}: longer than {schema['maxLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            out.append(f"{path}: does not match pattern")
        if schema.get("format") == "date-time" and not _is_date_time(value):
            out.append(f"{path}: not an RFC 3339 date-time")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            out.append(f"{path}: below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            out.append(f"{path}: above maximum {schema['maximum']}")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            out.append(f"{path}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            out.append(f"{path}: more than {schema['maxItems']} items")
        if schema.get("uniqueItems"):
            keys = [json.dumps(v, sort_keys=True) for v in value]
            if len(set(keys)) != len(keys):
                out.append(f"{path}: items not unique")
        if isinstance(schema.get("items"), dict):
            for idx, item in enumerate(value):
                out.extend(errors(item, schema["items"], f"{path}[{idx}]"))
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                out.append(f"{path}: missing required '{key}'")
        for key, sub in value.items():
            if key in props:
                out.extend(errors(sub, props[key], f"{path}.{key}"))
            elif schema.get("additionalProperties") is False:
                out.append(f"{path}: unexpected property '{key}'")
    return out


def check(value: Any, name: str) -> None:
    found = errors(value, load(name))
    if found:
        raise SchemaError(name, found)


def is_valid(value: Any, name: str) -> bool:
    return not errors(value, load(name))
