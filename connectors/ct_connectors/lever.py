"""Lever Postings API shape: a JSON array of postings (``mode=json``).

Shape modeled on the publicly documented Postings API. No request is ever made here.
"""
from __future__ import annotations

from typing import Any

from ._common import as_list, build_record, comp_item, finish, get, iter_jobs, seen_id
from .base import Capture, Issue, ParseResult, SourceRecord


class LeverAdapter:
    source = "lever"

    def parse(self, payload: Any, capture: Capture) -> ParseResult:
        issues: list[Issue] = []
        records: list[SourceRecord] = []
        seen: list[str] = []
        for _idx, job in iter_jobs(payload, self.source, issues, None):
            sid = seen_id(job)
            if sid:
                seen.append(sid)
            html_parts = [job.get("description") if isinstance(job.get("description"), str) else ""]
            for block in as_list(job.get("lists")):
                if isinstance(block, dict):
                    head = block.get("text") if isinstance(block.get("text"), str) else ""
                    body = block.get("content") if isinstance(block.get("content"), str) else ""
                    html_parts.append(f"<h3>{_esc(head)}</h3><ul>{body}</ul>")
            if isinstance(job.get("additional"), str):
                html_parts.append(job["additional"])
            sal = job.get("salaryRange")
            comp = []
            if sal is not None:
                comp.append(comp_item(sal.get("min"), sal.get("max"), sal.get("currency"), sal.get("interval"), "base")
                            if isinstance(sal, dict) else None)
            rec = build_record(
                capture, job, issues, source_id=job.get("id"), title=job.get("text"), url=job.get("hostedUrl"),
                apply_url=job.get("applyUrl"), description_html="".join(html_parts),
                description_plain=job.get("descriptionPlain"),
                posted_at=job.get("createdAt"), location_raw=get(job, "categories", "location"),
                country_raw=job.get("country"), remote_raw=job.get("workplaceType"),
                employment_raw=get(job, "categories", "commitment"),
                department=get(job, "categories", "team") or get(job, "categories", "department"),
                compensation_raw=comp)
            if rec:
                records.append(rec)
        return finish(capture, records, issues, seen)


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
