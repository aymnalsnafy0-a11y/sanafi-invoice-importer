"""
استخراج نص الفاتورة عبر Tesseract، وتجميعه في أسطر، ثم تفكيك كل سطر
إلى (وصف الصنف، الكمية، سعر الوحدة، الإجمالي) بأسلوب استدلالي (heuristic).

ملاحظة مهمة: تخطيطات فواتير الموردين تختلف كثيراً (أعمدة بترتيب مختلف،
وجود/غياب عمود الكمية أو الوحدة...). لذلك هذا الاستخراج تقريبي ومصمَّم
ليُراجَع يدوياً في شاشة المراجعة (app.py) قبل التصدير النهائي، وليس
ليُعتمَد عليه بثقة كاملة تلقائياً.
"""

import re

import pytesseract
from PIL import Image
from pytesseract import Output

import config
from line_item import ExtractedLine

if config.TESSERACT_CMD:
    pytesseract.pytesseract.tesseract_cmd = config.TESSERACT_CMD

# رقم عشري: يقبل الفواصل والنقاط كفاصل عشري/آلاف، أرقام عربية أو إنجليزية
_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_NUMBER_RE = re.compile(r"[0-9٠-٩]+(?:[.,][0-9٠-٩]+)?")

# كلمات تدل على سطر إجمالي/ضريبة/رأس جدول/ملاحظة تنسيق - يُستبعد من قائمة
# الأصناف المستخرجة (مطابقة غير حساسة لحالة الأحرف بالنسبة للإنجليزي)
_SKIP_LINE_KEYWORDS_AR = (
    "الإجمالي الكلي",
    "المجموع",
    "الضريبة",
    "ضريبة القيمة",
    "الخصم",
    "صافي",
    "المدفوع",
    "رقم الفاتورة",
    "التاريخ",
    "الرصيد",
    "مبيعات",
    "عروض",
)
_SKIP_LINE_KEYWORDS_EN_LOWER = (
    "total",
    "subtotal",
    "sub total",
    "tax",
    "vat",
    "balance",
    "invoice no",
    "invoice date",
    "discount",
    "qty/case",
    "excise tax",
    "fold here",
    "sales:",
    "promotions:",
    "net sales",
    "gross sales",
    "amount receivable",
)


def _normalize_number(token: str) -> float | None:
    token = token.translate(_ARABIC_DIGITS)
    token = token.replace(",", "")
    try:
        return float(token)
    except ValueError:
        return None


def _group_words_into_lines(ocr_data: dict) -> list[list[dict]]:
    """
    يجمّع كلمات Tesseract في صفوف بناءً على القرب الرأسي الفعلي (y) بدل
    الاعتماد على تجميع Tesseract الداخلي (block/par/line) — الأخير يخطئ
    أحياناً على صور الجوال المائلة/المزدحمة فيدمج صفَّين متجاورين ببعض أو
    يفصل صفاً واحداً لصفين، مما يخلط أرقام صنف بأرقام صنف آخر. التجميع حسب
    مركز ارتفاع كل كلمة أكثر موثوقية هنا.
    """
    words = []
    n = len(ocr_data["text"])
    for i in range(n):
        text = ocr_data["text"][i].strip()
        conf = float(ocr_data["conf"][i])
        if not text or conf < 0:
            continue
        top = ocr_data["top"][i]
        height = ocr_data["height"][i]
        words.append(
            {
                "text": text,
                "left": ocr_data["left"][i],
                "center": ocr_data["left"][i] + ocr_data["width"][i] / 2,
                "y_center": top + height / 2,
                "height": height,
                "conf": conf,
            }
        )

    if not words:
        return []

    words.sort(key=lambda w: w["y_center"])
    median_height = sorted(w["height"] for w in words)[len(words) // 2] or 20
    row_gap_threshold = median_height * 0.6

    rows: list[list[dict]] = []
    current_row: list[dict] = [words[0]]
    for w in words[1:]:
        # المقارنة مع أول كلمة بالصف (وليس آخر كلمة أُضيفت) لمنع "انزلاق"
        # تراكمي يدمج عدة صفوف ببعض بسبب فروقات صغيرة متتالية
        anchor_y = current_row[0]["y_center"]
        if abs(w["y_center"] - anchor_y) <= row_gap_threshold:
            current_row.append(w)
        else:
            rows.append(current_row)
            current_row = [w]
    rows.append(current_row)

    return [sorted(row, key=lambda w: w["left"]) for row in rows]


# كلمات رأس عمود تدل على الحقل المقابل - إن وُجد صف رأس أعمدة واضح بالفاتورة
# (شائع بالفواتير المطبوعة الرسمية)، تُستخدم مواقعه الأفقية (x) لتحديد أي
# رقم بكل سطر ينتمي لأي حقل، بدل التخمين بترتيب الظهور فقط - وهذا يحل مشكلة
# تداخل أعمدة أخرى (رقم الصنف الداخلي، رقم تسلسلي) مع الكمية/السعر/الإجمالي.
_HEADER_KEYWORDS = {
    "quantity": ("qty", "الكمية"),
    "unit_price": ("price", "سعر"),
    "total": ("total", "اجمالي", "إجمالي"),
}


def _find_header_positions(lines: list[list[dict]]) -> dict[str, float]:
    best_positions: dict[str, float] = {}
    best_match_count = 0
    for line_words in lines:
        positions: dict[str, float] = {}
        for w in line_words:
            token_lower = w["text"].lower()
            for field_name, keywords in _HEADER_KEYWORDS.items():
                if field_name in positions:
                    continue
                if any(kw in token_lower or kw in w["text"] for kw in keywords):
                    positions[field_name] = w["center"]
        if len(positions) > best_match_count:
            best_match_count = len(positions)
            best_positions = positions
    return best_positions if best_match_count >= 2 else {}


def _parse_line(words: list[dict], header_positions: dict[str, float] | None = None) -> ExtractedLine | None:
    full_text = " ".join(w["text"] for w in words)
    full_text_lower = full_text.lower()

    if any(kw in full_text for kw in _SKIP_LINE_KEYWORDS_AR):
        return None
    if any(kw in full_text_lower for kw in _SKIP_LINE_KEYWORDS_EN_LOWER):
        return None

    numbers: list[tuple[float, float]] = []  # (value, x_center)
    barcode_candidate = ""
    desc_tokens: list[str] = []
    for w in words:
        token = w["text"]
        if _NUMBER_RE.fullmatch(token):
            # سلسلة أرقام صحيحة طويلة (بدون فاصلة عشرية) هي على الأغلب باركود
            # أو رقم هوية/سجل تجاري/ضريبي، وليست كمية أو سعراً - كميات وأسعار
            # الفواتير الاعتيادية أرقام قصيرة نسبياً.
            digits_only = token.translate(_ARABIC_DIGITS)
            if "." not in digits_only and "," not in digits_only and len(digits_only) >= 7:
                barcode_candidate = digits_only
                continue
            value = _normalize_number(token)
            if value is not None:
                numbers.append((value, w["center"]))
                continue
        desc_tokens.append(token)

    description = " ".join(desc_tokens).strip()
    if not description or len(numbers) == 0:
        return None  # على الأغلب سطر غير متعلق بالأصناف (عنوان، فاصل...)

    quantity = unit_price = total = None

    if header_positions and len(numbers) > 3:
        # عدة أرقام على نفس السطر (رقم تسلسلي/رقم صنف داخلي مع الكمية والسعر
        # والإجمالي) - نربط كل رقم بأقرب عمود رأس له أفقياً بدل التخمين بالترتيب
        used_indices: set[int] = set()
        for field_name in ("quantity", "unit_price", "total"):
            header_x = header_positions.get(field_name)
            if header_x is None:
                continue
            best_idx, best_dist = None, None
            for idx, (_, center) in enumerate(numbers):
                if idx in used_indices:
                    continue
                dist = abs(center - header_x)
                if best_dist is None or dist < best_dist:
                    best_idx, best_dist = idx, dist
            if best_idx is not None:
                used_indices.add(best_idx)
                value = numbers[best_idx][0]
                if field_name == "quantity":
                    quantity = value
                elif field_name == "unit_price":
                    unit_price = value
                else:
                    total = value

    if quantity is None and unit_price is None and total is None:
        # لا يوجد رأس أعمدة موثوق أو لم يُحسم بالطريقة أعلاه - نرجع للتخمين
        # البسيط بترتيب ظهور الأرقام بالسطر
        values = [v for v, _ in numbers]
        if len(values) >= 3:
            quantity, unit_price, total = values[0], values[1], values[-1]
        elif len(values) == 2:
            quantity, unit_price = values[0], values[1]
            total = round(quantity * unit_price, 2)
        elif len(values) == 1:
            quantity = 1.0
            unit_price = values[0]
            total = unit_price

    avg_conf = sum(w["conf"] for w in words) / len(words)

    return ExtractedLine(
        raw_text=full_text,
        description=description,
        quantity=quantity,
        unit_price=unit_price,
        total=total,
        ocr_confidence=avg_conf,
        barcode=barcode_candidate,
    )


def extract_line_items(img: Image.Image) -> list[ExtractedLine]:
    """يشغّل OCR على الصورة ويرجع قائمة الأسطر المُفسَّرة كبنود فاتورة محتملة."""
    ocr_data = pytesseract.image_to_data(
        img, lang=config.OCR_LANGS, output_type=Output.DICT
    )
    lines = _group_words_into_lines(ocr_data)
    header_positions = _find_header_positions(lines)

    results = []
    for line_words in lines:
        parsed = _parse_line(line_words, header_positions)
        if parsed is not None:
            results.append(parsed)
    return results
