---
name: langgraph-receptionist-rag
description: >-
  Guides implementation and evaluation of the Aurora LangGraph stateful receptionist,
  structured Pydantic tool wrappers, confirmation and identity verification gates,
  pgvector metadata-filtered hotel policy RAG, and escalation handoff. Use for Milestone 3 or Milestone 4.
---

# LangGraph AI Receptionist & Grounded Policy RAG Runbook

Apply this skill when implementing hotel knowledge ingestion, `pgvector` retrieval, LangGraph state workflows, tool contracts, and agent security/trajectory evaluations.

## Grounded Policy RAG (`M3`)

1. **Chunk Metadata:** Every `policy_chunks` row must carry `property_code` (`SGN`, `DAD`, `PQC`, or `GROUP`), `doc_id`, `section_title`, and `language` (`vi` or `en`).
2. **Scoped Retrieval:** Always filter SQL `WHERE property_code IN (:property_code, 'GROUP')` before ranking similarity (`pgvector` cosine distance + optional lexical fallback) so Da Nang or Phu Quoc rules never leak into Ho Chi Minh City answers.
3. **Abstention & Citations:**
   - Return structured citations (`doc_id`, `property_code`, `section_title`, `score`) with every chunk.
   - If top similarity score falls below threshold or context lacks the answer, explicitly instruct the agent to abstain and offer human escalation—never extrapolate fees or rules.

## LangGraph Stateful Agent (`M4`)

1. **Explicit Graph State:** Track `conversation_id`, `property_code`, `language`, `channel`, `verified_reservation_codes`, `pending_confirmation`, `escalation_triggered`, and `trace_id`.
2. **Typed Backend Tools Only:**
   - `check_availability`, `create_reservation`, `verify_guest_reservation`, `get_reservation_details`, `modify_reservation`, `cancel_reservation`, `search_hotel_policy`, `create_support_ticket`, `escalate_to_human`.
   - Never expose raw SQL or accept LLM-supplied prices/booking IDs for creation.
3. **Mandatory Two-Gate Safety Enforcement:**
   - **Verification Gate:** Reject `get_reservation_details`, `modify_reservation`, and `cancel_reservation` unless `verification_token` from `verify_guest_reservation` is valid for that `confirmation_code`.
   - **Confirmation Gate:** Before `create_reservation`, `modify_reservation`, or `cancel_reservation`, ensure the assistant presented the exact summary (property, dates, room/rate, VND total or cancellation impact) and received explicit user confirmation (`confirm=True` only after user turn approval).
4. **Escalation Context Snapshot:** When triggering `escalate_to_human`, persist `reason_code`, `priority`, concise bilingual summary, attempted tools, and recent conversation messages into `escalations`.

## Validation Steps

1. **RAG Evaluation (`M3`):** Run golden `vi`/`en` test set measuring `Recall@3 >= 90%`, 0% cross-property leakage, and 100% abstention on unanswerable policy questions.
2. **Agent Trajectory & Security Suite (`M4`):** Run isolated scripted-LLM trajectory tests + real backend DB tests verifying zero unconfirmed bookings, zero unverified PII leaks, zero fabricated codes, and graceful recovery on `INSUFFICIENT_INVENTORY`.
