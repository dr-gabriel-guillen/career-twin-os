"""CareerTwin native interchange format for ``synthetic`` and ``user-import`` sources.

A flat, explicit list of postings: ``{"postings": [{id, title, url, ...}]}``. This is the
non-ATS source plugin and the format a user-authorized manual import would use.
"""
from __future__ import annotations

from typing import Any

from ._common import as_list, build_record, comp_item, finish, iter_jobs, seen_id
from .base import Capture, Issue, ParseResult, SourceRecord


class NativeAdapter:
    def __init__(self, source: str = "synthetic") -> None:
        self.source = source

    def parse(self, payload: Any, capture: Capture) -> ParseResult:
        issues: list[Issue] = []
        records: list[SourceRecord] = []
        seen: list[str] = []
        for _idx, job in iter_jobs(payload, self.source, issues, "postings"):
            sid = seen_id(job)
            if sid:
                seen.append(sid)
            comp = []
            for c in as_list(job.get("compensation")):
                comp.append(comp_item(c.get("min"), c.get("max"), c.get("currency"), c.get("period"), c.get("basis"))
                            if isinstance(c, dict) else None)
            listed = (not job["revoked"]) if isinstance(job.get("revoked"), bool) else None
            rec = build_record(
                capture, job, issues, source_id=job.get("id"), title=job.get("title"), url=job.get("url"),
                apply_url=job.get("apply_url"), description_html=job.get("description_html"),
                description_plain=job.get("description"), organization=job.get("organization"),
                posted_at=job.get("posted_at"), updated_at=job.get("updated_at"), expires_at=job.get("expires_at"),
                location_raw=job.get("location"), country_raw=job.get("country"), remote_raw=job.get("remote"),
                employment_raw=job.get("employment_type"), seniority_raw=job.get("seniority"),
                department=job.get("department"), listed=listed, compensation_raw=comp,
                declared_skills=as_list(job.get("skills")))
            if rec:
                records.append(rec)
        return finish(capture, records, issues, seen)
