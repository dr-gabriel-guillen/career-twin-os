"""Retrieval interface: deterministic lexical baseline + optional *offline* embedding adapters.

No retriever here calls a network service. ``HashedEmbeddingRetriever`` is a local,
dependency-free stand-in for a semantic model (hashed word/character n-grams plus
taxonomy synonym expansion); a real local model can be plugged in through
``AdapterRetriever`` as long as it declares ``offline = True``.
"""
from __future__ import annotations

import math
import re
import zlib
from typing import Any, Protocol, Sequence

from ct_connectors.textsafe import fold

from . import taxonomy

_STOP = frozenset("a an and are as at be by for from in is it of on or our that the this to we with you your will "
                  "have has their they us can".split())
_TOKEN = re.compile(r"[a-z0-9][a-z0-9+#./-]*")


def tokenize(text: str) -> list[str]:
    return [t.strip("./-") for t in _TOKEN.findall(fold(text)) if t.strip("./-") and t.strip("./-") not in _STOP]


def job_document(env: dict[str, Any]) -> str:
    p, e = env["posting"], env["ext"]
    return " \n".join([p["title"]] * 3 + [" ".join(p["skills"])] * 2 + [p.get("role_family", ""), e["description_text"]])


class Retriever(Protocol):
    name: str

    def index(self, docs: Sequence[tuple[str, str]]) -> None: ...

    def search(self, query: str, k: int = 10) -> list[tuple[str, float]]: ...


def _top(scores: dict[str, float], k: int) -> list[tuple[str, float]]:
    ranked = sorted(((doc, s) for doc, s in scores.items() if s > 0), key=lambda t: (-round(t[1], 12), t[0]))
    return [(doc, round(s, 6)) for doc, s in ranked[:k]]


class LexicalRetriever:
    """Okapi BM25 over word tokens. No stemming, no synonyms: the honest baseline."""

    name = "lexical-bm25"

    def __init__(self, k1: float = 1.2, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.postings: dict[str, list[tuple[int, int]]] = {}
        self.lengths: list[int] = []
        self.ids: list[str] = []

    def index(self, docs: Sequence[tuple[str, str]]) -> None:
        self.postings, self.lengths, self.ids = {}, [], []
        for idx, (doc_id, text) in enumerate(docs):
            counts: dict[str, int] = {}
            tokens = tokenize(text)
            for tok in tokens:
                counts[tok] = counts.get(tok, 0) + 1
            for tok, n in counts.items():
                self.postings.setdefault(tok, []).append((idx, n))
            self.lengths.append(len(tokens))
            self.ids.append(doc_id)
        self.avg = (sum(self.lengths) / len(self.lengths)) if self.lengths else 0.0

    def search(self, query: str, k: int = 10) -> list[tuple[str, float]]:
        n = len(self.ids)
        scores: dict[str, float] = {}
        for tok in sorted(set(tokenize(query))):
            plist = self.postings.get(tok)
            if not plist:
                continue
            idf = math.log(1 + (n - len(plist) + 0.5) / (len(plist) + 0.5))
            for idx, tf in plist:
                norm = tf + self.k1 * (1 - self.b + self.b * self.lengths[idx] / (self.avg or 1))
                scores[self.ids[idx]] = scores.get(self.ids[idx], 0.0) + idf * tf * (self.k1 + 1) / norm
        return _top(scores, k)


def expand_with_taxonomy(text: str) -> str:
    """Append canonical skill ids and family names for any surface forms found ("k8s" → kubernetes)."""
    extra = taxonomy.find_skills(text)
    family = taxonomy.classify_family(text)
    return text + " \n" + " ".join(extra + ([family] if family != "unknown" else []))


class HashedEmbeddingRetriever:
    """Local 'semantic-lite' vectors: hashed word uni/bi-grams + char 4-grams, idf-weighted, cosine."""

    name = "hashed-embedding-offline"
    offline = True

    def __init__(self, dim: int = 512, expand: bool = True) -> None:
        self.dim, self.expand = dim, expand
        self.vectors: list[dict[int, float]] = []
        self.ids: list[str] = []
        self.idf: dict[int, float] = {}

    def _features(self, text: str) -> dict[int, float]:
        if self.expand:
            text = expand_with_taxonomy(text)
        tokens = tokenize(text)
        grams = list(tokens) + [a + "_" + b for a, b in zip(tokens, tokens[1:])]
        for tok in tokens:
            padded = f"^{tok}$"
            grams += ["#" + padded[i:i + 4] for i in range(max(1, len(padded) - 3))]
        counts: dict[int, float] = {}
        for gram in grams:
            h = zlib.crc32(gram.encode("utf-8"))
            slot = h % self.dim
            counts[slot] = counts.get(slot, 0.0) + (1.0 if (h >> 16) & 1 else -1.0)
        return {slot: math.copysign(1 + math.log(abs(v)), v) for slot, v in counts.items() if v}

    @staticmethod
    def _normalize(vec: dict[int, float]) -> dict[int, float]:
        norm = math.sqrt(sum(v * v for v in vec.values()))
        return {k: v / norm for k, v in vec.items()} if norm else {}

    def index(self, docs: Sequence[tuple[str, str]]) -> None:
        raw = [self._features(text) for _, text in docs]
        self.ids = [doc_id for doc_id, _ in docs]
        df: dict[int, int] = {}
        for vec in raw:
            for slot in vec:
                df[slot] = df.get(slot, 0) + 1
        n = len(raw)
        self.idf = {slot: math.log(1 + n / count) for slot, count in df.items()}
        self.vectors = [self._normalize({s: v * self.idf[s] for s, v in vec.items()}) for vec in raw]

    def embed_query(self, query: str) -> dict[int, float]:
        return self._normalize({s: v * self.idf.get(s, 0.0) for s, v in self._features(query).items()})

    def search(self, query: str, k: int = 10) -> list[tuple[str, float]]:
        q = self.embed_query(query)
        scores = {}
        for doc_id, vec in zip(self.ids, self.vectors):
            small, large = (q, vec) if len(q) < len(vec) else (vec, q)
            scores[doc_id] = sum(v * large.get(s, 0.0) for s, v in small.items())
        return _top(scores, k)


class EmbeddingAdapter(Protocol):
    offline: bool

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class AdapterRetriever:
    """Wrap any local embedding model. Refuses adapters that do not declare ``offline = True``."""

    def __init__(self, adapter: EmbeddingAdapter, name: str = "adapter-embedding") -> None:
        if getattr(adapter, "offline", None) is not True:
            raise PermissionError("embedding adapter must declare offline=True; remote/paid inference is not permitted")
        self.adapter, self.name = adapter, name
        self.ids: list[str] = []
        self.vectors: list[list[float]] = []

    @staticmethod
    def _unit(vec: Sequence[float]) -> list[float]:
        norm = math.sqrt(sum(v * v for v in vec))
        return [v / norm for v in vec] if norm else [0.0 for _ in vec]

    def index(self, docs: Sequence[tuple[str, str]]) -> None:
        self.ids = [d for d, _ in docs]
        self.vectors = [self._unit(v) for v in self.adapter.embed([t for _, t in docs])]

    def search(self, query: str, k: int = 10) -> list[tuple[str, float]]:
        q = self._unit(self.adapter.embed([query])[0])
        return _top({d: sum(a * b for a, b in zip(q, v)) for d, v in zip(self.ids, self.vectors)}, k)


class HybridRetriever:
    """Reciprocal-rank fusion of several retrievers (deterministic, score-scale free)."""

    def __init__(self, parts: Sequence[Retriever], rrf_k: int = 60, depth: int = 100) -> None:
        self.parts, self.rrf_k, self.depth = list(parts), rrf_k, depth
        self.name = "hybrid-rrf(" + "+".join(p.name for p in self.parts) + ")"

    def index(self, docs: Sequence[tuple[str, str]]) -> None:
        for part in self.parts:
            part.index(docs)

    def search(self, query: str, k: int = 10) -> list[tuple[str, float]]:
        fused: dict[str, float] = {}
        for part in self.parts:
            for pos, (doc_id, _) in enumerate(part.search(query, self.depth)):
                fused[doc_id] = fused.get(doc_id, 0.0) + 1.0 / (self.rrf_k + pos + 1)
        return _top(fused, k)


# --------------------------------------------------------------------- metrics
def precision_at(ranked: Sequence[str], relevant: set[str], k: int) -> float:
    return sum(1 for d in ranked[:k] if d in relevant) / k if k else 0.0


def recall_at(ranked: Sequence[str], relevant: set[str], k: int) -> float | None:
    return (sum(1 for d in ranked[:k] if d in relevant) / len(relevant)) if relevant else None


def mrr(ranked: Sequence[str], relevant: set[str]) -> float:
    for pos, doc in enumerate(ranked):
        if doc in relevant:
            return 1.0 / (pos + 1)
    return 0.0


def ndcg_at(ranked: Sequence[str], grades: dict[str, int], k: int) -> float | None:
    ideal = sorted((g for g in grades.values() if g > 0), reverse=True)[:k]
    if not ideal:
        return None
    dcg = sum((2 ** grades.get(d, 0) - 1) / math.log2(i + 2) for i, d in enumerate(ranked[:k]))
    return dcg / sum((2 ** g - 1) / math.log2(i + 2) for i, g in enumerate(ideal))


def skill_vector(skills_required: Sequence[str], skills_preferred: Sequence[str] = ()) -> dict[str, Any]:
    """Fixed-order skill vector for lane C. 1=required, 0.5=preferred, 0=not listed.

    ``observed`` is False when the posting listed no recognizable skills at all, in which
    case the zeros mean "unknown", not "absent". Distances between these vectors are a
    representation for visualization, never a fit score.
    """
    vec = [0.0] * len(taxonomy.SKILL_IDS)
    for s in skills_preferred:
        if s in taxonomy.SKILL_INDEX:
            vec[taxonomy.SKILL_INDEX[s]] = 0.5
    for s in skills_required:
        if s in taxonomy.SKILL_INDEX:
            vec[taxonomy.SKILL_INDEX[s]] = 1.0
    return {"vocabulary_version": taxonomy.taxonomy_version(), "dimensions": len(vec), "values": vec,
            "observed": any(vec), "encoding": "1=required,0.5=preferred,0=not-listed"}
