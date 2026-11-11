"""Append-only, hash-chained journal (JSON Lines).

Every record is wrapped in an envelope:
    {"seq": n, "prev": <hash of previous envelope or GENESIS>, "kind": str,
     "recorded_at": ts, "body": {...}, "hash": sha256(canonical envelope without "hash")}

Editing, deleting, reordering or truncating-in-the-middle is detected by verify().
Tail truncation cannot be detected by a chain alone; callers that need it compare
head() with a previously published anchor (see anchor()/verify(expected_head=...)).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterator

from .core import CareerTwinError, canonical_json, sha256_hex

GENESIS = "0" * 64


class IntegrityError(CareerTwinError):
    pass


def _envelope_hash(env: dict[str, Any]) -> str:
    return sha256_hex(canonical_json({k: v for k, v in env.items() if k != "hash"}))


class Journal:
    def __init__(self, path: str | os.PathLike[str] | None = None, *, name: str = "journal"):
        self.name = name
        self.path = Path(path) if path is not None else None
        self._records: list[dict[str, Any]] = []
        if self.path is not None and self.path.exists():
            self._load()

    # -- persistence -----------------------------------------------------
    def _load(self) -> None:
        assert self.path is not None
        records = []
        with self.path.open("r", encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                if not line.strip():
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise IntegrityError(f"{self.name}: line {lineno} is not valid JSON") from exc
        self._records = records
        self.verify()

    def append(self, kind: str, body: dict[str, Any], recorded_at: str) -> dict[str, Any]:
        env = {
            "seq": len(self._records),
            "prev": self._records[-1]["hash"] if self._records else GENESIS,
            "kind": kind,
            "recorded_at": recorded_at,
            "body": json.loads(canonical_json(body)),  # deep copy + JSON-safety check
        }
        env["hash"] = _envelope_hash(env)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(canonical_json(env) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
        self._records.append(env)
        return env

    # -- reading ---------------------------------------------------------
    def __len__(self) -> int:
        return len(self._records)

    def __iter__(self) -> Iterator[dict[str, Any]]:
        # deep copies: callers can never mutate journal state through a read
        return (json.loads(canonical_json(r)) for r in self._records)

    def records(self, kind: str | None = None) -> list[dict[str, Any]]:
        return [r for r in self if kind is None or r["kind"] == kind]

    def head(self) -> str:
        return self._records[-1]["hash"] if self._records else GENESIS

    def anchor(self) -> dict[str, Any]:
        return {"journal": self.name, "length": len(self._records), "head": self.head()}

    def verify(self, expected_head: str | None = None, expected_length: int | None = None) -> bool:
        prev = GENESIS
        for i, env in enumerate(self._records):
            if not isinstance(env, dict) or set(env) != {"seq", "prev", "kind", "recorded_at", "body", "hash"}:
                raise IntegrityError(f"{self.name}: record {i} has an unexpected envelope shape")
            if env["seq"] != i:
                raise IntegrityError(f"{self.name}: record {i} has seq {env['seq']} (reordered or deleted)")
            if env["prev"] != prev:
                raise IntegrityError(f"{self.name}: record {i} breaks the hash chain")
            if env["hash"] != _envelope_hash(env):
                raise IntegrityError(f"{self.name}: record {i} was altered after it was written")
            prev = env["hash"]
        if expected_length is not None and len(self._records) < expected_length:
            raise IntegrityError(f"{self.name}: truncated ({len(self._records)} < anchored {expected_length})")
        if expected_head is not None:
            hashes = {r["hash"] for r in self._records} | {GENESIS}
            if expected_head not in hashes:
                raise IntegrityError(f"{self.name}: anchored head is no longer part of the chain")
        return True
