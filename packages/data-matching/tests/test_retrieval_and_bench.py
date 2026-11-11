import json

import pytest

from a_testkit import AS_OF, PKG
from ct_matching import evalbench, taxonomy
from ct_matching.retrieval import (AdapterRetriever, HashedEmbeddingRetriever, HybridRetriever, LexicalRetriever,
                                   expand_with_taxonomy, job_document, mrr, ndcg_at, precision_at, recall_at,
                                   skill_vector, tokenize)

DOCS = [("a", "Senior Site Reliability Engineer kubernetes terraform on-call"),
        ("b", "Frontend Engineer react typescript accessibility"),
        ("c", "Data Engineer sql etl pipelines"),
        ("d", "Kubernetes platform engineer"),
        ("e", "")]


@pytest.mark.parametrize("factory", [LexicalRetriever, HashedEmbeddingRetriever,
                                     lambda: HybridRetriever([LexicalRetriever(), HashedEmbeddingRetriever()])])
def test_retrievers_rank_relevant_first_and_are_deterministic(factory):
    r1, r2 = factory(), factory()
    r1.index(DOCS)
    r2.index(list(DOCS))
    hits = r1.search("kubernetes terraform", 3)
    assert hits[0][0] == "a" and hits == r2.search("kubernetes terraform", 3)
    assert all(score > 0 for _, score in hits) and "e" not in [d for d, _ in r1.search("kubernetes", 5)]


@pytest.mark.parametrize("factory", [LexicalRetriever, HashedEmbeddingRetriever])
def test_empty_query_empty_index_and_no_match(factory):
    r = factory()
    r.index([])
    assert r.search("anything", 5) == []
    r.index(DOCS)
    assert r.search("", 5) == [] and r.search("   ​ ", 5) == []
    assert factory is not LexicalRetriever or r.search("zzzzqqq", 5) == []


def test_lexical_baseline_has_no_synonym_knowledge_but_expansion_does():
    lex, emb = LexicalRetriever(), HashedEmbeddingRetriever()
    lex.index(DOCS)
    emb.index(DOCS)
    assert lex.search("k8s", 5) == []
    assert emb.search("k8s", 2)[0][0] in ("a", "d")
    assert "kubernetes" in expand_with_taxonomy("we run k8s").split()


def test_ties_break_by_id():
    lex = LexicalRetriever()
    lex.index([("z", "python"), ("y", "python"), ("x", "python")])
    assert [d for d, _ in lex.search("python", 3)] == ["x", "y", "z"]


def test_tokenize_folds_and_keeps_tech_tokens():
    assert tokenize("The C++/C# and Node.js on-call rotation, CI/CD!") == ["c++/c#", "node.js", "on-call", "rotation", "ci/cd"]


def test_adapter_retriever_requires_offline_declaration():
    class Remote:
        offline = False

        def embed(self, texts):
            raise AssertionError("must never be called")

    class Local:
        offline = True

        def embed(self, texts):
            return [[float(len(t)), float(t.count("k")), 1.0] for t in texts]

    with pytest.raises(PermissionError):
        AdapterRetriever(Remote())
    with pytest.raises(PermissionError):
        AdapterRetriever(object())  # no declaration at all
    r = AdapterRetriever(Local())
    r.index(DOCS)
    assert len(r.search("kubernetes", 3)) == 3


def test_metrics():
    ranked, relevant = ["a", "x", "b", "y"], {"a", "b", "c"}
    assert precision_at(ranked, relevant, 2) == 0.5 and recall_at(ranked, relevant, 4) == pytest.approx(2 / 3)
    assert mrr(["x", "a"], relevant) == 0.5 and mrr(["x"], relevant) == 0.0
    assert recall_at(ranked, set(), 3) is None and ndcg_at(ranked, {}, 3) is None
    assert ndcg_at(["a", "b"], {"a": 2, "b": 1}, 2) == pytest.approx(1.0)
    assert ndcg_at(["b", "a"], {"a": 2, "b": 1}, 2) < 1.0


def test_skill_vector_encoding_and_unknown_mask():
    vec = skill_vector(["python"], ["aws", "not-a-skill"])
    assert vec["dimensions"] == len(taxonomy.SKILL_IDS) == len(vec["values"]) and vec["observed"] is True
    assert vec["values"][taxonomy.SKILL_INDEX["python"]] == 1.0 and vec["values"][taxonomy.SKILL_INDEX["aws"]] == 0.5
    assert sum(vec["values"]) == 1.5
    empty = skill_vector([], [])
    assert empty["observed"] is False and not any(empty["values"])
    both = skill_vector(["aws"], ["aws"])
    assert both["values"][taxonomy.SKILL_INDEX["aws"]] == 1.0


def test_skill_vector_export_for_atlas_is_contract_shaped(service):
    out = service.skill_vectors(AS_OF)
    assert out["vocabulary"] == list(taxonomy.SKILL_IDS) and out["vocabulary_version"] == taxonomy.taxonomy_version()
    assert any("NOT fit scores" in w for w in out["warnings"])
    ids = {j["entity_id"] for j in out["jobs"]}
    assert len(ids) == len(out["jobs"]) == len(service.store.view(AS_OF, canonical_only=True))
    for job in out["jobs"]:
        assert job["representation_type"] == "job" and job["source_vector_ref"].endswith(job["entity_id"])
        assert set(job["values"]) <= {0.0, 0.5, 1.0}
    cand = out["candidate"]
    assert cand["representation_type"] == "candidate" and set(cand["values"]) <= {0.0, 0.25, 0.5, 1.0}
    assert cand["values"][taxonomy.SKILL_INDEX["deep-learning"]] == 0.0  # contradicted claim is not a skill
    assert cand["values"][taxonomy.SKILL_INDEX["compliance"]] == 0.0     # expired credential is not a skill


def test_job_document_uses_only_sanitized_fields(service, by_tag):
    doc = job_document(by_tag("poisoned-html-injection")[0])
    assert "ignore all previous" not in doc.lower() and "<" not in doc


# ------------------------------------------------------------------ benchmarks
FROZEN = json.loads((PKG / "benchmarks" / "frozen_metrics.json").read_text(encoding="utf-8"))


def test_ranking_benchmark_matches_frozen_baseline_exactly():
    got = evalbench.ranking_benchmark()
    assert got == FROZEN["ranking"], "ranking behaviour changed; re-freeze deliberately with tools/freeze_benchmarks.py"
    assert got["latently_inactive_in_shortlist"] == 0
    assert "not evidence of real-world ranking quality" in got["limits"]


def test_retrieval_benchmark_matches_frozen_baseline_exactly():
    got = evalbench.retrieval_benchmark()
    assert got == FROZEN["retrieval"]
    lex, emb = got["retrievers"]["lexical-bm25"], got["retrievers"]["hashed-embedding-offline"]
    assert emb["ndcg_at_10"] > lex["ndcg_at_10"], "comparison direction is part of the documented finding"


def test_benchmarks_hold_on_other_seeds_without_safety_violations():
    for seed in (1, 2, 3):
        got = evalbench.ranking_benchmark(seed=seed, n=120)
        assert got["latently_inactive_in_shortlist"] == 0, seed
        assert got["precision_at_10"] >= 0.5, (seed, got)


def test_perf_benchmark_smoke_and_scaling_shape():
    small = evalbench.perf_benchmark(200)
    assert small["jobs_in_store"] >= 200 and small["rejected"] == 10 and small["eligible"] > 0
    assert small["seconds"]["import_normalize_dedup"] < 30
