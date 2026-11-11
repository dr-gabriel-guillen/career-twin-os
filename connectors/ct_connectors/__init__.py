"""Offline ATS/source adapters for CareerTwin OS (lane CAREER-TWIN-A)."""
from __future__ import annotations

from typing import Any

from .ashby import AshbyAdapter
from .base import (CONNECTOR_API_VERSION, CONTRACT_SOURCES, Adapter, Capture, ConnectorError, ErrorClass,
                   FixtureFetcher, Issue, LiveFetchDisabled, ParseResult, SourceRecord, split_envelope)
from .greenhouse import GreenhouseAdapter
from .lever import LeverAdapter
from .native import NativeAdapter

_REGISTRY: dict[str, Adapter] = {
    "greenhouse": GreenhouseAdapter(), "lever": LeverAdapter(), "ashby": AshbyAdapter(),
    "synthetic": NativeAdapter("synthetic"), "user-import": NativeAdapter("user-import"),
}


def adapter_for(source: str) -> Adapter:
    adapter = _REGISTRY.get(source) if isinstance(source, str) else None
    if adapter is None:
        raise ConnectorError(ErrorClass.UNSUPPORTED_SOURCE, f"no adapter registered for source {source!r}")
    return adapter


def parse_envelope(doc: Any) -> ParseResult:
    """Parse one capture envelope. Whole-payload failures raise ConnectorError."""
    capture, payload = split_envelope(doc)
    return adapter_for(capture.source).parse(payload, capture)


__all__ = ["CONNECTOR_API_VERSION", "CONTRACT_SOURCES", "Adapter", "Capture", "ConnectorError", "ErrorClass",
           "FixtureFetcher", "Issue", "LiveFetchDisabled", "ParseResult", "SourceRecord", "adapter_for",
           "parse_envelope", "split_envelope", "GreenhouseAdapter", "LeverAdapter", "AshbyAdapter", "NativeAdapter"]
