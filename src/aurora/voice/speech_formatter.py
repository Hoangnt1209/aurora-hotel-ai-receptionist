"""Speech formatting, phonetic reference spelling, and spoken read-back confirmation."""

import re
from datetime import date
from decimal import Decimal
from typing import Any


def spell_code_for_speech(code: str, language: str = "vi") -> str:
    """Format an alphanumeric reference code for clear spoken pronunciation."""
    parts = code.strip().upper().split("-")
    separator = ", gạch ngang, " if language == "vi" else ", dash, "
    spelled_parts = [" ".join(list(part)) for part in parts]
    return separator.join(spelled_parts)


def format_vnd_for_speech(amount_str: str, language: str = "vi") -> str:
    """Convert formatted VND numbers (e.g. '6,900,000 VND') into natural spoken phrasing."""
    digits = re.sub(r"[^\d]", "", amount_str)
    if not digits:
        return amount_str
    value = int(digits)
    millions, rem = divmod(value, 1_000_000)
    thousands = rem // 1_000
    if language == "vi":
        if millions > 0 and thousands > 0:
            return f"{millions} triệu {thousands} nghìn đồng"
        if millions > 0:
            return f"{millions} triệu đồng"
        if thousands > 0:
            return f"{thousands} nghìn đồng"
        return f"{value} đồng"
    if millions > 0 and thousands > 0:
        return f"{millions} million {thousands} thousand VND"
    if millions > 0:
        return f"{millions} million VND"
    if thousands > 0:
        return f"{thousands} thousand VND"
    return f"{value} VND"


def format_for_speech(text: str, language: str = "vi") -> str:
    """Strip markdown and adapt receptionist text for streaming TTS playback."""
    cleaned = re.sub(r"\[(?:Source|Nguồn):[^\]]+\]", "", text)
    cleaned = re.sub(r"[*_`#]", "", cleaned)
    cleaned = re.sub(r"^\s*[-•]\s*", "", cleaned, flags=re.MULTILINE)
    cleaned = re.sub(r"\n+", ". ", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()

    cleaned = re.sub(
        r"\b((?:AUR|TCK|ESC)-[A-Z0-9]{2,4}-[A-Z0-9]{4,8})\b",
        lambda m: spell_code_for_speech(m.group(1), language),
        cleaned,
    )
    cleaned = re.sub(
        r"\b([\d,]+)\s*VND\b",
        lambda m: format_vnd_for_speech(m.group(1), language),
        cleaned,
    )
    return cleaned


def split_into_tts_sentences(spoken_text: str) -> list[str]:
    """Split spoken reply into sentence-level chunks for low-latency streaming TTS."""
    raw_parts = re.split(r"(?<=[.!?])\s+", spoken_text.strip())
    return [part.strip() for part in raw_parts if part.strip()]


def build_spoken_readback(pending_action: dict[str, Any], language: str = "vi") -> str:
    """Generate mandatory spoken read-back confirmation summarizing exact dates & price."""
    action_type = pending_action.get("type")
    if action_type == "CREATE_RESERVATION":
        ci = date.fromisoformat(pending_action["check_in_date"])
        co = date.fromisoformat(pending_action["check_out_date"])
        total_spoken = format_vnd_for_speech(
            str(Decimal(str(pending_action["total_amount_vnd"]))), language
        )
        if language == "vi":
            return (
                f"Tôi xin đọc lại để bạn xác nhận: đặt phòng {pending_action['room_type_name']} "
                f"tại {pending_action['property_name']}, nhận phòng {ci.strftime('%A ngày %d/%m/%Y')}, "
                f"trả phòng {co.strftime('%A ngày %d/%m/%Y')}, gói {pending_action['rate_plan_code']}, "
                f"tổng thanh toán {total_spoken}. Bạn xác nhận đặt phòng này chứ?"
            )
        return (
            f"Let me read that back for confirmation: {pending_action['room_type_name']} "
            f"at {pending_action['property_name']}, checking in {ci.strftime('%A, %B %d, %Y')} "
            f"and checking out {co.strftime('%A, %B %d, %Y')}, rate plan {pending_action['rate_plan_code']}, "
            f"total {total_spoken}. Would you like me to confirm this booking?"
        )

    if action_type == "CANCEL_RESERVATION":
        code_spoken = spell_code_for_speech(
            str(pending_action["confirmation_code"]), language
        )
        if language == "vi":
            return f"Tôi xin xác nhận lại: bạn muốn hủy đơn đặt phòng mã {code_spoken}, đúng không?"
        return f"Please confirm verbally: you would like me to cancel reservation {code_spoken}, correct?"

    if action_type == "MODIFY_RESERVATION":
        code_spoken = spell_code_for_speech(
            str(pending_action["confirmation_code"]), language
        )
        if language == "vi":
            return f"Bạn xác nhận áp dụng các thay đổi cho đơn đặt phòng {code_spoken} chứ?"
        return f"Please confirm verbally to apply the modifications to reservation {code_spoken}."

    return ""
