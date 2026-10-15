from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable


@dataclass(frozen=True, slots=True)
class RetrievalDocument:
    document_id: str
    project_id: str
    role_scope: str
    content: str
    occurred_at: datetime
    confidence: float
    source_refs: tuple[str, ...]
    status: str = "active"
    valid_to: datetime | None = None

    def __post_init__(self) -> None:
        if not self.source_refs:
            raise ValueError("retrieval document requires provenance")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("retrieval confidence must be between zero and one")


@dataclass(frozen=True, slots=True)
class RetrievalHit:
    document: RetrievalDocument
    score: float
    component_ranks: dict[str, int]


class RetrievalPipeline:
    _RRF_K = 60
    _WEIGHTS = {"bm25": 1.0, "temporal": 2.0, "confidence": 0.5}

    def search(
        self, query: str, project_id: str, role_id: str, as_of: datetime,
        documents: Iterable[RetrievalDocument], limit: int,
    ) -> list[RetrievalHit]:
        if limit < 0:
            raise ValueError("limit must be non-negative")
        query_terms = self._tokens(query)
        if not query_terms or limit == 0:
            return []
        eligible = [
            document
            for document in documents
            if document.project_id == project_id
            and document.status == "active"
            and (document.role_scope == "shared" or document.role_scope == role_id)
            and document.occurred_at <= as_of
            and (document.valid_to is None or document.valid_to > as_of)
        ]
        if not eligible:
            return []
        eligible.sort(key=lambda document: document.document_id)
        bm25 = self._bm25(query_terms, eligible)
        eligible = [document for document in eligible if bm25[document.document_id] > 0]
        if not eligible:
            return []
        components = {
            "bm25": bm25,
            "temporal": {
                document.document_id: document.occurred_at.timestamp() for document in eligible
            },
            "confidence": {
                document.document_id: document.confidence for document in eligible
            },
        }
        ranks = {name: self._rank(scores, eligible) for name, scores in components.items()}
        hits = []
        for document in eligible:
            component_ranks = {name: values[document.document_id] for name, values in ranks.items()}
            score = sum(
                self._WEIGHTS[name] / (self._RRF_K + rank)
                for name, rank in component_ranks.items()
            )
            hits.append(RetrievalHit(document, score, component_ranks))
        hits.sort(key=lambda hit: (-hit.score, hit.document.document_id))
        return hits[:limit]

    def _bm25(
        self, query_terms: tuple[str, ...], documents: list[RetrievalDocument]
    ) -> dict[str, float]:
        terms_by_document = {
            document.document_id: self._tokens(document.content) for document in documents
        }
        average_length = sum(map(len, terms_by_document.values())) / len(documents)
        document_frequency = {
            term: sum(term in terms for terms in terms_by_document.values())
            for term in set(query_terms)
        }
        scores: dict[str, float] = {}
        for document in documents:
            terms = terms_by_document[document.document_id]
            counts = Counter(terms)
            score = 0.0
            for term in query_terms:
                frequency = counts[term]
                if frequency == 0:
                    continue
                idf = math.log(1 + (len(documents) - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5))
                normalization = frequency + 1.5 * (1 - 0.75 + 0.75 * len(terms) / max(average_length, 1))
                score += idf * (frequency * 2.5) / normalization
            scores[document.document_id] = score
        return scores

    @staticmethod
    def _rank(
        scores: dict[str, float], documents: list[RetrievalDocument]
    ) -> dict[str, int]:
        ordered_scores = sorted({scores[document.document_id] for document in documents}, reverse=True)
        rank_by_score = {score: index + 1 for index, score in enumerate(ordered_scores)}
        return {document.document_id: rank_by_score[scores[document.document_id]] for document in documents}

    @staticmethod
    def _tokens(text: str) -> tuple[str, ...]:
        return tuple(re.findall(r"[a-z0-9_]+", text.casefold()))
