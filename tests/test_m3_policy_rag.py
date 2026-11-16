"""Milestone 3 verification tests: pgvector RAG ingestion, Recall@3, zero property leak, abstention."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aurora.db.models import PolicyChunk
from aurora.rag.retriever import ingest_policy_corpus, search_hotel_policy

GOLDEN_POLICY_EVAL_SET: list[tuple[str, str, str, str]] = [
    # (query, property_code, expected_language, expected_doc_id)
    ("What is the cancellation policy for FLEX_24H bookings?", "SGN", "en", "group_cancellation_policy"),
    ("Gói không hoàn hủy NON_REF có được tự hủy không?", "DAD", "vi", "group_cancellation_policy"),
    ("Do you provide baby cribs or extra beds for children?", "DAD", "en", "group_child_extrabed_policy"),
    ("Phí giường phụ cho trẻ em là bao nhiêu tiền một đêm?", "PQC", "vi", "group_child_extrabed_policy"),
    ("Can I bring my pet dog to the hotel?", "PQC", "en", "group_pet_smoking_policy"),
    ("Khách sạn có cho hút thuốc lá trong phòng không?", "SGN", "vi", "group_pet_smoking_policy"),
    ("How much is the security deposit at check-in?", "SGN", "en", "group_payment_deposit_policy"),
    ("Khi nhận phòng có cần đóng tiền cọc không?", "DAD", "vi", "group_payment_deposit_policy"),
    ("Can I book 4 rooms together through the AI?", "SGN", "en", "group_booking_policy"),
    ("Tôi muốn đặt đoàn 5 phòng cho công ty", "PQC", "vi", "group_booking_policy"),
    ("How much is the Tan Son Nhat airport sedan transfer?", "SGN", "en", "sgn_checkin_shuttle"),
    ("Xe đón sân bay Tân Sơn Nhất giá bao nhiêu?", "SGN", "vi", "sgn_checkin_shuttle"),
    ("What time is breakfast at Aurora Saigon Central?", "SGN", "en", "sgn_breakfast_facilities"),
    ("Ở Sài Gòn mấy giờ phục vụ bữa sáng và phòng gym?", "SGN", "vi", "sgn_breakfast_facilities"),
    ("How much is the airport car transfer in Da Nang?", "DAD", "en", "dad_checkin_shuttle"),
    ("Xe đón sân bay Đà Nẵng bao nhiêu tiền một lượt?", "DAD", "vi", "dad_checkin_shuttle"),
    ("Are beach loungers and infinity pool included in Da Nang?", "DAD", "en", "dad_breakfast_beach"),
    ("Bãi biển Mỹ Khê có miễn phí ghế tắm nắng và hồ bơi không?", "DAD", "vi", "dad_breakfast_beach"),
    ("Is the Phu Quoc airport shuttle complimentary and when is check-in?", "PQC", "en", "pqc_checkin_shuttle"),
    ("Phú Quốc có xe đón sân bay miễn phí và nhận phòng lúc mấy giờ?", "PQC", "vi", "pqc_checkin_shuttle"),
]

UNANSWERABLE_TRAP_QUERIES: list[tuple[str, str]] = [
    ("Do you have a rooftop helicopter landing pad?", "SGN"),
    ("Can I pay my hotel room bill using Bitcoin cryptocurrency?", "DAD"),
    ("Khách sạn có sòng bạc casino và máy đánh bạc không?", "PQC"),
    ("What is the price of private submarine rental?", "PQC"),
    ("Do you offer ski lift passes and snowboarding gear?", "SGN"),
]


async def test_ingest_policy_corpus_is_idempotent(db_session: AsyncSession) -> None:
    """Ingesting the bilingual corpus twice stores exactly 24 policy chunks."""
    count1 = await ingest_policy_corpus(db_session)
    count2 = await ingest_policy_corpus(db_session)
    total_in_db = await db_session.scalar(select(func.count()).select_from(PolicyChunk))

    assert count1 == 24
    assert count2 == 24
    assert total_in_db == 24


async def test_rag_golden_eval_recall_and_zero_cross_property_leakage(
    db_session: AsyncSession,
) -> None:
    """Golden 20-query eval must hit Recall@3 >= 90% and 0% cross-property leakage."""
    await ingest_policy_corpus(db_session)

    hits = 0
    for query, prop_code, expected_lang, expected_doc in GOLDEN_POLICY_EVAL_SET:
        res = await search_hotel_policy(
            db_session, query=query, property_code=prop_code, top_k=3
        )
        assert res.abstained is False, f"Unexpected abstention on: {query}"
        assert res.language == expected_lang
        # Zero cross-property leakage check
        for cit in res.citations:
            assert cit.property_code in {prop_code, "GROUP"}, (
                f"Leakage: query for {prop_code} returned {cit.property_code}"
            )
        returned_docs = [cit.doc_id for cit in res.citations]
        if expected_doc in returned_docs:
            hits += 1

    recall_at_3 = hits / len(GOLDEN_POLICY_EVAL_SET)
    assert recall_at_3 >= 0.90, f"Recall@3 too low: {recall_at_3:.2%}"


async def test_rag_abstains_on_out_of_scope_questions(db_session: AsyncSession) -> None:
    """Unanswerable policy traps must abstain 100% of the time without fabricating answers."""
    await ingest_policy_corpus(db_session)

    for query, prop_code in UNANSWERABLE_TRAP_QUERIES:
        res = await search_hotel_policy(
            db_session, query=query, property_code=prop_code, top_k=3
        )
        assert res.abstained is True, f"Failed to abstain on trap query: {query}"
        assert res.citations == []
