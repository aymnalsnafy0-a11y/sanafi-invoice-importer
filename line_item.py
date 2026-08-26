"""تعريف سطر الفاتورة المستخرَج (ExtractedLine) - بدون أي اعتماد على Tesseract
أو OpenCV، عشان يستخدمه مسار Claude Vision (vision_extract.py) بدون الحاجة
لتثبيت مكتبات OCR المحلي غير المستخدمة فعلياً."""

from dataclasses import dataclass


@dataclass
class ExtractedLine:
    raw_text: str
    description: str
    quantity: float | None
    unit_price: float | None
    total: float | None
    ocr_confidence: float
    matched_item_code: str = ""
    matched_item_name: str = ""
    match_score: float = 0.0
    needs_review: bool = True
    barcode: str = ""
    unit: str = ""
    matched_internal_id: str = ""  # CLS_ID الداخلي (لصيغة الملف القياسي)
    matched_unit_id: str = ""  # UN_ID الداخلي (لصيغة الملف القياسي)
    confirmed_not_in_catalog: bool = False  # المستخدم أكّد صراحة إنه صنف جديد، مو تخمين
    match_reason: str = ""  # سبب مطابقة matching_engine.py (أو أقرب اقتراح لو مو مؤكد)
