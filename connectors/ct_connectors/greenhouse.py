"""Greenhouse Job Board API shape: ``{"jobs": [...], "meta": {...}}`` (list endpoint, content=true).

Shape modeled on the publicly documented Job Board API. No request is ever made here.
"""
from __future__ import annotations

from typing import Any

from ._common import as_list, build_record, comp_item, finish, get, iter_jobs, number, seen_id
from .base import Capture, Issue, ParseResult, SourceRecord


class GreenhouseAdapter:
    source = "greenhouse"

    def parse(self, payload: Any, capture: Capture) -> ParseResult:
        issues: list[Issue] = []
        records: list[SourceRecord] = []
        seen: list[str] = []
        for _idx, job in iter_jobs(payload, self.source, issues, "jobs"):
            sid = seen_id(job)
            if sid:
                seen.append(sid)
            comp = []
            for rng in as_list(job.get("pay_input_ranges")):
                if not isinstance(rng, dict):
                    comp.append(None)
                    continue
                lo, hi = number(rng.get("min_cents")), number(rng.get("max_cents"))
                label = rng.get("title") if isinstance(rng.get("title"), str) else ""
                low = label.lower()
                basis = "base" if "base" in low else ("total" if "total" in low or "ote" in low else "unknown")
                comp.append(comp_item(None if lo is None else lo / 100, None if hi is None else hi / 100,
                                      rng.get("currency_type"), label, basis))
            meta = {m.get("name"): m.get("value") for m in as_list(job.get("metadata"))
                    if isinstance(m, dict) and isinstance(m.get("name"), str)}
            departments = as_list(job.get("departments"))
            rec = build_record(
                capture, job, issues, source_id=job.get("id"), title=job.get("title"), url=job.get("absolute_url"),
                description_html=job.get("content"), entity_escaped=True, organization=job.get("company_name"),
                posted_at=job.get("first_published"), updated_at=job.get("updated_at"),
                location_raw=get(job, "location", "name"),
                remote_raw=meta.get("Workplace Type"), employment_raw=meta.get("Employment Type"),
                seniority_raw=meta.get("Level"),
                department=departments[0].get("name") if departments and isinstance(departments[0], dict) else None,
                compensation_raw=comp)
            if rec:
                records.append(rec)
        return finish(capture, records, issues, seen)
