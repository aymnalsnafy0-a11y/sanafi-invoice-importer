"""
تطبيع نص وصف الصنف واستخراج خصائصه (ماركة/حجم/عدد قطع/نوع تعبئة) - يُستخدم
من matching_engine.py لمقارنة صنف الفاتورة بمرشّحي القاعدة بمعنى، مو بس
تشابه حروف. أهم جزء هنا فعلياً هو check_attribute_conflict: صنفين بنفس
الاسم تقريباً لكن بحجم/عدد مختلف هم صنفان مختلفان جوهرياً، مو مجرد اختلاف
صياغة - هذا بالضبط اللي يمنع مطابقة "250 مل" مع "330 مل" بالغلط.
"""

import re
from dataclasses import dataclass

_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_TASHKEEL_RE = re.compile(r"[ؗ-ًؚ-ْٰ]")

# وحدات الحجم/الوزن المعروفة، بكل الأشكال اللي شفناها فعلياً - كل مجموعة
# تحوّل لأصغر وحدة بنفس عائلتها (وزن=غرام، حجم=مل) عشان "1كغ" == "1000غ"
_WEIGHT_UNITS = {
    "g": 1, "gm": 1, "gram": 1, "غ": 1, "جم": 1, "جرام": 1, "غم": 1,
    "kg": 1000, "كغ": 1000, "كجم": 1000, "كيلو": 1000, "كيلوجرام": 1000,
}
_VOLUME_UNITS = {
    "ml": 1, "مل": 1, "مللتر": 1, "ملل": 1,
    "l": 1000, "لتر": 1000, "لترات": 1000,
}
_ALL_UNITS = {**_WEIGHT_UNITS, **_VOLUME_UNITS}

# كلمات تعبئة/بيع معروفة - مرتبة من "تعبئة خارجية" إلى "وحدة عد داخلية"،
# عشان "كرتون 24 حبة" ياخذ unit_word="كرتون" (التعبئة الفعلية المطبوعة)
# مو "حبة" (وحدة العد بالداخل)
_OUTER_PACK_WORDS = {
    "كرتون": "carton", "كراتين": "carton", "carton": "carton", "ctn": "carton",
    "صندوق": "box", "علبة": "box", "box": "box",
    "كيس": "bag", "اكياس": "bag", "bag": "bag",
    "بالة": "pallet", "طبلية": "pallet", "pallet": "pallet",
    "دستة": "dozen", "دزينة": "dozen", "dozen": "dozen",
}
_INNER_UNIT_WORDS = {
    "حبة": "piece", "حبات": "piece", "قطعة": "piece", "قطع": "piece",
    "piece": "piece", "pieces": "piece", "pcs": "piece", "pc": "piece",
}
_UNIT_WORD_CANON = {**_OUTER_PACK_WORDS, **_INNER_UNIT_WORDS}

# كلمات وصفية عامة (نوع تعبئة/حالة) - مو أسماء ماركات، تُستبعد من تخمين
# الماركة لو طلعت أول كلمة بالنص
_GENERIC_LEADING_WORDS = {
    "علب", "علبة", "عبوة", "عبوات", "زجاجة", "زجاج", "كيس", "اكياس", "كرتون",
    "صندوق", "قطعة", "قطع", "حبة", "حبات", "وزن", "صافي", "جديد", "عرض",
}


def _norm_word(word: str) -> str:
    """يطبّق نفس تحويل ة->ه اللي يصير للنص المُدخَل (_normalize_text)، عشان
    كلمات القاموس (مكتوبة بإملاء طبيعي فيه ة) تطابق النص بعد التطبيع.
    مُعرّفة هنا (قبل أي استخدام) بدل تكرارها بآخر الملف."""
    return word.replace("ة", "ه")


# نسخ مطبَّعة (ة->ه) من كل القواميس أعلاه - تُستخدم فعلياً بكل عمليات
# البحث بالنص المطبَّع، عشان ما نكرر _norm_word() بكل موقع استخدام ونضمن
# ما راح ننسى موقع ونطابق بالخطأ
_OUTER_PACK_WORDS_NORM = {_norm_word(k): v for k, v in _OUTER_PACK_WORDS.items()}
_INNER_UNIT_WORDS_NORM = {_norm_word(k): v for k, v in _INNER_UNIT_WORDS.items()}
_GENERIC_LEADING_WORDS_NORM = {_norm_word(w) for w in _GENERIC_LEADING_WORDS}
_UNIT_WORD_CANON_NORM = {_norm_word(k) for k in _UNIT_WORD_CANON}

_NUMBER = r"(\d+(?:[.,]\d+)?)"
_UNIT_ALT = "|".join(sorted(_ALL_UNITS, key=len, reverse=True))
# نمط مجمّع شائع جداً بالفواتير - بالترتيبين المحتملين:
# "12x330ml" (عدد ثم حجم) و"330ml x 24" / "330 مل × 24" (حجم ثم عدد)
_COUNT_THEN_SIZE_RE = re.compile(rf"{_NUMBER}\s*x\s*{_NUMBER}\s*({_UNIT_ALT})\b", re.IGNORECASE)
_SIZE_THEN_COUNT_RE = re.compile(rf"{_NUMBER}\s*({_UNIT_ALT})\s*x\s*{_NUMBER}\b", re.IGNORECASE)
# نمط حجم مفرد: "250 مل" أو "1.5لتر" أو "1800كغ"
_SIZE_RE = re.compile(rf"{_NUMBER}\s*({_UNIT_ALT})\b", re.IGNORECASE)


@dataclass
class ItemAttributes:
    normalized_text: str
    brand: str | None = None
    size_value: float | None = None
    size_unit: str | None = None
    size_value_base: float | None = None  # بأصغر وحدة بنفس العائلة (غرام أو مل)
    pack_count: int | None = None
    unit_word: str | None = None  # "carton" | "box" | "bag" | "pallet" | "dozen" | "piece"


def _normalize_text(text: str) -> str:
    text = text.translate(_ARABIC_DIGITS)
    text = _TASHKEEL_RE.sub("", text)
    text = text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا").replace("ة", "ه").replace("ى", "ي")
    text = text.replace("×", "x").replace("*", "x").replace("Х", "x").replace("X", "x")
    text = text.casefold()
    text = re.sub(r"[^\w\s.x]", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _unit_family_and_base(value: float, unit_key: str) -> tuple[str, float]:
    unit_key = unit_key.lower()
    if unit_key in _WEIGHT_UNITS:
        return "weight", value * _WEIGHT_UNITS[unit_key]
    return "volume", value * _VOLUME_UNITS[unit_key]


def extract_attributes(text: str) -> ItemAttributes:
    if not text:
        return ItemAttributes(normalized_text="")

    norm = _normalize_text(text)
    attrs = ItemAttributes(normalized_text=norm)

    # 1) نمط "عدد × حجم" المجمّع أولاً (أدق - يمسك عدد القطع والحجم معاً)،
    # جرّب الترتيبين (عدد ثم حجم، أو حجم ثم عدد - شفنا الاثنين بفواتير حقيقية)
    m = _COUNT_THEN_SIZE_RE.search(norm)
    if m:
        pack_count_raw, size_raw, unit_raw = m.group(1), m.group(2), m.group(3)
    else:
        m = _SIZE_THEN_COUNT_RE.search(norm)
        if m:
            size_raw, unit_raw, pack_count_raw = m.group(1), m.group(2), m.group(3)
        else:
            pack_count_raw = size_raw = unit_raw = None

    if pack_count_raw is not None:
        attrs.pack_count = int(float(pack_count_raw.replace(",", ".")))
        size_value = float(size_raw.replace(",", "."))
        attrs.size_value = size_value
        attrs.size_unit = unit_raw.lower()
        _, attrs.size_value_base = _unit_family_and_base(size_value, unit_raw)
    else:
        m = _SIZE_RE.search(norm)
        if m:
            size_value = float(m.group(1).replace(",", "."))
            unit_raw = m.group(2)
            attrs.size_value = size_value
            attrs.size_unit = unit_raw.lower()
            _, attrs.size_value_base = _unit_family_and_base(size_value, unit_raw)

    # 2) عدد القطع لوحده لو ما انلقط بالنمط المجمّع (مثال: "كرتون 24 حبة")
    if attrs.pack_count is None:
        for word in _INNER_UNIT_WORDS_NORM:
            pack_m = re.search(rf"\b(\d+)\s*{re.escape(word)}\b", norm)
            if pack_m:
                attrs.pack_count = int(pack_m.group(1))
                break

    # 3) نوع التعبئة - التعبئة الخارجية أولى من وحدة العد الداخلية
    attrs.unit_word = _find_unit_word(norm)

    # 4) ماركة - تخمين ضعيف عمداً: أول كلمة غير رقمية وغير وصفية عامة
    tokens = norm.split()
    for tok in tokens:
        if not tok:
            continue
        if re.match(r"^\d", tok):
            break  # وصلنا لرقم (حجم غالباً) - نوقف تخمين الماركة
        if tok in _GENERIC_LEADING_WORDS_NORM or tok in _UNIT_WORD_CANON_NORM:
            continue
        attrs.brand = tok
        break

    return attrs


def _find_unit_word(norm: str) -> str | None:
    """يبحث عن نوع التعبئة بنص مطبَّع مسبقاً (التعبئة الخارجية أولى من وحدة
    العد الداخلية). مستخدَمة داخلياً من extract_attributes ومن
    canonicalize_unit_word معاً - عشان يبقى نفس المنطق بمكان واحد."""
    for word, canon in _OUTER_PACK_WORDS_NORM.items():
        if re.search(rf"\b{re.escape(word)}\b", norm):
            return canon
    for word, canon in _INNER_UNIT_WORDS_NORM.items():
        if re.search(rf"\b{re.escape(word)}\b", norm):
            return canon
    return None


def size_family(unit: str | None) -> str | None:
    """يرجّع "weight" أو "volume" حسب وحدة الحجم/الوزن، أو None لو الوحدة
    غير معروفة. مستخدمة من matching_engine.py لبناء فهرس استرجاع حسب عائلة
    الحجم (نفس منطق التصنيف الداخلي بـcheck_attribute_conflict، بس مكشوف
    للاستخدام من برّا هذا الملف)."""
    if not unit:
        return None
    unit = unit.lower()
    if unit in _WEIGHT_UNITS:
        return "weight"
    if unit in _VOLUME_UNITS:
        return "volume"
    return None


def canonicalize_unit_word(text: str) -> str | None:
    """يطبّق نفس منطق البحث عن نوع التعبئة الموجود بـextract_attributes، بس
    على نص قصير مباشر (مثلاً حقل "الوحدة" الصريح - line.unit أو
    ref_item.default_unit) بدل وصف صنف كامل. يُستخدم من matching_engine.py
    للمقارنة المباشرة بين الوحدة المطبوعة بالفاتورة والوحدة المسجّلة
    بالقاعدة - إشارة منفصلة تماماً عن unit_word المستنتج من اسم الصنف
    (ممكن الاسم ما يذكر الوحدة إطلاقاً، بينما الحقل الصريح موجود)."""
    if not text:
        return None
    return _find_unit_word(_normalize_text(text))


def size_status(a: ItemAttributes, b: ItemAttributes) -> str | None:
    """'agree' / 'conflict' / None (غير قابل للمقارنة - ناقص بطرف أو
    الاثنين، أو عائلتين مختلفتين وزن/حجم فمو قابلين للمقارنة أصلاً). تسامح
    5% (فروق تقريب الطباعة/التحويل)."""
    if a.size_value_base is None or b.size_value_base is None:
        return None
    a_family = "weight" if a.size_unit in _WEIGHT_UNITS else "volume"
    b_family = "weight" if b.size_unit in _WEIGHT_UNITS else "volume"
    if a_family != b_family:
        return None
    bigger = max(a.size_value_base, b.size_value_base)
    smaller = min(a.size_value_base, b.size_value_base)
    if bigger <= 0:
        return None
    return "conflict" if (bigger - smaller) / bigger > 0.05 else "agree"


def pack_count_status(a: ItemAttributes, b: ItemAttributes) -> str | None:
    """'agree' / 'conflict' / None - لازم يتطابق تماماً (عدد قطع مو رقم
    تقريبي)."""
    if a.pack_count is None or b.pack_count is None:
        return None
    return "agree" if a.pack_count == b.pack_count else "conflict"


def unit_word_status(a: ItemAttributes, b: ItemAttributes) -> str | None:
    """'agree' / 'conflict' / None - لازم يتطابق تماماً (كرتون ≠ حبة)."""
    if a.unit_word is None or b.unit_word is None:
        return None
    return "agree" if a.unit_word == b.unit_word else "conflict"


def check_attribute_conflict(a: ItemAttributes, b: ItemAttributes) -> tuple[bool, str]:
    """True + سبب لو خاصية مكتشفة بالطرفين تختلف فعلياً - لو خاصية غير
    مكتشفة بأي طرف، ما نعاقب عليها (مو تعارض، بس معلومة ناقصة). مبنية من 3
    فحوصات منفصلة (size_status/pack_count_status/unit_word_status) -
    matching_engine.py يستخدم نفس الفحوصات فردياً لحساب مكافأة/عقوبة كل
    خاصية على حدة بتقييمه متعدد العوامل، مو بس تعارض/لا-تعارض مجمّع."""
    if size_status(a, b) == "conflict":
        return True, f"اختلاف الحجم/الوزن: {a.size_value}{a.size_unit} مقابل {b.size_value}{b.size_unit}"
    if pack_count_status(a, b) == "conflict":
        return True, f"اختلاف عدد القطع: {a.pack_count} مقابل {b.pack_count}"
    if unit_word_status(a, b) == "conflict":
        return True, f"اختلاف نوع التعبئة: {a.unit_word} مقابل {b.unit_word}"
    return False, ""
