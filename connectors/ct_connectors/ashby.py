"""Ashby public job-board posting API shape: ``{"jobs": [...]}`` (includeCompensation=true).

Shape modeled on the publicly documented posting API. No request is ever made here.
"""
from __future__ import annotations

from typing import Any

from ._common import as_list, build_record, comp_item, finish, get, iter_jobs, seen_id
from .base import Capture, Issue, ParseResult, SourceRecord


class AshbyAdapter:
    source = "ashby"

    def parse(self, payload: Any, capture: Capture) -> ParseResult:
        issues: list[Issue] = []
        records: list[SourceRecord] = []
        seen: list[str] = []
        for _idx, job in iter_jobs(payload, self.source, issues, "jobs"):
            sid = seen_id(job)
            if sid:
                seen.append(sid)
            comp = []
            for part in as_list(get(job, "compensation", "summaryComponents")):
                if not isinstance(part, dict):
                    comp.append(None)
                    continue
                kind = str(part.get("compensationType") or "").lower()
                if kind not in ("salary", "base", "hourly"):
                    continue  # equity/bonus components are not base pay; never summed in
                comp.append(comp_item(part.get("minValue"), part.get("maxValue"), part.get("currencyCode"),
                                      part.get("interval"), "base"))
            remote = job.get("workplaceType")
            if not isinstance(remote, str) and job.get("isRemote") is True:
                remote = "Remote"
            rec = build_record(
                capture, job, issues, source_id=job.get("id"), title=job.get("title"), url=job.get("jobUrl"),
                apply_url=job.get("applyUrl"), description_html=job.get("descriptionHtml"),
                description_plain=job.get("descriptionPlain"), posted_at=job.get("publishedAt"),
                location_raw=job.get("location"), country_raw=get(job, "address", "postalAddress", "addressCountry"),
                remote_raw=remote, employment_raw=job.get("employmentType"),
                department=job.get("department") or job.get("team"), listed=job.get("isListed"),
                compensation_raw=comp)
            if rec:
                records.append(rec)
        return finish(capture, records, issues, seen)
