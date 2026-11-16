"""Bilingual hotel policy knowledge base for Aurora Hospitality Group (GROUP, SGN, DAD, PQC)."""

from typing import TypedDict


class PolicyDocEntry(TypedDict):
    property_code: str
    doc_id: str
    section_title: str
    language: str
    content: str
    tags: list[str]


POLICY_CORPUS: list[PolicyDocEntry] = [
    # --- GROUP POLICIES (Apply across all 3 Aurora properties) ---
    {
        "property_code": "GROUP",
        "doc_id": "group_cancellation_policy",
        "section_title": "Cancellation & Modification Policy",
        "language": "en",
        "content": (
            "Flexible Rate (FLEX_24H) reservations may be modified or cancelled free of "
            "charge up to 24 hours prior to the property's standard check-in time. "
            "Cancellations within 24 hours of check-in incur a one-night penalty. "
            "Non-Refundable (NON_REF) bookings receive a 15% discount and cannot be "
            "cancelled or refunded automatically; exceptional waiver requests must be "
            "escalated to front-desk management."
        ),
        "tags": ["cancel", "cancellation", "modify", "refund", "flex", "non_ref", "penalty"],
    },
    {
        "property_code": "GROUP",
        "doc_id": "group_cancellation_policy",
        "section_title": "Chính sách hủy và đổi đặt phòng",
        "language": "vi",
        "content": (
            "Đặt phòng theo gói Linh hoạt (FLEX_24H) được phép thay đổi ngày hoặc hủy "
            "miễn phí trước giờ nhận phòng tiêu chuẩn 24 giờ. Hủy trong vòng 24 giờ "
            "trước khi nhận phòng sẽ tính phí 1 đêm đầu tiên. Gói Tiết kiệm Không hoàn "
            "hủy (NON_REF) được giảm 15% và không thể tự động hủy hay hoàn tiền; các "
            "trường hợp bất khả kháng cần chuyển quản lý lễ tân xem xét."
        ),
        "tags": ["huy", "doi ngay", "hoan tien", "khong hoan huy", "linh hoat", "flex", "non_ref"],
    },
    {
        "property_code": "GROUP",
        "doc_id": "group_child_extrabed_policy",
        "section_title": "Children, Baby Cribs & Extra Bed Policy",
        "language": "en",
        "content": (
            "Children under 6 years stay free when sharing existing bedding with parents "
            "(complimentary breakfast included). Baby cribs for infants under 2 years "
            "are complimentary upon request. Children aged 6 to 11 sharing existing beds "
            "incur a 250,000 VND daily breakfast fee. Rollaway extra beds cost "
            "550,000 VND per night (breakfast included) and fit Deluxe or Suite categories."
        ),
        "tags": ["child", "children", "kid", "infant", "crib", "extra bed", "rollaway"],
    },
    {
        "property_code": "GROUP",
        "doc_id": "group_child_extrabed_policy",
        "section_title": "Chính sách trẻ em, nôi em bé và giường phụ",
        "language": "vi",
        "content": (
            "Trẻ em dưới 6 tuổi được ở miễn phí và miễn phí ăn sáng khi ngủ chung giường "
            "với bố mẹ. Nôi em bé dưới 2 tuổi được cung cấp miễn phí theo yêu cầu. Trẻ từ "
            "6 đến 11 tuổi ngủ chung giường phụ thu ăn sáng 250.000 VND/đêm. Giường phụ "
            "(extra bed) có giá 550.000 VND/đêm (đã gồm ăn sáng), áp dụng cho phòng Deluxe và Suite."
        ),
        "tags": ["tre em", "em be", "noi", "giuong phu", "extra bed", "phu thu"],
    },
    {
        "property_code": "GROUP",
        "doc_id": "group_pet_smoking_policy",
        "section_title": "Pets & Smoke-Free Room Policy",
        "language": "en",
        "content": (
            "Pets are not permitted at any Aurora property, except certified guide or "
            "service animals with prior notice. All guest rooms and suites are 100% "
            "smoke-free (including e-cigarettes); smoking is allowed only in designated "
            "outdoor garden areas. Violations carry a 2,000,000 VND deep-cleaning fee."
        ),
        "tags": ["pet", "dog", "cat", "service animal", "smoke", "smoking", "cigarette"],
    },
    {
        "property_code": "GROUP",
        "doc_id": "group_pet_smoking_policy",
        "section_title": "Chính sách thú cưng và phòng không hút thuốc",
        "language": "vi",
        "content": (
            "Khách sạn Aurora không nhận thú cưng (chó, mèo), ngoại trừ chó dẫn đường "
            "hoặc động vật hỗ trợ có chứng nhận báo trước. Toàn bộ phòng nghỉ đều cấm "
            "hút thuốc (kể cả thuốc lá điện tử); khách chỉ được hút tại khu vực sân vườn "
            "ngoài trời quy định. Vi phạm sẽ phụ thu phí vệ sinh khử mùi 2.000.000 VND."
        ),
        "tags": ["thu cung", "cho", "meo", "hut thuoc", "thuoc la", "khong hut thuoc"],
    },
    {
        "property_code": "GROUP",
        "doc_id": "group_payment_deposit_policy",
        "section_title": "Payment, Security Deposit & Card Privacy",
        "language": "en",
        "content": (
            "Reservations made through the AI receptionist are guaranteed for payment at "
            "the hotel upon check-in via cash (VND), bank transfer, or Visa/Mastercard. "
            "A refundable security deposit of 1,000,000 VND per night is collected at "
            "check-in for incidentals. Never share credit card numbers or CVV codes in chat or voice."
        ),
        "tags": ["payment", "pay", "deposit", "cash", "credit card", "visa", "security"],
    },
    {
        "property_code": "GROUP",
        "doc_id": "group_payment_deposit_policy",
        "section_title": "Chính sách thanh toán, tiền cọc và bảo mật thẻ",
        "language": "vi",
        "content": (
            "Đặt phòng qua lễ tân AI được thanh toán trực tiếp tại quầy khi nhận phòng "
            "bằng tiền mặt VND, chuyển khoản hoặc thẻ Visa/Mastercard. Khách sạn thu "
            "tiền cọc đảm bảo phát sinh 1.000.000 VND/đêm khi làm thủ tục nhận phòng và "
            "hoàn lại lúc trả phòng. Quý khách tuyệt đối không gửi số thẻ tín dụng hay CVV qua chat hoặc thoại."
        ),
        "tags": ["thanh toan", "tien coc", "dat coc", "the tin dung", "chuyen khoan", "tien mat"],
    },
    {
        "property_code": "GROUP",
        "doc_id": "group_booking_policy",
        "section_title": "Group Bookings & Maximum Stay Limits",
        "language": "en",
        "content": (
            "Online AI bookings handle single-room stays (1 room per reservation) for up "
            "to 30 consecutive nights. Requests for 2 or more rooms, corporate retreats, "
            "or wedding room blocks must be escalated to our human group sales desk."
        ),
        "tags": ["group", "multiple rooms", "rooms", "4 rooms", "together", "ai", "2 rooms", "wedding", "corporate", "30 nights"],
    },
    {
        "property_code": "GROUP",
        "doc_id": "group_booking_policy",
        "section_title": "Đặt phòng đoàn và giới hạn số phòng",
        "language": "vi",
        "content": (
            "Trợ lý AI hỗ trợ đặt tối đa 1 phòng mỗi giao dịch và tối đa 30 đêm liên tiếp. "
            "Các yêu cầu đặt từ 2 phòng trở lên, đặt đoàn công ty hoặc tiệc cưới cần "
            "chuyển nhân viên kinh doanh và lễ tân trực tiếp sắp xếp."
        ),
        "tags": ["nhieu phong", "dat doan", "2 phong", "4 phong", "5 phong", "cong ty", "dam cuoi", "gioi han"],
    },
    # --- SGN: AURORA SAIGON CENTRAL ---
    {
        "property_code": "SGN",
        "doc_id": "sgn_checkin_shuttle",
        "section_title": "Saigon Check-in, Late Check-out & Tan Son Nhat Transfer",
        "language": "en",
        "content": (
            "Aurora Saigon Central (SGN, District 1) standard check-in is 14:00 and "
            "check-out is 12:00. Early check-in before 10:00 or late check-out until 18:00 "
            "is 50% of one night's rate subject to availability. Private sedan transfer "
            "from Tan Son Nhat International Airport (SGN) is 450,000 VND one-way (book 12 hours ahead)."
        ),
        "tags": ["sgn", "saigon", "ho chi minh", "check-in", "check-out", "shuttle", "airport", "tan son nhat"],
    },
    {
        "property_code": "SGN",
        "doc_id": "sgn_checkin_shuttle",
        "section_title": "Giờ nhận trả phòng Sài Gòn & Xe đón sân bay Tân Sơn Nhất",
        "language": "vi",
        "content": (
            "Aurora Saigon Central (SGN, Quận 1) nhận phòng từ 14:00 và trả phòng trước "
            "12:00. Nhận phòng sớm trước 10:00 hoặc trả phòng muộn đến 18:00 phụ thu 50% "
            "giá 1 đêm tùy tình trạng phòng. Xe sedan riêng đưa đón sân bay Tân Sơn Nhất "
            "có giá 450.000 VND/chiều (cần đặt trước 12 tiếng kèm mã chuyến bay)."
        ),
        "tags": ["sgn", "sai gon", "ho chi minh", "nhan phong", "tra phong", "xe don", "san bay", "tan son nhat"],
    },
    {
        "property_code": "SGN",
        "doc_id": "sgn_breakfast_facilities",
        "section_title": "Saigon Breakfast Hours & Rooftop Facilities",
        "language": "en",
        "content": (
            "At Aurora Saigon Central (SGN), buffet and Vietnamese pho breakfast is served "
            "daily from 06:00 to 10:00 at Saigon Bistro on Floor 2. Guests enjoy complimentary "
            "access to the rooftop fitness studio (06:00-22:00) and luggage storage."
        ),
        "tags": ["sgn", "saigon", "breakfast", "bistro", "gym", "fitness", "luggage"],
    },
    {
        "property_code": "SGN",
        "doc_id": "sgn_breakfast_facilities",
        "section_title": "Ăn sáng & Tiện ích tại Aurora Saigon Central",
        "language": "vi",
        "content": (
            "Tại Aurora Saigon Central (SGN), bữa sáng buffet phục vụ hằng ngày từ 06:00 "
            "đến 10:00 tại nhà hàng Saigon Bistro tầng 2. Khách lưu trú được miễn phí "
            "phòng gym tầng thượng (06:00-22:00) và dịch vụ giữ hành lý miễn phí."
        ),
        "tags": ["sgn", "sai gon", "an sang", "buffet", "gym", "hanh ly"],
    },
    # --- DAD: AURORA MY KHE BEACH ---
    {
        "property_code": "DAD",
        "doc_id": "dad_checkin_shuttle",
        "section_title": "Da Nang Check-in & Airport Transfer Policy",
        "language": "en",
        "content": (
            "Aurora My Khe Beach (DAD) standard check-in is 14:00 and check-out is 12:00. "
            "Private car transfer between Da Nang International Airport (DAD) and the hotel "
            "costs 350,000 VND per vehicle one-way and requires flight details 6 hours ahead."
        ),
        "tags": ["dad", "da nang", "my khe", "check-in", "check-out", "airport", "transfer", "shuttle"],
    },
    {
        "property_code": "DAD",
        "doc_id": "dad_checkin_shuttle",
        "section_title": "Nhận trả phòng Đà Nẵng & Đưa đón sân bay Đà Nẵng",
        "language": "vi",
        "content": (
            "Aurora My Khe Beach (DAD, Đà Nẵng) nhận phòng từ 14:00 và trả phòng lúc 12:00. "
            "Dịch vụ xe riêng đưa đón sân bay Quốc tế Đà Nẵng có giá 350.000 VND/lượt "
            "(vui lòng đặt trước tối thiểu 6 tiếng kèm số hiệu chuyến bay)."
        ),
        "tags": ["dad", "da nang", "my khe", "nhan phong", "tra phong", "san bay", "xe don"],
    },
    {
        "property_code": "DAD",
        "doc_id": "dad_breakfast_beach",
        "section_title": "Da Nang Breakfast, Infinity Pool & Beach Access",
        "language": "en",
        "content": (
            "Aurora My Khe Beach (DAD) serves coastal buffet breakfast from 06:30 to 10:00. "
            "The oceanfront infinity pool is open 06:00-21:00. Complimentary beach loungers, "
            "umbrellas, and fresh towels are provided directly on My Khe Beach."
        ),
        "tags": ["dad", "da nang", "breakfast", "pool", "infinity pool", "beach", "my khe"],
    },
    {
        "property_code": "DAD",
        "doc_id": "dad_breakfast_beach",
        "section_title": "Ăn sáng, Hồ bơi vô cực & Bãi biển Mỹ Khê",
        "language": "vi",
        "content": (
            "Aurora My Khe Beach (DAD) phục vụ bữa sáng buffet từ 06:30 đến 10:00. "
            "Hồ bơi vô cực hướng biển mở cửa từ 06:00 đến 21:00. Khách nghỉ dưỡng được "
            "miễn phí ghế tắm nắng, dù che và khăn tắm ngay tại bãi biển Mỹ Khê."
        ),
        "tags": ["dad", "da nang", "an sang", "ho boi", "bai bien", "my khe"],
    },
    # --- PQC: AURORA SUNSET BAY ---
    {
        "property_code": "PQC",
        "doc_id": "pqc_checkin_shuttle",
        "section_title": "Phu Quoc Check-in & Complimentary Airport Shuttle",
        "language": "en",
        "content": (
            "Aurora Sunset Bay (PQC, Phu Quoc) standard check-in starts at 15:00 and "
            "check-out is 12:00. Scheduled shared shuttle from Phu Quoc Airport (PQC) is "
            "complimentary for confirmed guests submitting flight details 24 hours ahead; "
            "private 7-seater SUV transfer is 600,000 VND one-way."
        ),
        "tags": ["pqc", "phu quoc", "check-in", "15:00", "shuttle", "complimentary", "free", "airport"],
    },
    {
        "property_code": "PQC",
        "doc_id": "pqc_checkin_shuttle",
        "section_title": "Nhận phòng Phú Quốc & Xe đưa đón sân bay miễn phí",
        "language": "vi",
        "content": (
            "Aurora Sunset Bay (PQC, Phú Quốc) nhận phòng từ 15:00 và trả phòng lúc 12:00. "
            "Xe bus đưa đón sân bay Phú Quốc theo lịch cố định được miễn phí cho khách "
            "gửi mã chuyến bay trước 24 giờ; xe SUV riêng 7 chỗ có giá 600.000 VND/chiều."
        ),
        "tags": ["pqc", "phu quoc", "nhan phong", "15:00", "mien phi", "xe don", "san bay"],
    },
    {
        "property_code": "PQC",
        "doc_id": "pqc_breakfast_villa_facilities",
        "section_title": "Phu Quoc Breakfast, Sunset Club & Villa Butler",
        "language": "en",
        "content": (
            "Aurora Sunset Bay (PQC) serves resort breakfast from 06:30 to 10:30 at "
            "Sunset Restaurant. Private Pool Villa guests receive dedicated butler service "
            "from 07:00 to 22:00 and complimentary afternoon tea at Sunset Beach Club."
        ),
        "tags": ["pqc", "phu quoc", "breakfast", "butler", "villa", "sunset club"],
    },
    {
        "property_code": "PQC",
        "doc_id": "pqc_breakfast_villa_facilities",
        "section_title": "Ăn sáng, Quản gia Villa & Sunset Club tại Phú Quốc",
        "language": "vi",
        "content": (
            "Aurora Sunset Bay (PQC) phục vụ bữa sáng từ 06:30 đến 10:30 tại Sunset "
            "Restaurant. Khách đặt Private Pool Villa được phục vụ quản gia riêng từ "
            "07:00 đến 22:00 và miễn phí trà chiều tại Sunset Beach Club."
        ),
        "tags": ["pqc", "phu quoc", "an sang", "quan gia", "villa", "ho boi rieng"],
    },
    {
        "property_code": "PQC",
        "doc_id": "pqc_late_checkout_luggage",
        "section_title": "Phu Quoc Late Check-out & Departure Lounge",
        "language": "en",
        "content": (
            "Late check-out at Aurora Sunset Bay (PQC) until 18:00 is 50% of the daily "
            "rate subject to villa availability. After 12:00 check-out, guests may use "
            "complimentary luggage storage and shower suites before evening flights."
        ),
        "tags": ["pqc", "phu quoc", "late checkout", "shower", "luggage", "departure"],
    },
    {
        "property_code": "PQC",
        "doc_id": "pqc_late_checkout_luggage",
        "section_title": "Trả phòng muộn & Phòng tắm chờ chuyến bay tại Phú Quốc",
        "language": "vi",
        "content": (
            "Trả phòng muộn đến 18:00 tại Aurora Sunset Bay (PQC) phụ thu 50% giá phòng "
            "tùy tình trạng phòng trống. Sau 12:00 trả phòng, quý khách được sử dụng "
            "miễn phí khu giữ hành lý và phòng tắm tráng trước giờ bay tối."
        ),
        "tags": ["pqc", "phu quoc", "tra phong muon", "hanh ly", "tam trang"],
    },
]
