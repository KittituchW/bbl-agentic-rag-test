"""Hybrid retrieval over a local text knowledge base.

Pipeline:
    knowledge_base.txt
        -> paragraph chunking
        -> BM25 (hand-rolled, lexical)  +  FastEmbed MiniLM (semantic)
        -> Reciprocal Rank Fusion (keyed by chunk id, so dedup is implicit)
        -> no-answer check
        -> top-k chunks

The semantic side degrades gracefully: if the embedding model cannot be
loaded (e.g. offline on first run), retrieval falls back to BM25 only.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------
BM25_K1 = 1.5
BM25_B = 0.75
RRF_K = 60  # standard constant from the RRF paper (Cormack et al., 2009)
TOP_K = 3
# Conservative no-answer thresholds (empirical, model-specific — see README):
MIN_COSINE = 0.25  # below this, the semantic match is considered noise
MIN_QUERY_TERMS_FOR_OVERLAP_CHECK = 3
MIN_LEXICAL_OVERLAP = 2
NO_ANSWER_MARKER = "NO_RELEVANT_INFORMATION"

STOPWORDS = frozenset(
    "a an and are as at be by for from has have in is it its of on or that the "
    "this to was were what when where which who will with "
    "about ask can could did do does how i if me must my need needed our please "
    "should tell we would you your "
    "company employee policy".split()
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _light_stem(token: str) -> str:
    """Very light plural stemming so 'password' matches 'passwords'.

    Deliberately crude (no Porter stemmer dependency); applied identically
    to queries and documents, so occasional over-stripping is harmless.
    """
    if len(token) > 3 and token.endswith("es") and not token.endswith("ses"):
        return token[:-2]
    if len(token) > 3 and token.endswith("s"):
        return token[:-1]
    return token


def tokenize(text: str) -> list[str]:
    """Lowercase, strip punctuation, drop stopwords, light plural stemming."""
    tokens = []
    for raw_token in _TOKEN_RE.findall(text.lower()):
        token = _light_stem(raw_token)
        if raw_token not in STOPWORDS and token not in STOPWORDS:
            tokens.append(token)
    return tokens


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------
@dataclass
class Chunk:
    chunk_id: int
    text: str
    tokens: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.tokens:
            self.tokens = tokenize(self.text)


def load_chunks(path: str) -> list[Chunk]:
    """Split the knowledge base into paragraph chunks (blank-line separated)."""
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", raw) if p.strip()]
    return [Chunk(chunk_id=i, text=p) for i, p in enumerate(paragraphs)]


# ---------------------------------------------------------------------------
# Lexical retrieval: hand-rolled BM25 (Okapi)
# ---------------------------------------------------------------------------
def bm25_scores(query: str, chunks: list[Chunk]) -> list[float]:
    """Score every chunk against the query with Okapi BM25.

    score(q, d) = sum over query terms t of:
        idf(t) * tf(t, d) * (k1 + 1) / (tf(t, d) + k1 * (1 - b + b * |d| / avgdl))
    """
    query_terms = tokenize(query)
    n = len(chunks)
    avgdl = sum(len(c.tokens) for c in chunks) / max(n, 1)

    # Document frequency per query term.
    df = {
        t: sum(1 for c in chunks if t in c.tokens)
        for t in set(query_terms)
    }

    scores: list[float] = []
    for chunk in chunks:
        dl = len(chunk.tokens)
        score = 0.0
        for term in query_terms:
            tf = chunk.tokens.count(term)
            if tf == 0:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            score += idf * tf * (BM25_K1 + 1) / (
                tf + BM25_K1 * (1 - BM25_B + BM25_B * dl / avgdl)
            )
        scores.append(score)
    return scores


# ---------------------------------------------------------------------------
# Semantic retrieval: FastEmbed (local MiniLM), optional
# ---------------------------------------------------------------------------
class SemanticIndex:
    """Embeds chunks once at construction; scores queries by cosine similarity.

    If the embedding model cannot be loaded, `available` is False and
    `scores()` returns None so callers can fall back to BM25 only.
    """

    MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"

    def __init__(self, chunks: list[Chunk]) -> None:
        self.available = False
        self._chunk_vecs: list[list[float]] = []
        try:
            from fastembed import TextEmbedding

            self._model = TextEmbedding(model_name=self.MODEL_NAME)
            # embed() is the *document* path; query_embed() is the *query*
            # path — some models apply different prefixes to each.
            self._chunk_vecs = [
                self._normalize(v) for v in self._model.embed([c.text for c in chunks])
            ]
            self.available = True
        except Exception as exc:  # pragma: no cover - environment dependent
            logger.warning(
                "Semantic retrieval unavailable (%s); falling back to BM25 only.", exc
            )

    @staticmethod
    def _normalize(vec) -> list[float]:
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]

    def scores(self, query: str) -> list[float] | None:
        if not self.available:
            return None
        embed_query = getattr(self._model, "query_embed", self._model.embed)
        query_vec = self._normalize(next(iter(embed_query(query))))
        return [
            sum(a * b for a, b in zip(query_vec, chunk_vec))
            for chunk_vec in self._chunk_vecs
        ]


# ---------------------------------------------------------------------------
# Fusion + no-answer check
# ---------------------------------------------------------------------------
def _ranks(scores: list[float]) -> dict[int, int]:
    """Map chunk_id -> 1-based rank (best score first)."""
    order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    return {chunk_id: rank + 1 for rank, chunk_id in enumerate(order)}


def _lexical_match_is_credible(
    query_terms: list[str], chunks: list[Chunk], bm25: list[float]
) -> bool:
    """Avoid treating a single generic word in a noisy query as relevant."""
    best_bm25 = max(bm25, default=0.0)
    if best_bm25 == 0.0:
        return False

    unique_query_terms = set(query_terms)
    if len(unique_query_terms) < MIN_QUERY_TERMS_FOR_OVERLAP_CHECK:
        return True

    best_chunk_id = max(range(len(bm25)), key=bm25.__getitem__)
    overlap = unique_query_terms & set(chunks[best_chunk_id].tokens)
    return len(overlap) >= MIN_LEXICAL_OVERLAP


@dataclass
class RetrievalResult:
    chunk: Chunk
    rrf_score: float
    bm25_score: float
    cosine: float | None
    found_by: list[str]


def hybrid_search(
    query: str,
    chunks: list[Chunk],
    semantic_index: SemanticIndex | None = None,
    top_k: int = TOP_K,
) -> list[RetrievalResult] | str:
    """Fuse BM25 and semantic rankings with RRF; return top-k results.

    Returns NO_ANSWER_MARKER when neither retriever produces a credible
    signal: the BM25 match is too weak AND the best cosine similarity is below
    MIN_COSINE (or semantic is unavailable).
    """
    query_terms = tokenize(query)
    bm25 = bm25_scores(query, chunks)
    cosine = semantic_index.scores(query) if semantic_index else None

    best_cosine = max(cosine) if cosine else None
    lexical_is_credible = _lexical_match_is_credible(query_terms, chunks, bm25)
    semantic_is_credible = best_cosine is not None and best_cosine >= MIN_COSINE
    if not lexical_is_credible and not semantic_is_credible:
        return NO_ANSWER_MARKER

    # Reciprocal Rank Fusion, keyed by chunk id — a chunk found by both
    # retrievers accumulates both contributions, so dedup is implicit.
    fused: dict[int, float] = {c.chunk_id: 0.0 for c in chunks}
    rankings = [("bm25", _ranks(bm25))]
    if cosine is not None:
        rankings.append(("semantic", _ranks(cosine)))
    for _, ranks in rankings:
        for chunk_id, rank in ranks.items():
            fused[chunk_id] += 1.0 / (RRF_K + rank)

    # RRF assigns a rank to *every* chunk, so slicing the fused ranking to
    # top_k would pad the result with chunks no retriever actually matched.
    # Filter first, then slice: a chunk must carry a real signal (non-zero
    # BM25, or cosine above the floor) to reach the generator's context.
    results = []
    for chunk_id in sorted(fused, key=fused.get, reverse=True):
        found_by = []
        if bm25[chunk_id] > 0:
            found_by.append("bm25")
        if cosine is not None and cosine[chunk_id] >= MIN_COSINE:
            found_by.append("semantic")
        if not found_by:
            continue
        results.append(
            RetrievalResult(
                chunk=chunks[chunk_id],
                rrf_score=fused[chunk_id],
                bm25_score=bm25[chunk_id],
                cosine=cosine[chunk_id] if cosine is not None else None,
                found_by=found_by,
            )
        )
        if len(results) == top_k:
            break
    # Credibility check above passed, so this is defensive rather than
    # expected — but an empty result set is a no-answer, not an empty answer.
    return results or NO_ANSWER_MARKER


def format_results(results: list[RetrievalResult] | str) -> str:
    """Render results as the raw-snippet payload the Data Retriever returns."""
    if results == NO_ANSWER_MARKER:
        return NO_ANSWER_MARKER
    lines = []
    for i, r in enumerate(results, 1):
        cos = f"{r.cosine:.3f}" if r.cosine is not None else "n/a"
        lines.append(
            f"[Snippet {i} | chunk {r.chunk.chunk_id} | "
            f"bm25={r.bm25_score:.2f} cosine={cos} | via {'+'.join(r.found_by)}]\n"
            f"{r.chunk.text}"
        )
    return "\n\n".join(lines)
