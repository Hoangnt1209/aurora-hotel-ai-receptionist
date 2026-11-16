"""Bilingual (vi/en) NLU slot extractor, prompt-injection detector, and intent classifier."""

import re
from datetime import date, timedelta
from typing import Any

from pydantic import BaseModel, Field

from aurora.domain.enums import TicketCategory
from aurora.rag.embeddings import detect_language, strip_vietnamese_accents

INJECTION_PATTERNS = (
    r"ignore\s+(all\s+)?(previous|prior|system)\s+(instructions|rules|prompt)",
    r"(bypass|skip|disable)\s+(verification|confirmation|auth|security)",
    r"(override|set)\s+(price|rate|total)\s*(to|=)?\s*0",
    r"(0\s*vnd|free\s+room|gia\s*0\s*dong|mien\s*phi\s*phong)",
    r"(admin\s+mode|developer\s+mode|jailbreak)",
    r"(drop\s+table|select\s+\*\s+from|union\s+select)",
)

CONFIRM_MARKERS = (
    "yes", "confirm", "confirmed", "book it", "proceed", "go ahead", "ok book",
    "yes please", "do it", "cancel it", "agree",
    "dong y", "xac nhan", "chot", "dat di", "dat ngay", "ok dat", "huy di",
    "vang", "ok xac nhan", "chinh xac",
)

DECLINE_MARKERS = (
    "no thanks", "never mind", "cancel action", "don't book", "do not book",
    "stop", "abort", "khong dat", "thoi khong", "huy thao tac", "khong can nua",
)

ESCALATION_MARKERS = (
    "manager", "human", "real person", "staff", "supervisor", "complaint",
    "refund dispute", "sue", "terrible", "unacceptable",
    "quan ly", "nguoi that", "nhan vien", "le tan that", "khieu nai",
    "buc xuc", "doi boi thuong", "chuyen cho quan ly", "gap quan ly",
)


class ParsedTurn(BaseModel):
    language: str
    intent: str
    is_injection: bool = False
    is_confirmation: bool = False
    is_decline: bool = False
    slots: dict[str, Any] = Field(default_factory=dict)


def _extract_dates(text: str, today: date) -> tuple[date | None, date | None]:
    iso_matches = re.findall(r"\b(20\d{2}-\d{2}-\d{2})\b", text)
    if len(iso_matches) >= 2:
        try:
            d1 = date.fromisoformat(iso_matches[0])
            d2 = date.fromisoformat(iso_matches[1])
            return d1, d2
        except ValueError:
            pass

    dmy_matches = re.findall(r"\b(\d{1,2})/(\d{1,2})(?:/(20\d{2}))?\b", text)
    if len(dmy_matches) >= 2:
        try:
            y1 = int(dmy_matches[0][2]) if dmy_matches[0][2] else today.year
            y2 = int(dmy_matches[1][2]) if dmy_matches[1][2] else y1
            d1 = date(y1, int(dmy_matches[0][1]), int(dmy_matches[0][0]))
            d2 = date(y2, int(dmy_matches[1][1]), int(dmy_matches[1][0]))
            if d1 < today and not dmy_matches[0][2]:
                d1 = date(today.year + 1, d1.month, d1.day)
                d2 = date(today.year + 1, d2.month, d2.day)
            return d1, d2
        except ValueError:
            pass

    folded = strip_vietnamese_accents(text.lower())
    nights_match = re.search(r"(\d+)\s*(nights?|dem)\b", folded)
    if "tomorrow" in folded or "ngay mai" in folded:
        d1 = today + timedelta(days=1)
        nights = int(nights_match.group(1)) if nights_match else 1
        return d1, d1 + timedelta(days=max(1, min(nights, 30)))

    return None, None


def _extract_property_code(folded: str) -> str | None:
    if any(k in folded for k in ("sgn", "saigon", "sai gon", "ho chi minh", "hcmc", "district 1", "quan 1")):
        return "SGN"
    if any(k in folded for k in ("dad", "da nang", "my khe")):
        return "DAD"
    if any(k in folded for k in ("pqc", "phu quoc", "sunset bay")):
        return "PQC"
    return None


def _extract_room_type_code(folded: str, prop_code: str | None) -> str | None:
    if any(k in folded for k in ("pool villa", "biet thu", "villa")):
        return "POOL_VILLA"
    if "ocean" in folded or "bien" in folded:
        return "DLX_OCEAN"
    if "garden" in folded or "vuon" in folded:
        return "DLX_GARDEN"
    if "balcony" in folded or "ban cong" in folded:
        return "DLX_BALCONY"
    if "executive" in folded:
        return "EXEC_SUITE"
    if "beachfront" in folded or "beach suite" in folded:
        return "BEACH_SUITE"
    if "sunset" in folded and "suite" in folded:
        return "SUNSET_SUITE"
    if "tropical" in folded or "bungalow" in folded:
        return "TROPICAL_DLX"
    if "superior" in folded or "sup_city" in folded:
        return "SUP_CITY"
    if "deluxe" in folded:
        if prop_code == "DAD":
            return "DLX_OCEAN"
        if prop_code == "SGN":
            return "DLX_BALCONY"
        if prop_code == "PQC":
            return "TROPICAL_DLX"
    for explicit in (
        "SUP_CITY", "DLX_BALCONY", "EXEC_SUITE",
        "DLX_GARDEN", "DLX_OCEAN", "BEACH_SUITE",
        "TROPICAL_DLX", "SUNSET_SUITE", "POOL_VILLA",
    ):
        if explicit.lower() in folded:
            return explicit
    return None


def _extract_guest_name(text: str) -> str | None:
    patterns = (
        r"(?:guest\s*name|full\s*name|name|tên\s*khách|họ\s*tên|họ\s*và\s*tên|tên\s*là|tôi\s*là|tên|ten\s*khach|ho\s*ten|ten\s*la|toi\s*la)\s*[:\-]?\s*([A-Za-zÀ-ỹ\s]{3,60}?)(?:,|\.|;|$|\b\d{8,}|\b[A-Za-z0-9._%+-]+@)",
        r"(?:confirm|dong y|xac nhan|đồng ý|xác nhận|yes)\s*[,\.]\s*([A-Za-zÀ-ỹ\s]{3,50}?)\s*,",
    )
    for pat in patterns:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            candidate = " ".join(m.group(1).strip().split())
            if len(candidate) >= 3 and not re.search(r"\d", candidate):
                return candidate
    return None


def parse_user_turn(
    text: str,
    existing_slots: dict[str, Any] | None = None,
    has_pending_action: bool = False,
    today: date | None = None,
) -> ParsedTurn:
    """Parse bilingual guest message into intent, safety flags, and structured slots."""
    today_ref = today or date.today()
    lang = detect_language(text)
    folded = strip_vietnamese_accents(text.lower()).strip()

    is_injection = any(re.search(pat, folded) for pat in INJECTION_PATTERNS)

    slots: dict[str, Any] = {}

    prop = _extract_property_code(folded) or (existing_slots or {}).get("property_code")
    if prop:
        slots["property_code"] = prop

    rt = _extract_room_type_code(folded, prop)
    if rt:
        slots["room_type_code"] = rt

    if any(k in folded for k in ("non_ref", "non-ref", "non refundable", "khong hoan", "tiet kiem", "saver")):
        slots["rate_plan_code"] = "NON_REF"
    elif any(k in folded for k in ("flex", "linh hoat", "flexible")):
        slots["rate_plan_code"] = "FLEX_24H"

    d1, d2 = _extract_dates(text, today_ref)
    if d1 and d2:
        slots["check_in_date"] = d1.isoformat()
        slots["check_out_date"] = d2.isoformat()

    code_match = re.search(r"\b(AUR-[A-Z]{3}-[A-Z0-9]{6})\b", text.upper())
    if code_match:
        slots["confirmation_code"] = code_match.group(1)

    email_match = re.search(r"\b([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})\b", text)
    if email_match:
        slots["guest_email"] = email_match.group(1).lower()

    phone_match = re.search(r"(?:\+?84|0)[\s\-\.]*(?:\d[\s\-\.]*){8,10}\b", text)
    if phone_match:
        slots["guest_phone"] = phone_match.group(0).strip()

    name = _extract_guest_name(text)
    if name:
        slots["guest_full_name"] = name

    adults_match = re.search(r"(\d+)\s*(adults?|nguoi lon|khach)\b", folded)
    if adults_match:
        slots["adults"] = int(adults_match.group(1))

    children_match = re.search(r"(\d+)\s*(children|child|kids?|tre em)\b", folded)
    if children_match:
        slots["children"] = int(children_match.group(1))

    rooms_match = re.search(r"(\d+)\s*(rooms?|phong)\b", folded)
    if rooms_match:
        rooms_val = int(rooms_match.group(1))
        if rooms_val >= 1:
            slots["rooms_count"] = rooms_val

    # Confirm/Decline evaluation
    clean_words = re.sub(r"[^\w\s]", " ", folded).strip()
    is_decline = any(
        clean_words == m or m in folded
        for m in DECLINE_MARKERS
    )
    is_confirmation = (not is_decline) and any(
        clean_words == m or re.search(rf"\b{re.escape(m)}\b", folded)
        for m in CONFIRM_MARKERS
    )

    if is_injection:
        return ParsedTurn(
            language=lang,
            intent="PROMPT_INJECTION",
            is_injection=True,
            slots=slots,
        )

    if any(m in folded for m in ESCALATION_MARKERS):
        return ParsedTurn(
            language=lang,
            intent="ESCALATE",
            slots=slots,
        )

    if has_pending_action and is_confirmation and not any(
        k in folded for k in ("policy", "chinh sach", "cancel", "huy phong", "check availability")
    ):
        return ParsedTurn(
            language=lang,
            intent="CONFIRM_ACTION",
            is_confirmation=True,
            slots=slots,
        )

    if has_pending_action and is_decline:
        return ParsedTurn(
            language=lang,
            intent="DECLINE_ACTION",
            is_decline=True,
            slots=slots,
        )

    # Ticket creation detection
    if any(k in folded for k in ("airport transfer", "pickup", "shuttle", "don san bay", "xe don")) and any(
        k in folded for k in ("book ", "arrange", "request", "dat xe", "can xe", "flight", "chuyen bay", "ticket")
    ):
        slots["ticket_category"] = TicketCategory.AIRPORT_TRANSFER.value
        return ParsedTurn(language=lang, intent="CREATE_TICKET", slots=slots)

    if any(k in folded for k in ("towel", "pillow", "housekeeping", "clean room", "khan", "goi", "don phong", "them nuoc")):
        slots["ticket_category"] = TicketCategory.HOUSEKEEPING.value
        return ParsedTurn(language=lang, intent="CREATE_TICKET", slots=slots)

    if any(k in folded for k in ("noisy", "broken", "leak", "ac not working", "on ao", "hu may lanh", "khieu nai")):
        slots["ticket_category"] = TicketCategory.COMPLAINT.value
        return ParsedTurn(language=lang, intent="CREATE_TICKET", slots=slots)

    # Cancel / Modify / View existing booking
    if any(k in folded for k in ("cancel", "huy phong", "huy dat", "huy don")):
        return ParsedTurn(
            language=lang,
            intent="CANCEL_BOOKING",
            is_confirmation=is_confirmation,
            slots=slots,
        )

    if any(k in folded for k in ("modify", "change date", "reschedule", "upgrade", "doi ngay", "sua dat", "thay doi")):
        return ParsedTurn(
            language=lang,
            intent="MODIFY_BOOKING",
            is_confirmation=is_confirmation,
            slots=slots,
        )

    if any(k in folded for k in ("verify", "xac minh", "my booking", "view booking", "check my reservation", "xem dat phong", "kiem tra don")):
        return ParsedTurn(language=lang, intent="VIEW_BOOKING", slots=slots)

    # If user just sent verification contact when code is already known
    if (existing_slots or {}).get("pending_verification_for") and (
        "guest_phone" in slots or "guest_email" in slots
    ):
        return ParsedTurn(language=lang, intent="VERIFY_BOOKING", slots=slots)

    # Policy question vs booking/availability
    policy_cues = (
        "policy", "chinh sach", "check-in", "checkin", "check-out", "checkout",
        "nhan phong", "tra phong", "breakfast", "an sang", "pet", "thu cung",
        "smoke", "hut thuoc", "crib", "extra bed", "giuong phu", "deposit",
        "tien coc", "shuttle", "san bay", "cancellation", "hoan tien", "how much is",
        "gia bao nhieu", "helicopter", "bitcoin", "casino", "pool", "ho boi",
    )
    if any(k in folded for k in policy_cues) and not (d1 and d2):
        return ParsedTurn(language=lang, intent="ASK_POLICY", slots=slots)

    if any(k in folded for k in ("book", "reserve", "dat phong", "giu phong", "dat 1 phong")) or (
        d1 and d2 and ("guest_full_name" in slots or "guest_email" in slots or "guest_phone" in slots)
    ):
        return ParsedTurn(
            language=lang,
            intent="BOOK_ROOM",
            is_confirmation=is_confirmation,
            slots=slots,
        )

    if any(k in folded for k in ("available", "availability", "price", "rate", "phong trong", "kiem tra phong", "bao nhieu tien")) or (
        d1 and d2
    ):
        return ParsedTurn(language=lang, intent="CHECK_AVAILABILITY", slots=slots)

    if slots.get("confirmation_code") and ("guest_phone" in slots or "guest_email" in slots):
        return ParsedTurn(language=lang, intent="VERIFY_BOOKING", slots=slots)

    return ParsedTurn(language=lang, intent="GREETING", slots=slots)
