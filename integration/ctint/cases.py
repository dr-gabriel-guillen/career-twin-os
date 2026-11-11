"""Loader for the positive/negative contract fixtures in ``fixtures/contracts``.

A case is a named base document plus an optional list of JSON-Patch-style
operations (``add`` / ``replace`` / ``remove`` with RFC 6901 pointers). Keeping
negatives as patches makes each one differ from a valid document in exactly one way.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contracts import CONTRACTS
from .paths import fixtures_dir


@dataclass(frozen=True)
class Case:
    contract: str
    name: str
    doc: Any
    #: "valid" | "schema" (schema must reject) | "semantic" (schema accepts, rule must fire)
    layer: str
    rule: str | None = None

    @property
    def id(self) -> str:
        return f"{self.contract}:{self.layer}:{self.name}"


def _tokens(pointer: str) -> list[str]:
    if pointer == "":
        return []
    if not pointer.startswith("/"):
        raise ValueError(f"bad JSON pointer {pointer!r}")
    return [t.replace("~1", "/").replace("~0", "~") for t in pointer[1:].split("/")]


def apply_patch(doc: Any, ops: list[dict[str, Any]]) -> Any:
    """Apply add/replace/remove operations to a deep copy of ``doc``."""
    result = copy.deepcopy(doc)
    for op in ops:
        tokens = _tokens(op["path"])
        kind = op["op"]
        if not tokens:
            if kind == "replace":
                result = copy.deepcopy(op["value"])
                continue
            raise ValueError("only 'replace' may target the document root")
        parent = result
        for token in tokens[:-1]:
            parent = parent[int(token)] if isinstance(parent, list) else parent[token]
        last = tokens[-1]
        if isinstance(parent, list):
            if kind == "add":
                parent.insert(len(parent) if last == "-" else int(last), copy.deepcopy(op["value"]))
            elif kind == "replace":
                parent[int(last)] = copy.deepcopy(op["value"])
            elif kind == "remove":
                del parent[int(last)]
            else:
                raise ValueError(f"unsupported op {kind!r}")
        elif isinstance(parent, dict):
            if kind == "add":
                parent[last] = copy.deepcopy(op["value"])
            elif kind == "replace":
                if last not in parent:
                    raise KeyError(f"replace target missing: {op['path']}")
                parent[last] = copy.deepcopy(op["value"])
            elif kind == "remove":
                del parent[last]
            else:
                raise ValueError(f"unsupported op {kind!r}")
        else:
            raise TypeError(f"cannot patch into {type(parent).__name__} at {op['path']}")
    return result


def load_cases(contract: str, directory: Path | None = None) -> list[Case]:
    path = (directory or fixtures_dir() / "contracts") / f"{contract}.cases.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if data["contract"] != contract:
        raise ValueError(f"{path.name}: declares contract {data['contract']!r}")
    bases: dict[str, Any] = data["bases"]
    cases: list[Case] = []
    for entry in data["valid"]:
        doc = apply_patch(bases[entry["base"]], entry.get("patch", []))
        cases.append(Case(contract, entry["name"], doc, "valid"))
    for entry in data["invalid"]:
        doc = apply_patch(bases[entry["base"]], entry["patch"])
        layer = entry["layer"]
        if layer not in ("schema", "semantic"):
            raise ValueError(f"{contract}:{entry['name']}: bad layer {layer!r}")
        if layer == "semantic" and not entry.get("rule"):
            raise ValueError(f"{contract}:{entry['name']}: semantic case needs a rule")
        cases.append(Case(contract, entry["name"], doc, layer, entry.get("rule")))
    names = [c.name for c in cases]
    if len(names) != len(set(names)):
        raise ValueError(f"{contract}: duplicate case names")
    return cases


def all_cases(directory: Path | None = None) -> list[Case]:
    return [case for contract in CONTRACTS for case in load_cases(contract, directory)]
