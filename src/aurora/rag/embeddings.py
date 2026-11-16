"""Multilingual bilingual (vi/en) 384-dim semantic + lexical embedding provider."""

import hashlib
import math
import re
import unicodedata

EMBEDDING_DIM = 384
LEXICAL_DIM = 256
CONCEPT_DIM = 128

# Bilingual semantic concepts so Vietnamese and English phrasings align in vector space.
HOSPITALITY_CONCEPT_CLUSTERS: list[tuple[float, tuple[str, ...]]] = [
    (3.0, ("cancel", "cancellation", "refund", "non_ref", "flex_24h", "huy", "hoan tien", "khong hoan", "doi ngay", "modify", "penalty", "24h", "24 gio")),
    (3.0, ("shuttle", "airport", "transfer", "pickup", "sedan", "suv", "flight", "san bay", "xe don", "dua don", "chuyen bay")),
    (2.8, ("tan son nhat", "sgn", "saigon", "sai gon", "ho chi minh", "quan 1", "district 1", "450,000", "450.000")),
    (2.8, ("da nang", "dad", "my khe", "350,000", "350.000")),
    (2.8, ("phu quoc", "pqc", "sunset bay", "600,000", "complimentary", "mien phi", "15:00")),
    (2.8, ("check-in", "checkin", "check-out", "checkout", "early", "late", "nhan phong", "tra phong", "som", "muon", "14:00", "12:00")),
    (2.8, ("breakfast", "buffet", "pho", "bistro", "an sang", "bua sang", "nha hang")),
    (2.8, ("child", "children", "kid", "infant", "crib", "extra bed", "rollaway", "tre em", "em be", "noi", "giuong phu", "phu thu", "550,000", "550.000")),
    (2.8, ("pet", "pets", "dog", "cat", "animal", "thu cung", "cho", "meo", "dong vat")),
    (2.8, ("smoke", "smoking", "cigarette", "vape", "e-cigarette", "hut thuoc", "thuoc la", "khu mui", "2,000,000", "2.000.000")),
    (2.8, ("deposit", "payment", "pay", "cash", "card", "visa", "mastercard", "cvv", "thanh toan", "dat coc", "tien coc", "the tin dung", "chuyen khoan")),
    (2.8, ("group", "multiple", "wedding", "corporate", "block", "rooms", "together", "2 rooms", "3 rooms", "4 rooms", "5 rooms", "maximum stay", "online ai", "nhieu phong", "dat doan", "2 phong", "3 phong", "4 phong", "5 phong", "cong ty")),
    (2.6, ("pool", "infinity", "beach", "lounger", "towel", "gym", "fitness", "butler", "villa", "ho boi", "bai bien", "quan gia")),
    (2.4, ("luggage", "storage", "shower", "departure", "hanh ly", "tam trang", "giu do")),
]


def strip_vietnamese_accents(text: str) -> str:
    """Fold Vietnamese diacritics into ASCII equivalents for robust matching."""
    text = text.replace("đ", "d").replace("Đ", "D")
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def detect_language(text: str) -> str:
    """Detect whether text is Vietnamese ('vi') or English ('en')."""
    if re.search(
        r"[àáảãạăằắẳẵặâầấẩẫậđèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵ]",
        text.lower(),
    ):
        return "vi"
    folded = strip_vietnamese_accents(text.lower())
    vi_markers = (
        "khach san",
        "dat phong",
        "huy phong",
        "san bay",
        "an sang",
        "nhan phong",
        "tra phong",
        "bao nhieu",
        "cho toi",
        "toi muon",
        "thu cung",
        "hut thuoc",
        "giuong phu",
        "tre em",
        "dat coc",
        "mien phi",
        "may gio",
    )
    if any(marker in folded for marker in vi_markers):
        return "vi"
    return "en"


def embed_text(text: str) -> list[float]:
    """Compute a normalized 384-dim semantic + lexical vector for pgvector."""
    vec = [0.0] * EMBEDDING_DIM
    lower = text.lower()
    folded = strip_vietnamese_accents(lower)

    # 1. Lexical token + trigram hashing into first 256 dims
    tokens = re.findall(r"[a-z0-9_]+", folded)
    for tok in tokens:
        idx = int.from_bytes(hashlib.blake2b(tok.encode("utf-8"), digest_size=4).digest(), "big") % LEXICAL_DIM
        vec[idx] += 1.0
        if len(tok) >= 4:
            for i in range(len(tok) - 2):
                gram = tok[i : i + 3]
                gidx = int.from_bytes(hashlib.blake2b(gram.encode("utf-8"), digest_size=4).digest(), "big") % LEXICAL_DIM
                vec[gidx] += 0.35

    # 2. Semantic hospitality cluster activations in upper 128 dims
    for c_idx, (weight, keywords) in enumerate(HOSPITALITY_CONCEPT_CLUSTERS):
        hits = sum(1 for kw in keywords if kw in folded or kw in lower)
        if hits > 0:
            base_slot = LEXICAL_DIM + (c_idx * 4) % CONCEPT_DIM
            activation = weight * math.sqrt(hits)
            vec[base_slot] += activation
            vec[(base_slot + 1) % EMBEDDING_DIM] += activation * 0.5

    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [round(v / norm, 6) for v in vec]
