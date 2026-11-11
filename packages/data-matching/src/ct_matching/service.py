"""Application facade shared by the CLI and the HTTP API (import, query, rank, provenance)."""
from __future__ import annotations

from typing import Any

from . import schema, taxonomy
from .matching import MatchRequestError, legacy_profile_to_vault, rank
from .normalize import utc
from .retrieval import HashedEmbeddingRetriever, HybridRetriever, LexicalRetriever, job_document, skill_vector
from .store import JobStore
from .vault import EvidenceVault, VaultError

API_VERSION = "a-api/0.1"
LIST_FIELDS_DROPPED = ("description_text",)
RETRIEVERS = ("lexical", "hashed", "hybrid")


class RequestError(ValueError):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.status = code, status


def vault_from_profile(doc: Any, at: str) -> tuple[EvidenceVault, dict[str, Any]]:
    """Build a vault + preferences from a lane-A profile document or main's legacy profile fixture."""
    if not isinstance(doc, dict):
        raise RequestError("bad-profile", "profile must be a JSON object")
    try:
        if "claims" not in doc and "skills" in doc:
            return legacy_profile_to_vault(doc, at)
        vault = EvidenceVault(doc.get("subject_id"))  # type: ignore[arg-type]
        refs: dict[str, str] = {}
        for ev in doc.get("evidence") or []:
            refs[str(ev.get("ref"))] = vault.add_evidence(ev.get("kind"), ev.get("pointer"), at, sha256=ev.get("sha256"))
        for item in doc.get("claims") or []:
            missing = [r for r in item.get("evidence") or [] if r not in refs]
            if missing:
                raise RequestError("bad-profile", f"claim references unknown evidence {missing}")
            cid = vault.add_claim(item.get("type"), item.get("text"), at, skills=item.get("skills"),
                                  evidence_ids=[refs[r] for r in item.get("evidence") or []], valid_to=item.get("valid_to"))
            if item.get("visibility") in ("shareable",):
                vault.set_visibility(cid, "shareable", at)
            if item.get("conflict"):
                vault.register_conflict(cid, at, str(item["conflict"]))
    except (VaultError, AttributeError, TypeError) as exc:
        raise RequestError("bad-profile", str(exc)) from exc
    prefs = doc.get("preferences") or {}
    if not isinstance(prefs, dict):
        raise RequestError("bad-profile", "preferences must be an object")
    return vault, prefs


def _as_of(value: Any) -> str:
    try:
        return utc(value).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, TypeError, AttributeError):
        raise RequestError("bad-as-of", "as_of is required and must be a tz-aware ISO-8601 timestamp "
                                        "(lane A never reads the wall clock)") from None


def _int(value: Any, name: str, lo: int, hi: int, default: int) -> int:
    if value is None:
        return default
    try:
        number = int(str(value))
    except ValueError:
        raise RequestError("bad-parameter", f"{name} must be an integer") from None
    if not lo <= number <= hi:
        raise RequestError("bad-parameter", f"{name} must be within {lo}..{hi}")
    return number


class Service:
    def __init__(self, store: JobStore | None = None, vault: EvidenceVault | None = None,
                 preferences: dict[str, Any] | None = None) -> None:
        self.store = store or JobStore()
        self.vault = vault
        self.preferences = preferences or {}

    # ----------------------------------------------------------------- import
    def import_doc(self, doc: Any) -> dict[str, Any]:
        """Accepts one capture envelope or a bundle ``{"captures": [envelope, ...]}``."""
        envelopes = doc["captures"] if isinstance(doc, dict) and isinstance(doc.get("captures"), list) else [doc]
        if len(envelopes) > 5000:
            raise RequestError("too-many-captures", "at most 5000 captures per import")
        reports = [self.store.import_envelope(env) for env in envelopes]
        totals: dict[str, int] = {}
        for rep in reports:
            for key, value in rep["counts"].items():
                totals[key] = totals.get(key, 0) + value
        by_class: dict[str, int] = {}
        for rep in reports:
            for issue in rep["rejected"]:
                by_class[issue["error_class"]] = by_class.get(issue["error_class"], 0) + 1
        return {"api_version": API_VERSION, "captures": len(reports), "counts": totals,
                "rejected_by_class": dict(sorted(by_class.items())),
                "rejected": [i for rep in reports for i in rep["rejected"]][:500],
                "warnings": [i for rep in reports for i in rep["warnings"]][:500],
                "store": self.store.stats()}

    # ------------------------------------------------------------------ reads
    def query(self, params: dict[str, Any]) -> dict[str, Any]:
        unknown = set(params) - {"as_of", "status", "source", "family", "remote", "q", "retriever", "limit", "offset",
                                 "include_duplicates", "include_text"}
        if unknown:
            raise RequestError("bad-parameter", f"unknown parameters: {sorted(unknown)}")
        as_of = _as_of(params.get("as_of"))
        limit, offset = _int(params.get("limit"), "limit", 1, 500, 50), _int(params.get("offset"), "offset", 0, 10 ** 7, 0)
        dup = str(params.get("include_duplicates", "false")).lower() == "true"
        jobs = self.store.view(as_of, canonical_only=not dup)
        for field, getter in (("status", lambda e: e["posting"]["status"]), ("source", lambda e: e["posting"]["source"]),
                              ("family", lambda e: e["posting"].get("role_family")),
                              ("remote", lambda e: e["posting"]["remote_policy"])):
            wanted = params.get(field)
            if wanted is not None:
                jobs = [e for e in jobs if getter(e) == wanted]
        scores: dict[str, float] | None = None
        if params.get("q"):
            query = str(params["q"])[:500]
            name = params.get("retriever", "lexical")
            if name not in RETRIEVERS:
                raise RequestError("bad-parameter", f"retriever must be one of {RETRIEVERS}")
            retriever = {"lexical": LexicalRetriever, "hashed": HashedEmbeddingRetriever,
                         "hybrid": lambda: HybridRetriever([LexicalRetriever(), HashedEmbeddingRetriever()])}[name]()
            retriever.index([(e["posting"]["id"], job_document(e)) for e in jobs])
            hits = retriever.search(query, len(jobs))
            scores = dict(hits)
            by_id = {e["posting"]["id"]: e for e in jobs}
            jobs = [by_id[doc_id] for doc_id, _ in hits]
        total = len(jobs)
        keep_text = str(params.get("include_text", "false")).lower() == "true"
        items = []
        for env in jobs[offset: offset + limit]:
            ext = env["ext"] if keep_text else {k: v for k, v in env["ext"].items() if k not in LIST_FIELDS_DROPPED}
            item = {"posting": env["posting"], "ext": ext}
            if scores is not None:
                item["retrieval_score"] = scores[env["posting"]["id"]]
            items.append(item)
        return {"api_version": API_VERSION, "as_of": as_of, "total": total, "offset": offset, "limit": limit,
                "items": items, "taxonomy_version": taxonomy.taxonomy_version(),
                "notice": "status is evaluated at as_of from the last capture; lane A cannot confirm a posting is still open"}

    def job(self, job_id: str, as_of: Any) -> dict[str, Any]:
        env = self.store.get(job_id, _as_of(as_of))
        if env is None:
            raise RequestError("not-found", "no such job id", 404)
        return {"api_version": API_VERSION, **env}

    def provenance(self, job_id: str) -> dict[str, Any]:
        doc = self.store.provenance(job_id)
        if doc is None:
            raise RequestError("not-found", "no such job id", 404)
        return {"api_version": API_VERSION, **doc}

    def rank(self, body: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(body, dict):
            raise RequestError("bad-request", "rank body must be a JSON object")
        unknown = set(body) - {"as_of", "preferences", "top_n", "profile"}
        if unknown:
            raise RequestError("bad-parameter", f"unknown fields: {sorted(unknown)}")
        as_of = _as_of(body.get("as_of"))
        vault, prefs = self.vault, dict(self.preferences)
        if body.get("profile") is not None:
            vault, prefs = vault_from_profile(body["profile"], as_of)
        if vault is None:
            raise RequestError("no-profile", "no candidate profile loaded; pass 'profile' or start the service with one")
        if body.get("preferences") is not None:
            if not isinstance(body["preferences"], dict):
                raise RequestError("bad-request", "preferences must be an object")
            prefs = {**prefs, **body["preferences"]}
        top_n = body.get("top_n", 10)
        try:
            result = rank(self.store.view(as_of), vault, prefs, as_of, top_n=top_n)
        except MatchRequestError as exc:
            raise RequestError("bad-preferences", str(exc)) from exc
        schema.check(result, "a-match-result")
        return result

    def skill_vectors(self, as_of: Any) -> dict[str, Any]:
        """Representation export for lane C (atlas). Not a fit score."""
        moment = _as_of(as_of)
        items = [{"entity_id": e["posting"]["id"], "representation_type": "job",
                  "source_vector_ref": f"a-skillvec/{taxonomy.taxonomy_version()}/{e['posting']['id']}",
                  "label": e["posting"]["title"], "role_family": e["posting"].get("role_family", "unknown"),
                  "status": e["posting"]["status"],
                  **skill_vector(e["ext"]["skills_required"], e["ext"]["skills_preferred"])}
                 for e in self.store.view(moment, canonical_only=True)]
        candidate = None
        if self.vault is not None:
            proof = self.vault.skill_proof(moment)
            values = [0.0] * len(taxonomy.SKILL_IDS)
            for skill, slot in proof.items():
                if slot["status"] is not None:
                    values[taxonomy.SKILL_INDEX[skill]] = slot["weight"]
            candidate = {"entity_id": self.vault.subject_id, "representation_type": "candidate", "values": values,
                         "observed": any(values), "encoding": "evidence weight: supported=1, self-reported=0.5, unverified=0.25, none=0",
                         "source_vector_ref": f"a-skillvec/{taxonomy.taxonomy_version()}/{self.vault.subject_id}"}
        return {"api_version": API_VERSION, "as_of": moment, "vocabulary": list(taxonomy.SKILL_IDS),
                "vocabulary_version": taxonomy.taxonomy_version(), "jobs": items, "candidate": candidate,
                "warnings": ["Vectors are a bag-of-skills representation for visualization.",
                             "Distances between vectors or their 2D projections are NOT fit scores or hiring likelihoods.",
                             "observed=false means the posting listed no recognizable skills: zeros are unknown, not absent."]}

    def health(self) -> dict[str, Any]:
        return {"api_version": API_VERSION, "ok": True, "network_access": "disabled", "store": self.store.stats(),
                "profile_loaded": self.vault is not None, "taxonomy_version": taxonomy.taxonomy_version()}
