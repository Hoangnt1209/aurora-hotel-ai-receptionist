"""Property-scoped hybrid pgvector policy retriever with mandatory abstention."""

import re

from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from aurora.db.models import PolicyChunk
from aurora.rag.corpus import POLICY_CORPUS
from aurora.rag.embeddings import (
    detect_language,
    embed_text,
    strip_vietnamese_accents,
)

MIN_GROUNDING_SCORE = 0.30
MIN_VECTOR_SIMILARITY = 0.26
UNGROUNDED_SPECIFIC_TERMS = (
    "helicopter",
    "helipad",
    "truc thang",
    "bitcoin",
    "cryptocurrency",
    "crypto",
    "tien ao",
    "casino",
    "gambling",
    "song bac",
    "danh bac",
    "submarine",
    "tau ngam",
    "ski",
    "snowboard",
    "jet ski",
)


class PolicyCitation(BaseModel):
    doc_id: str
    property_code: str
    section_title: str
    language: str
    content: str
    score: float


class PolicySearchResponse(BaseModel):
    query: str
    property_code: str
    language: str
    abstained: bool
    reason: str | None = None
    citations: list[PolicyCitation]


async def ingest_policy_corpus(session: AsyncSession) -> int:
    """Idempotently upsert the full bilingual policy corpus into PostgreSQL pgvector."""
    await session.execute(
        delete(PolicyChunk).where(
            PolicyChunk.chunk_metadata["source"].astext == "m1_baseline"
        )
    )
    rows = []
    for entry in POLICY_CORPUS:
        combined_for_embedding = (
            f"{entry['property_code']} {entry['section_title']} "
            f"{entry['content']} {' '.join(entry['tags'])}"
        )
        rows.append(
            {
                "property_code": entry["property_code"],
                "doc_id": entry["doc_id"],
                "section_title": entry["section_title"],
                "language": entry["language"],
                "content": entry["content"],
                "embedding": embed_text(combined_for_embedding),
                "chunk_metadata": {
                    "tags": entry["tags"],
                    "source": "aurora_policy_corpus_v1",
                },
            }
        )

    stmt = pg_insert(PolicyChunk).values(rows)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_policy_chunks_prop_doc_lang",
        set_={
            "section_title": stmt.excluded.section_title,
            "content": stmt.excluded.content,
            "embedding": stmt.excluded.embedding,
            "metadata": stmt.excluded.metadata,
        },
    )
    await session.execute(stmt)
    await session.commit()
    return len(rows)


def _token_set(text: str) -> set[str]:
    folded = strip_vietnamese_accents(text.lower())
    stop = {
        "the", "is", "are", "at", "in", "on", "for", "of", "to", "a", "an", "and", "or",
        "can", "do", "does", "how", "much", "what", "when", "with", "my", "our", "your",
        "co", "la", "o", "tai", "va", "hay", "bao", "nhieu", "khong", "duoc", "cho", "toi",
        "cua", "thi", "nao", "may", "gio", "trong", "den", "tu", "aurora", "hotel", "khach", "san",
    }
    return {t for t in re.findall(r"[a-z0-9_]+", folded) if len(t) >= 2 and t not in stop}


async def search_hotel_policy(
    session: AsyncSession,
    query: str,
    property_code: str = "GROUP",
    language: str | None = None,
    top_k: int = 3,
) -> PolicySearchResponse:
    """Search hotel policies strictly scoped to (property_code, 'GROUP') with abstention."""
    cleaned_prop = property_code.strip().upper() if property_code else "GROUP"
    if cleaned_prop not in {"GROUP", "SGN", "DAD", "PQC"}:
        cleaned_prop = "GROUP"

    target_lang = language if language in {"vi", "en"} else detect_language(query)
    allowed_props = [cleaned_prop] if cleaned_prop == "GROUP" else [cleaned_prop, "GROUP"]
    query_vec = embed_text(query)

    distance_expr = PolicyChunk.embedding.cosine_distance(query_vec).label("distance")
    stmt = (
        select(PolicyChunk, distance_expr)
        .where(
            PolicyChunk.property_code.in_(allowed_props),
            PolicyChunk.language == target_lang,
        )
        .order_by(distance_expr.asc())
        .limit(max(top_k * 4, 10))
    )
    rows = (await session.execute(stmt)).all()

    query_tokens = _token_set(query)
    scored: list[tuple[float, float, float, PolicyChunk]] = []
    for chunk, distance in rows:
        vector_sim = max(0.0, 1.0 - float(distance))
        tags = chunk.chunk_metadata.get("tags", [])
        doc_tokens = _token_set(
            f"{chunk.section_title} {chunk.content} {' '.join(tags)}"
        )
        overlap = len(query_tokens & doc_tokens)
        lexical_score = overlap / max(len(query_tokens), 1)
        raw_grounding = (0.65 * vector_sim) + (0.35 * lexical_score)
        prop_boost = 0.06 if (cleaned_prop != "GROUP" and chunk.property_code == cleaned_prop) else 0.0
        rank_score = raw_grounding + prop_boost
        scored.append((rank_score, raw_grounding, vector_sim, chunk))

    scored.sort(key=lambda item: item[0], reverse=True)
    if not scored:
        return PolicySearchResponse(
            query=query,
            property_code=cleaned_prop,
            language=target_lang,
            abstained=True,
            reason="NO_POLICY_CHUNKS_AVAILABLE",
            citations=[],
        )

    _, best_raw, best_vec, best_chunk = scored[0]
    if best_raw < MIN_GROUNDING_SCORE or best_vec < MIN_VECTOR_SIMILARITY:
        return PolicySearchResponse(
            query=query,
            property_code=cleaned_prop,
            language=target_lang,
            abstained=True,
            reason="LOW_GROUNDING_CONFIDENCE",
            citations=[],
        )

    folded_query = strip_vietnamese_accents(query.lower())
    folded_top_content = strip_vietnamese_accents(best_chunk.content.lower())
    for term in UNGROUNDED_SPECIFIC_TERMS:
        if term in folded_query and term not in folded_top_content:
            return PolicySearchResponse(
                query=query,
                property_code=cleaned_prop,
                language=target_lang,
                abstained=True,
                reason="SPECIFIC_ENTITY_NOT_IN_POLICY",
                citations=[],
            )

    citations = [
        PolicyCitation(
            doc_id=chunk.doc_id,
            property_code=chunk.property_code,
            section_title=chunk.section_title,
            language=chunk.language,
            content=chunk.content,
            score=round(raw_score, 4),
        )
        for _, raw_score, vec_sim, chunk in scored[:top_k]
        if raw_score >= (MIN_GROUNDING_SCORE * 0.72) and vec_sim >= (MIN_VECTOR_SIMILARITY * 0.70)
    ]

    if not citations:
        return PolicySearchResponse(
            query=query,
            property_code=cleaned_prop,
            language=target_lang,
            abstained=True,
            reason="LOW_GROUNDING_CONFIDENCE",
            citations=[],
        )

    return PolicySearchResponse(
        query=query,
        property_code=cleaned_prop,
        language=target_lang,
        abstained=False,
        citations=citations,
    )
