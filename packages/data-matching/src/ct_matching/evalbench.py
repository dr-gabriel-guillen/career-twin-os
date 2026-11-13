"""Frozen synthetic benchmarks: ranking quality vs latent labels, retrieval quality, throughput.

LIMITS (read before quoting any number): labels come from the generator's latent
variables and the same authored taxonomy the ranker uses, so these scores measure
*self-consistency and regression*, not real-world relevance or hiring outcomes.
"""
from __future__ import annotations

import time
from typing import Any

from . import corpus, taxonomy
from .matching import rank
from .normalize import job_id
from .retrieval import (HashedEmbeddingRetriever, HybridRetriever, LexicalRetriever, job_document, mrr, ndcg_at,
                        precision_at, recall_at)
from .service import Service, vault_from_profile
from .store import JobStore

QUERIES: tuple[tuple[str, str], ...] = (
    ("customer-facing engineer embedding with enterprise accounts to ship genai integrations", "fde"),
    ("k8s reliability on-call infrastructure as code", "devops-sre"),
    ("pre-sales demos and technical discovery for prospects", "solutions-architect"),
    ("llm evals and rag reference architectures for clients", "ai-architect"),
    ("pytorch model training and ml ops", "ml-engineer"),
    ("etl pipelines dbt warehouse modelling", "data-engineer"),
    ("threat modelling appsec iam reviews", "security-engineer"),
    ("react design systems and wcag", "frontend"),
    ("executive briefings ai roadmap for strategic accounts", "field-cto"),
    ("renewals and customer success for technical accounts", "tam"),
    ("a/b testing and statistical analysis", "data-scientist"),
    ("product roadmap and customer interviews", "product-manager"),
)
_NON_CANONICAL_TAGS = {"dup-cross-source-copy", "dup-repost-copy", "alias-same-employer"}


def _truth_by_id(bundle: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {job_id(t["source"], t["board"], t["source_id"]): t for t in bundle["truth"]["postings"]}


def latent_grade(truth: dict[str, Any], prefs: dict[str, Any], candidate_skills: set[str]) -> int:
    """Relevance grade from generator latents only (never from parsed text): 2, 1 or 0."""
    targets = {taxonomy.classify_family(t) for t in prefs.get("target_roles", [])}
    lo = taxonomy.SENIORITY_LADDER.index(prefs.get("seniority_min", "intern"))
    hi = taxonomy.SENIORITY_LADDER.index(prefs.get("seniority_max", "executive"))
    level = taxonomy.SENIORITY_LADDER.index(truth["seniority"])
    eligible = (truth["active"] and (truth["remote"] is None or truth["remote"] in prefs.get("remote", [truth["remote"]]))
                and (truth["country"] is None or truth["country"] in prefs.get("jurisdictions", [truth["country"]]))
                and (lo <= level <= hi or truth["family"] == "field-cto"))
    if not eligible:
        return 0
    if truth["family"] in targets:
        return 2
    near = any(taxonomy.family_closeness(truth["family"], t) == 0.6 for t in targets)
    overlap = len(set(truth["required"]) & candidate_skills) / max(1, len(truth["required"]))
    return 1 if near and overlap >= 0.4 else 0


def ranking_benchmark(seed: int = corpus.DEFAULT_SEED, n: int = 180, top_n: int = 20) -> dict[str, Any]:
    bundle = corpus.generate(seed, n)
    service = Service()
    service.import_doc(bundle)
    vault, prefs = vault_from_profile(corpus.SYNTHETIC_PROFILE, corpus.AS_OF)
    result = rank(service.store.view(corpus.AS_OF), vault, prefs, corpus.AS_OF, top_n=top_n)
    truth = _truth_by_id(bundle)
    skills = {s for s, slot in vault.skill_proof(corpus.AS_OF).items() if slot["status"]}
    canonical = {e["posting"]["id"] for e in service.store.view(corpus.AS_OF, canonical_only=True)}
    grades = {jid: latent_grade(t, prefs, skills) for jid, t in truth.items() if jid in canonical}
    ranked = [item["job_id"] for item in result["shortlist"]]
    relevant = {j for j, g in grades.items() if g > 0}
    strong = {j for j, g in grades.items() if g == 2}
    inactive_shown = [j for j in ranked if j in truth and not truth[j]["active"]]
    return {"benchmark": "ranking-vs-latent-labels", "seed": seed, "n_base": n, "top_n": top_n,
            "jobs_in_store": len(service.store.versions), "canonical_jobs": len(canonical),
            "eligible": result["corpus"]["eligible"], "labelled_relevant": len(relevant), "labelled_strong": len(strong),
            "precision_at_10": round(precision_at(ranked, relevant, 10), 4),
            "precision_at_10_strong": round(precision_at(ranked, strong, 10), 4),
            "recall_at_20": _r(recall_at(ranked, relevant, 20)), "ndcg_at_10": _r(ndcg_at(ranked, grades, 10)),
            "latently_inactive_in_shortlist": len(inactive_shown),
            "result_hash": result["result_hash"],
            "limits": "Labels derive from generator latents and lane A's own taxonomy; this is a self-consistency/"
                      "regression measure on fictional data, not evidence of real-world ranking quality."}


def retrieval_benchmark(seed: int = corpus.DEFAULT_SEED, n: int = 180) -> dict[str, Any]:
    bundle = corpus.generate(seed, n)
    store = JobStore()
    for env in bundle["captures"]:
        store.import_envelope(env)
    jobs = store.view(corpus.AS_OF, canonical_only=True)
    truth = _truth_by_id(bundle)
    docs = [(e["posting"]["id"], job_document(e)) for e in jobs]
    out: dict[str, Any] = {"benchmark": "retrieval-frozen-queries", "seed": seed, "n_base": n, "documents": len(docs),
                           "queries": len(QUERIES), "retrievers": {}}
    for retriever in (LexicalRetriever(), HashedEmbeddingRetriever(), HashedEmbeddingRetriever(expand=False),
                      HybridRetriever([LexicalRetriever(), HashedEmbeddingRetriever()])):
        label = retriever.name + ("" if getattr(retriever, "expand", True) else "-no-expansion")
        retriever.index(docs)
        rows = []
        for query, family in QUERIES:
            relevant = {jid for jid, _ in docs if jid in truth and truth[jid]["family"] == family}
            ranked = [d for d, _ in retriever.search(query, 20)]
            rows.append((precision_at(ranked, relevant, 5), recall_at(ranked, relevant, 20) or 0.0, mrr(ranked, relevant),
                         ndcg_at(ranked, {j: 1 for j in relevant}, 10) or 0.0))
        out["retrievers"][label] = {name: round(sum(r[i] for r in rows) / len(rows), 4)
                                    for i, name in enumerate(("precision_at_5", "recall_at_20", "mrr", "ndcg_at_10"))}
    out["limits"] = ("12 hand-written queries over fictional postings rendered from the same taxonomy surface forms; "
                     "the hashed 'embedding' is a lexical-hash stand-in, not a learned semantic model.")
    return out


def perf_benchmark(n: int, seed: int = corpus.DEFAULT_SEED) -> dict[str, Any]:
    """Wall-clock timings (machine dependent; reported, never asserted as fixed numbers)."""
    t0 = time.perf_counter()
    bundle = corpus.generate(seed, n)
    t1 = time.perf_counter()
    service = Service()
    report = service.import_doc(bundle)
    t2 = time.perf_counter()
    jobs = service.store.view(corpus.AS_OF)
    t3 = time.perf_counter()
    vault, prefs = vault_from_profile(corpus.SYNTHETIC_PROFILE, corpus.AS_OF)
    result = rank(jobs, vault, prefs, corpus.AS_OF, top_n=20)
    t4 = time.perf_counter()
    lex = LexicalRetriever()
    lex.index([(e["posting"]["id"], job_document(e)) for e in jobs])
    t5 = time.perf_counter()
    for query, _ in QUERIES:
        lex.search(query, 10)
    t6 = time.perf_counter()
    return {"benchmark": "throughput", "n_base": n, "jobs_in_store": len(service.store.versions),
            "rejected": report["counts"]["rejected"], "eligible": result["corpus"]["eligible"],
            "seconds": {"generate": round(t1 - t0, 3), "import_normalize_dedup": round(t2 - t1, 3),
                        "view_with_dedup": round(t3 - t2, 3), "rank_with_sensitivity": round(t4 - t3, 3),
                        "bm25_index": round(t5 - t4, 3), "bm25_12_queries": round(t6 - t5, 3)},
            "import_jobs_per_second": round(len(service.store.versions) / max(t2 - t1, 1e-9)),
            "note": "single-threaded pure Python on the machine that ran the command; not a service-level guarantee"}


def _r(value: float | None) -> float | None:
    return None if value is None else round(value, 4)
