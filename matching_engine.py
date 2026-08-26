"""
محرك المطابقة الذكي المتدرّج - يعمل فقط على أسطر ما تطابقت بالباركود
(المتصل مسؤول عن هذا الفحص، راجع app.py::_extract_one_invoice). لا يلمس
items.py ولا match_line_items() إطلاقاً - يُستدعى بعدها، مكمّل لها.

أولوية المطابقة هنا لأي سطر بدون باركود:
1. مطابقة متعلّمة سابقة (نفس المورد + نفس/قريب من نص الصنف) - learned_matches.py،
   تتصدّر دايماً بغض النظر عن نتائج بقية المصادر.
2. استرجاع مرشّحين من عدة مصادر مستقلة (خصائص بنيوية: حجم/عدد قطع/تعبئة/
   وحدة صريحة، تاريخ المورد، الماركة، تشابه الاسم) - مو مصدر واحد (تشابه
   الاسم) زي التصميم القديم. صنف بصياغة مختلفة جداً عن اسم الفاتورة، لكن
   بنفس الحجم/العدد/الماركة، ما يُستبعد بصمت قبل حتى فحص خصائصه.
3. تقييم متعدد العوامل حقيقي (اسم + حجم + عدد + تعبئة + وحدة صريحة + ماركة +
   تاريخ مورد + سعر) - كل عامل مكافأة/عقوبة مستقلة، مو "اسم + مكافأة بسيطة".

أي مرشّح، من أي مصدر (حتى المتعلّمة - احتياط لو تغيّرت تعبئة المورد)، يمر
بفحص تعارض الخصائص كخطوة أخيرة: لو تعارض بنيوي حقيقي (حجم/عدد/تعبئة/وحدة
مختلف فعلياً)، الثقة تُقصّ بشدة بغض النظر عن مصدرها أو تشابه الاسم. تشابه
الاسم وحده (بدون أي إشارة داعمة) أبداً ما يوصل لثقة قبول تلقائي - راجع
MATCH_NO_SUPPORT_CONFIDENCE_CAP. حتى لو أفضل مرشّح عدّى عتبة القبول
التلقائي، لو ثاني أفضل مرشّح قريب منه جداً (فجوة غموض) نرفض القبول التلقائي
ونحيلها لمراجعة بشرية - راجع MATCH_MIN_CONFIDENCE_GAP بـenhance_one().
"""

import math
from collections import defaultdict
from dataclasses import dataclass, field

from rapidfuzz import fuzz, process

import config
import learned_matches
import settings as settings_module
from item_attributes import (
    ItemAttributes,
    canonicalize_unit_word,
    extract_attributes,
    pack_count_status,
    size_family,
    size_status,
    unit_word_status,
)
from items import ReferenceItem, _apply_match
from line_item import ExtractedLine

_NAME_POOL_SIZE = 30  # مرشّحين بالتشابه الاسمي الخام - مصدر واحد من عدة مصادر الآن، مو الوحيد
_MAX_CANDIDATES_BEFORE_SCORING = 80  # سقف المجموع بعد دمج كل المصادر، قبل التقييم الكامل (بند 7)
_SIZE_BUCKET_LOG_STEP = math.log(1.05)  # نفس نسبة تسامح 5% المستخدمة بـitem_attributes.size_status
_MIN_BRAND_TOKEN_LEN = 3  # حد أدنى لطول الماركة عشان "نثق" فيها بما يكفي للاسترجاع/المكافأة/العقوبة


@dataclass
class MatchCandidate:
    item: ReferenceItem
    confidence: float
    reason: str
    has_structural_conflict: bool = False  # حجم/عدد قطع/تعبئة/وحدة صريحة - يُستثنى من فلترة الثقة الدنيا (مو تعارض الماركة، أضعف/أكثر ضجيجاً)
    from_supplier_history: bool = False  # مسترجَع (كمان) عبر تاريخ المورد - Boost قوي يستاهل يبان حتى لو باقي الأدلة ضعيفة، بس أبداً ما يحتكر القبول التلقائي لوحده


@dataclass
class ReferenceIndex:
    """فهارس محسوبة مرة وحدة لكل تحميل قائمة مرجعية - تُمرَّر لكل نداءات
    matching_engine بدل إعادة حسابها لكل سطر فاتورة (القائمة ممكن تكون
    عشرات الآلاف من الأصناف، تأكدنا فعلياً من ملف حقيقي 35,699 صف). كل
    فهرس: مفتاح خاصية -> قائمة أرقام صفوف (indices) بـreference تحمل هذي
    الخاصية. هذا هو أساس الاسترجاع متعدد المصادر - بدل مسح القائمة كاملة
    بحثاً عن تشابه اسمي فقط لكل سطر فاتورة.

    القيمة المُرجعة من build_reference_attrs_index() (الاسم نفسه محفوظ
    للتوافق - app.py يستخدمها كصندوق أسود بدون فحص نوعها، صفر تعديل هناك)."""

    attrs: dict[int, ItemAttributes]
    by_size_bucket: dict[tuple[str, int], list[int]] = field(default_factory=dict)
    by_pack_count: dict[int, list[int]] = field(default_factory=dict)
    by_unit_word: dict[str, list[int]] = field(default_factory=dict)
    by_unit_field: dict[str, list[int]] = field(default_factory=dict)
    by_brand: dict[str, list[int]] = field(default_factory=dict)
    by_code: dict[str, list[int]] = field(default_factory=dict)


def _size_bucket_key(value: float) -> int:
    """تجميع لوغاريتمي بخطوة 5% - قيمتين بفارق ≤5% تقريباً تقعان بنفس
    الصندوق أو صندوق مجاور، بغض النظر عن حجم القيمة نفسها (يشتغل نفس الشي
    لقيم صغيرة كـ50غ وكبيرة كـ10000غ)."""
    return round(math.log(value) / _SIZE_BUCKET_LOG_STEP)


def _size_buckets_for(value: float) -> tuple[int, int, int]:
    """3 صناديق حول القيمة (احتياط لحدود التقريب قرب عتبة الـ5%) - تُستخدم
    وقت الاسترجاع فقط؛ الفهرسة نفسها تخزّن كل صنف بصندوقه المضبوط بس."""
    center = _size_bucket_key(value)
    return (center - 1, center, center + 1)


def build_reference_attrs_index(reference: list[ReferenceItem]) -> ReferenceIndex:
    """يبني فهرس متعدد الخصائص لقائمة الأصناف المرجعية - مرة وحدة لكل دفعة
    فواتير، يُمرَّر لبقية دوال هذا الملف بدل إعادة الحساب لكل سطر."""
    index = ReferenceIndex(attrs={})
    for i, item in enumerate(reference):
        attrs = extract_attributes(item.name)
        index.attrs[i] = attrs

        if attrs.size_value_base is not None:
            family = size_family(attrs.size_unit)
            if family is not None:
                index.by_size_bucket.setdefault((family, _size_bucket_key(attrs.size_value_base)), []).append(i)
        if attrs.pack_count is not None:
            index.by_pack_count.setdefault(attrs.pack_count, []).append(i)
        if attrs.unit_word is not None:
            index.by_unit_word.setdefault(attrs.unit_word, []).append(i)
        unit_field_canon = canonicalize_unit_word(item.default_unit)
        if unit_field_canon is not None:
            index.by_unit_field.setdefault(unit_field_canon, []).append(i)
        if attrs.brand and len(attrs.brand) >= _MIN_BRAND_TOKEN_LEN:
            index.by_brand.setdefault(attrs.brand, []).append(i)
        if item.code:
            index.by_code.setdefault(item.code, []).append(i)

    return index


def _find_by_code(reference: list[ReferenceItem], code: str) -> ReferenceItem | None:
    for item in reference:
        if item.code == code:
            return item
    return None


def _retrieve_candidate_hits(
    line: ExtractedLine,
    line_attrs: ItemAttributes,
    reference: list[ReferenceItem],
    reference_index: ReferenceIndex,
    supplier_confirmed_codes: set[str],
) -> dict[int, set[str]]:
    """يرجّع {رقم صف بالمرجع: مجموعة أسماء المصادر اللي رشّحته} - عدة
    مصادر مستقلة بدل الاعتماد على تشابه الاسم وحده. صنف ورد من أكثر من
    مصدر يترجّح تلقائياً بترتيب الاسترجاع (_rank_and_cap_candidates)."""
    hits: dict[int, set[str]] = defaultdict(set)

    # مصدر 1: خصائص بنيوية - استرجاع مباشر من فهارس محسوبة مسبقاً، بدون أي
    # حاجة لتشابه بالاسم إطلاقاً (هذا يحل المشكلة الأساسية: صنف بصياغة اسم
    # مختلفة جداً لسا يُسترجع لو حجمه/عدده/تعبئته متطابقة)
    if line_attrs.size_value_base is not None:
        family = size_family(line_attrs.size_unit)
        if family is not None:
            for bucket in _size_buckets_for(line_attrs.size_value_base):
                for idx in reference_index.by_size_bucket.get((family, bucket), ()):
                    hits[idx].add("size")
    if line_attrs.pack_count is not None:
        for idx in reference_index.by_pack_count.get(line_attrs.pack_count, ()):
            hits[idx].add("pack")
    if line_attrs.unit_word is not None:
        for idx in reference_index.by_unit_word.get(line_attrs.unit_word, ()):
            hits[idx].add("unit_word")
    line_unit_canon = canonicalize_unit_word(line.unit)
    if line_unit_canon is not None:
        for idx in reference_index.by_unit_field.get(line_unit_canon, ()):
            hits[idx].add("unit_field")

    # مصدر 2: تاريخ المورد - أي رقم صنف سبق تأكيده لنفس المورد، بغض النظر
    # عن نص الوصف الحالي. Boost قوي بالتقييم لاحقاً، مو فلتر استبعاد هنا -
    # نضيفه للمرشّحين فقط، ما نمنع أي صنف ثاني من الظهور بسببه
    for code in supplier_confirmed_codes:
        for idx in reference_index.by_code.get(code, ()):
            hits[idx].add("supplier_history")

    # مصدر 3: الماركة (تخمين ضعيف عمداً بـitem_attributes.py) - توسيع فقط،
    # أضعف مصدر، ما نعتمد عليه لوحده لاسترجاع مرشّح بثقة
    if line_attrs.brand and len(line_attrs.brand) >= _MIN_BRAND_TOKEN_LEN:
        for idx in reference_index.by_brand.get(line_attrs.brand, ()):
            hits[idx].add("brand")

    # مصدر 4: تشابه الاسم (الموجود سابقاً كمصدر وحيد) - يبقى مصدر واحد من
    # عدة مصادر الآن، مو المصدر الوحيد اللي يحدّد مين يوصل لمرحلة التقييم
    if line.description and reference:
        choices = {i: item.name for i, item in enumerate(reference)}
        pool = process.extract(line.description, choices, scorer=fuzz.token_sort_ratio, limit=_NAME_POOL_SIZE)
        for _, _score, idx in pool:
            hits[idx].add("name")

    return hits


_STRUCTURAL_SOURCES = frozenset({"size", "pack", "unit_word", "unit_field"})


def _candidate_priority_tier(sources: set[str]) -> int:
    """رتبة أولوية (0 = الأعلى) - تُستخدم للترتيب قبل القصّ للسقف الأقصى،
    عشان خاصية بنيوية شائعة جداً (مثلاً "1 لتر" لوحدها بقائمة ضخمة) ما تملأ
    السقف على حساب مرشّحين من مصادر أدقّ/أقوى دلالة. الترتيب المطلوب صراحة:
    تاريخ المورد أولاً، بعده تقاطع خصائص بنيوية متعددة، بعده ماركة+خاصية
    بنيوية، بعده تشابه اسم حقيقي، وأخيراً خاصية بنيوية وحيدة شائعة."""
    if "supplier_history" in sources:
        return 0
    structural_hits = sources & _STRUCTURAL_SOURCES
    if len(structural_hits) >= 2:
        return 1
    if "brand" in sources and structural_hits:
        return 2
    if "name" in sources:
        return 3
    return 4  # مصدر بنيوي وحيد بس (مثلاً حجم فقط) - أضعف أولوية، أول من يُقصّ


# حصص محجوزة لكل رتبة أولوية (راجع _candidate_priority_tier) - مو مجرد
# ترتيب، لأن رتبة "الأعلى أولوية" (تاريخ المورد) نفسها ممكن تفيض عن السقف
# (مثلاً مورد عنده أكثر من 100 صنف مؤكَّد سابقاً) وتبتلع كل الحصة على حساب
# مرشّح اسم قوي برتبة أضعف. كل رتبة محفوظ لها حد أقصى، والفائض (لو رتبة
# استخدمت أقل من حصتها) يتوزّع على باقي الرتب بترتيب الأولوية نفسه - الرتبة
# الأخيرة (4: خاصية بنيوية وحيدة شائعة) بلا حصة مخصصة، تاخذ أي شي يفضل.
_TIER_QUOTAS = {0: 25, 1: 20, 2: 10, 3: 20}


def _rank_and_cap_candidates(hits: dict[int, set[str]]) -> list[int]:
    """يقصّ المرشّحين لسقف أقصى قبل التقييم الكامل - يحمي الأداء لو خاصية
    شائعة جداً (مثلاً "1 لتر" بقائمة 35 ألف صنف) رجّعت مئات المرشّحين من
    مصدر واحد بس. **مو ترتيب عالمي بس** - كل رتبة أولوية لها حصة محجوزة
    (_TIER_QUOTAS)، عشان رتبة عالية الأولوية بس فياضة العدد (مثلاً تاريخ
    مورد عنده مئات الأصناف) ما تبتلع السقف كامل وتُسقط مرشّح اسم قوي برتبة
    أضعف - الحصص تضمن كل رتبة تاخذ نصيبها الأدنى المضمون قبل أي فائض يتوزّع."""
    by_tier: dict[int, list[int]] = defaultdict(list)
    for idx, sources in hits.items():
        by_tier[_candidate_priority_tier(sources)].append(idx)
    for tier_indices in by_tier.values():
        tier_indices.sort(key=lambda idx: -len(hits[idx]))

    selected: list[int] = []
    selected_set: set[int] = set()
    for tier in sorted(by_tier.keys()):
        quota = _TIER_QUOTAS.get(tier, _MAX_CANDIDATES_BEFORE_SCORING)
        remaining_cap = _MAX_CANDIDATES_BEFORE_SCORING - len(selected)
        take = min(quota, remaining_cap, len(by_tier[tier]))
        if take <= 0:
            continue
        chosen = by_tier[tier][:take]
        selected.extend(chosen)
        selected_set.update(chosen)

    # فائض: رتبة استخدمت أقل من حصتها المحجوزة تسيب مجال بالسقف - نعبّيه من
    # أي مرشّح لسا ما أُخذ، بنفس ترتيب أولوية الرتب (الأعلى أولوية أولاً)
    if len(selected) < _MAX_CANDIDATES_BEFORE_SCORING:
        for tier in sorted(by_tier.keys()):
            if len(selected) >= _MAX_CANDIDATES_BEFORE_SCORING:
                break
            for idx in by_tier[tier]:
                if idx in selected_set:
                    continue
                selected.append(idx)
                selected_set.add(idx)
                if len(selected) >= _MAX_CANDIDATES_BEFORE_SCORING:
                    break

    return selected


def _price_signal(line_price: float | None, ref_item: ReferenceItem) -> tuple[float, str | None]:
    """إشارة سعر - غير مفعّلة فعلياً حالياً لأن ReferenceItem ما فيه
    last_purchase_price بعد (getattr الآمن يخليها معطّلة بهدوء بدون كسر
    شي، بدل توسيع قاعدة البيانات الآن لمجرد هذي المرحلة). التصميم جاهز لما
    تتوفر بيانات سعر مستقبلاً: فرق بسيط = مكافأة خفيفة، فرق كبير جداً (3x+)
    = عقوبة/تحذير - أبداً مو سبب رفض وحيد (ممكن يكون فرق حبة/كرتون، خصم،
    تغيّر سعر، أو ضريبة، مو بالضرورة صنف غلط)."""
    ref_price = getattr(ref_item, "last_purchase_price", None)
    if line_price is None or not ref_price or ref_price <= 0:
        return 0.0, None
    bigger = max(line_price, ref_price)
    smaller = min(line_price, ref_price)
    ratio = bigger / smaller
    if ratio <= 1.1:
        return 5.0, "السعر قريب جداً من آخر سعر شراء"
    if ratio <= 1.3:
        return 2.0, "السعر قريب من آخر سعر شراء"
    if ratio >= 3.0:
        return -10.0, f"⚠ فرق سعر كبير جداً ({ratio:.1f}x) - راجع يدوياً (ممكن وحدة مختلفة/خصم/ضريبة)"
    return 0.0, None


def _score_one_candidate(
    line: ExtractedLine,
    line_attrs: ItemAttributes,
    ref_item: ReferenceItem,
    ref_attrs: ItemAttributes,
    sources: set[str],
) -> MatchCandidate:
    """تقييم متعدد العوامل حقيقي: كل عامل (اسم/حجم/عدد/تعبئة/وحدة صريحة/
    ماركة/تاريخ مورد/سعر) مستقل، يضيف مكافأته لوحده بدل معادلة "اسم + بونص
    بسيط". التعارضات البنيوية (حجم/عدد/تعبئة/وحدة) تُجمَّع بسقف واحد
    (hard_cap) يُطبَّق **بعد** جمع كل المكافآت - عشان مكافأة متأخرة (مثلاً
    عدد قطع متطابق) ما "تفكّ" سقف تعارض سابق (مثلاً حجم مختلف) بالغلط.
    تعارض الماركة **ليس** له سقف صارم (راجع config.MATCH_BRAND_MISMATCH_PENALTY) -
    استخراج الماركة تخمين ضعيف بدون قاموس ماركات موثوق، فمعاملته كتعارض
    بنيوي مؤكد كان يرفض مرشّحين صحيحين بالغلط."""
    name_score = fuzz.token_sort_ratio(line.description, ref_item.name) if line.description else 0.0
    bonus = 0.0
    reason_parts = [f"الاسم {name_score:.0f}%"]
    supported = False
    hard_cap = 100.0

    size_stat = size_status(line_attrs, ref_attrs)
    if size_stat == "agree":
        bonus += 10
        reason_parts.append("الحجم/الوزن متطابق")
        supported = True
    elif size_stat == "conflict":
        hard_cap = min(hard_cap, config.MATCH_ATTRIBUTE_CONFLICT_CAP)
        reason_parts.append(
            f"⚠ اختلاف الحجم/الوزن: {line_attrs.size_value}{line_attrs.size_unit} مقابل "
            f"{ref_attrs.size_value}{ref_attrs.size_unit}"
        )

    pack_stat = pack_count_status(line_attrs, ref_attrs)
    if pack_stat == "agree":
        bonus += 8
        reason_parts.append("عدد القطع متطابق")
        supported = True
    elif pack_stat == "conflict":
        hard_cap = min(hard_cap, config.MATCH_ATTRIBUTE_CONFLICT_CAP)
        reason_parts.append(f"⚠ اختلاف عدد القطع: {line_attrs.pack_count} مقابل {ref_attrs.pack_count}")

    unit_word_stat = unit_word_status(line_attrs, ref_attrs)
    if unit_word_stat == "agree":
        bonus += 7
        reason_parts.append("نوع التعبئة متطابق")
        supported = True
    elif unit_word_stat == "conflict":
        hard_cap = min(hard_cap, config.MATCH_ATTRIBUTE_CONFLICT_CAP)
        reason_parts.append(f"⚠ اختلاف نوع التعبئة: {line_attrs.unit_word} مقابل {ref_attrs.unit_word}")

    # الوحدة الصريحة (line.unit مقابل ref_item.default_unit) - منفصلة عمداً
    # عن unit_word (المستنتج من نص الاسم)، لأن الحقل الصريح موجود حتى لو
    # الاسم نفسه ما يذكر الوحدة إطلاقاً
    line_unit_canon = canonicalize_unit_word(line.unit)
    ref_unit_canon = canonicalize_unit_word(ref_item.default_unit)
    if line_unit_canon is not None and ref_unit_canon is not None:
        if line_unit_canon == ref_unit_canon:
            bonus += 6
            reason_parts.append("الوحدة الصريحة متطابقة")
            supported = True
        else:
            hard_cap = min(hard_cap, config.MATCH_ATTRIBUTE_CONFLICT_CAP)
            reason_parts.append(f"⚠ اختلاف الوحدة الصريحة: {line.unit} مقابل {ref_item.default_unit}")

    # الماركة - إشارة ضعيفة عمداً (استخراج تخميني، أول كلمة غير وصفية
    # بالنص، بدون قاموس ماركات معروفة أو أي قياس ثقة حقيقي - كلمة نوع منتج
    # عامة زي "عصير"/"حليب" ممكن تُقرأ كماركة بالغلط). موافقتها Boost خفيف
    # فقط، واختلافها عقوبة خفيفة تراكمية - **مو سقف صارم يمنع القبول
    # التلقائي بمفرده**، عشان تخمين ماركة غلط ما يرفض مرشّح صحيح فعلاً.
    if (
        line_attrs.brand
        and ref_attrs.brand
        and len(line_attrs.brand) >= _MIN_BRAND_TOKEN_LEN
        and len(ref_attrs.brand) >= _MIN_BRAND_TOKEN_LEN
    ):
        if line_attrs.brand == ref_attrs.brand:
            bonus += 5
            reason_parts.append("الماركة متطابقة (تخمين)")
            supported = True
        else:
            bonus -= config.MATCH_BRAND_MISMATCH_PENALTY
            reason_parts.append(f"الماركة مختلفة (تخمين ضعيف): {line_attrs.brand} مقابل {ref_attrs.brand}")

    if "supplier_history" in sources:
        bonus += 6
        reason_parts.append("هذا المورد باع هذا الصنف قبل")
        supported = True

    price_delta, price_note = _price_signal(line.unit_price, ref_item)
    if price_delta:
        bonus += price_delta
        if price_note:
            reason_parts.append(price_note)
        if price_delta > 0:
            supported = True

    confidence = name_score + bonus
    confidence = min(confidence, hard_cap)

    # سقف "الاسم بدون دعم" - آخر خطوة، يمنع تشابه اسم عالي صدفة من الوصول
    # لثقة قبول تلقائي بمفرده. تعارض حقيقي أصلاً يبقى تحته من الأسقف أعلاه،
    # فهذا السقف يهم بس بحالة "ما فيه أي إشارة موافقة ولا متعارضة إطلاقاً"
    if not supported:
        confidence = min(confidence, config.MATCH_NO_SUPPORT_CONFIDENCE_CAP)

    confidence = max(0.0, min(100.0, confidence))
    return MatchCandidate(
        item=ref_item,
        confidence=confidence,
        reason="، ".join(reason_parts),
        has_structural_conflict=hard_cap < 100.0,
        from_supplier_history="supplier_history" in sources,
    )


def _score_all_candidates(
    line: ExtractedLine,
    reference: list[ReferenceItem],
    reference_index: ReferenceIndex,
    supplier_confirmed_codes: set[str],
) -> list[MatchCandidate]:
    if not line.description or not reference:
        return []

    line_attrs = extract_attributes(line.description)
    hits = _retrieve_candidate_hits(line, line_attrs, reference, reference_index, supplier_confirmed_codes)
    candidate_indices = _rank_and_cap_candidates(hits)

    candidates = []
    for idx in candidate_indices:
        ref_item = reference[idx]
        ref_attrs = reference_index.attrs.get(idx) or extract_attributes(ref_item.name)
        candidates.append(_score_one_candidate(line, line_attrs, ref_item, ref_attrs, hits[idx]))

    candidates.sort(key=lambda c: c.confidence, reverse=True)
    return candidates


def _score_learned_candidate(
    line: ExtractedLine,
    line_attrs: ItemAttributes,
    learned_item: ReferenceItem,
    confirm_count: int,
) -> MatchCandidate:
    """يحسب مرشّح الذاكرة المتعلّمة بنفس فحوصات التعارض البنيوي اللي يمر
    فيها أي مرشّح عادي (حجم/عدد قطع/تعبئة مستنتجة من الاسم/وحدة صريحة) -
    عشان ذاكرة قديمة تتعارض مع بيانات الفاتورة الحالية أبداً ما توصل لقبول
    تلقائي، حتى لو ثقتها الأساسية عالية جداً من كثرة التأكيدات القديمة.
    الثقة الأساسية (confirm_count) تبقى "الأولوية القوية" المطلوبة بالتصميم -
    بس تبقى خاضعة لنفس قواعد الأمان، مو محصّنة منها (راجع suggest_candidates:
    هذا المرشّح يشارك بنفس الترتيب العادل مع الباقي، ما يتصدّر بشكل مطلق)."""
    ref_attrs = extract_attributes(learned_item.name)
    confidence = learned_matches.confidence_for_confirm_count(confirm_count)
    reason_parts = [f"مطابقة سابقة مؤكَّدة ({confirm_count} مرة)"]
    hard_cap = 100.0

    size_stat = size_status(line_attrs, ref_attrs)
    if size_stat == "conflict":
        hard_cap = min(hard_cap, config.MATCH_ATTRIBUTE_CONFLICT_CAP)
        reason_parts.append(
            f"⚠ اختلاف الحجم/الوزن: {line_attrs.size_value}{line_attrs.size_unit} مقابل "
            f"{ref_attrs.size_value}{ref_attrs.size_unit}"
        )

    pack_stat = pack_count_status(line_attrs, ref_attrs)
    if pack_stat == "conflict":
        hard_cap = min(hard_cap, config.MATCH_ATTRIBUTE_CONFLICT_CAP)
        reason_parts.append(f"⚠ اختلاف عدد القطع: {line_attrs.pack_count} مقابل {ref_attrs.pack_count}")

    unit_word_stat = unit_word_status(line_attrs, ref_attrs)
    if unit_word_stat == "conflict":
        hard_cap = min(hard_cap, config.MATCH_ATTRIBUTE_CONFLICT_CAP)
        reason_parts.append(f"⚠ اختلاف نوع التعبئة: {line_attrs.unit_word} مقابل {ref_attrs.unit_word}")

    line_unit_canon = canonicalize_unit_word(line.unit)
    ref_unit_canon = canonicalize_unit_word(learned_item.default_unit)
    if line_unit_canon is not None and ref_unit_canon is not None and line_unit_canon != ref_unit_canon:
        hard_cap = min(hard_cap, config.MATCH_ATTRIBUTE_CONFLICT_CAP)
        reason_parts.append(f"⚠ اختلاف الوحدة الصريحة: {line.unit} مقابل {learned_item.default_unit}")

    confidence = min(confidence, hard_cap)
    return MatchCandidate(
        item=learned_item,
        confidence=confidence,
        reason="، ".join(reason_parts),
        has_structural_conflict=hard_cap < 100.0,
    )


def _merge_learned_with_ordinary(
    ordinary: MatchCandidate, learned: MatchCandidate, confirm_count: int
) -> MatchCandidate:
    """يدمج دليل المطابقة العادي (اسم+خصائص+مورد+إلخ) مع دليل الذاكرة
    المتعلّمة لنفس الصنف - بدل استبدال أحدهما بالثاني بالكامل (كان يخفّض
    ثقة مرشّح قوي فعلاً بصمت لمجرد وجود تأكيد ذاكرة واحد أضعف، مثلاً من 98%
    لـ80%). الثقة النهائية = أعلى الاثنين. هذا مكافئ رياضياً لتطبيق نفس سقف
    التعارض البنيوي المشترك على المجموع (max(min(a,cap), min(b,cap)) ==
    min(max(a,b), cap))، لأن الاثنين يحسبان نفس فحص التعارض من نفس الحقائق
    (نفس line_attrs، نفس اسم الصنف) - فتعارض بنيوي حقيقي يبقى سقف فعلي، ما
    يقدر "بوست" الذاكرة يتجاوزه، بينما دليل عادي قوي فعلاً ما ينخفض بسبب
    ذاكرة أضعف."""
    if ordinary.confidence >= learned.confidence:
        winner, note_only = ordinary, True
    else:
        winner, note_only = learned, False

    reason = winner.reason
    if note_only:
        reason += f"، وأيضاً مطابقة سابقة مؤكَّدة ({confirm_count} مرة)"

    return MatchCandidate(
        item=winner.item,
        confidence=winner.confidence,
        reason=reason,
        has_structural_conflict=ordinary.has_structural_conflict or learned.has_structural_conflict,
        from_supplier_history=ordinary.from_supplier_history or learned.from_supplier_history,
    )


def suggest_candidates(
    line: ExtractedLine,
    reference: list[ReferenceItem],
    supplier_name: str | None = None,
    reference_attrs_index: ReferenceIndex | None = None,
    top_n: int = 5,
) -> list[MatchCandidate]:
    """يرجّع أفضل top_n مرشّحين لعرضهم بنافذة المراجعة. مرشّح الذاكرة
    المتعلّمة (لو موجود) يحصل على ثقة أساسية عالية (أولوية/boost قوي
    بالتصميم - راجع learned_matches.confidence_for_confirm_count)، لكنه
    **يشارك بنفس الترتيب العادل** مع باقي المرشّحين حسب الثقة الفعلية بعد
    فحص التعارضات البنيوية (_score_learned_candidate) - مو محصّن أو مثبّت
    بالمرتبة الأولى بشكل مطلق. لو نفس الصنف عنده دليل عادي (اسم/خصائص/مورد)
    ودليل ذاكرة معاً، **يُدمَجان** (_merge_learned_with_ordinary) بدل ما
    يستبدل أحدهما الثاني - ذاكرة أضعف (مثلاً تأكيد واحد بس) أبداً ما تخفّض
    ثقة دليل عادي أقوى فعلاً. ذاكرة قديمة تتعارض مع الفاتورة الحالية
    (حجم/عدد/تعبئة/وحدة مختلف فعلياً) تنزل ثقتها فعلياً، فمرشّح ثاني أسلم
    وأقوى ممكن يتصدّر بدلها - هذا يمنع enhance_one() من اعتبار ذاكرة
    متعارضة "أفضل مرشّح" بصمت."""
    if reference_attrs_index is None:
        reference_attrs_index = build_reference_attrs_index(reference)

    line_attrs = extract_attributes(line.description)
    supplier_confirmed_codes = learned_matches.codes_confirmed_for_supplier(supplier_name)
    all_candidates = _score_all_candidates(line, reference, reference_attrs_index, supplier_confirmed_codes)

    learned = learned_matches.lookup(supplier_name, line.description)
    if learned is not None:
        entry, confirm_count = learned
        learned_item = _find_by_code(reference, entry["matched_item_code"])
        if learned_item is not None:
            learned_candidate = _score_learned_candidate(line, line_attrs, learned_item, confirm_count)
            # الصنف نفسه ممكن يتكرر بعدة صفوف بالمرجع (باركود/وحدة مختلفة) -
            # لو المصادر العادية رجّعت أكثر من صف لنفس الكود، نأخذ أقواهم
            # للدمج (نفس فلسفة "أفضل صف لكل كود" المستخدمة بالدمج النهائي)
            same_code_ordinary = [c for c in all_candidates if c.item.code == learned_candidate.item.code]
            all_candidates = [c for c in all_candidates if c.item.code != learned_candidate.item.code]
            if same_code_ordinary:
                best_ordinary = max(same_code_ordinary, key=lambda c: c.confidence)
                all_candidates.append(_merge_learned_with_ordinary(best_ordinary, learned_candidate, confirm_count))
            else:
                all_candidates.append(learned_candidate)

    all_candidates.sort(key=lambda c: c.confidence, reverse=True)

    # نسمح بمرشّح "معطوب" بتعارض خصائص بنيوي، أو مرشّح من تاريخ المورد،
    # يمر رغم ثقته المنخفضة - سبب الرفض نفسه معلومة مفيدة للمراجع البشري
    # (أو "المورد باع هذا قبل" إشارة يستاهل يشوفها)، ما نبيهم يختفوا بصمت
    rest = [
        c
        for c in all_candidates
        if c.confidence >= config.MIN_SUGGEST_THRESHOLD or c.has_structural_conflict or c.from_supplier_history
    ]

    # الصنف الواحد بالمرجع ممكن يتكرر بعدة صفوف (باركود/وحدة مختلفة لكل
    # صف - راجع items.py::_load_from_amnc_xml) - كلها بنفس الاسم فتاخذ نفس
    # درجة التشابه، فتمتلئ قائمة الاقتراحات بنسخ متطابقة الشكل لنفس الصنف
    # بدل بدائل حقيقية مختلفة. نبقي أفضل صف بس لكل رقم صنف.
    seen_codes = set()
    deduped = []
    for c in rest:
        if c.item.code in seen_codes:
            continue
        seen_codes.add(c.item.code)
        deduped.append(c)

    return deduped[:top_n]


def enhance_one(
    line: ExtractedLine,
    reference: list[ReferenceItem],
    supplier_name: str | None = None,
    reference_attrs_index: ReferenceIndex | None = None,
) -> None:
    """يعيد حساب مطابقة سطر ما تطابق بالباركود من الصفر. يُطبَّق التطابق
    فعلياً (matched_item_code وغيره) بس لو تحقق **كل** مما يلي:
    1. ثقة أفضل مرشّح >= عتبة القبول التلقائي.
    2. الفرق عن ثاني أفضل مرشّح كافٍ (فجوة غموض - config.MATCH_MIN_CONFIDENCE_GAP) -
       مرشّحين متقاربين جداً (مثلاً 96% و94%) تعتبر حالة غامضة تحتاج مراجعة
       بشرية حتى لو الأول عدّى العتبة.
    غير كذا يبقى matched_item_code فاضي (نفس مبدأ الأداة: لا تخمّن) بس
    match_score/match_reason يتعبّون لعرض أقرب اقتراح بجدول المراجعة."""
    if reference_attrs_index is None:
        reference_attrs_index = build_reference_attrs_index(reference)

    candidates = suggest_candidates(line, reference, supplier_name, reference_attrs_index, top_n=2)
    if not candidates:
        line.needs_review = True
        return

    best = candidates[0]
    thresholds = settings_module.get_settings()
    line.match_reason = best.reason

    ambiguous = False
    if len(candidates) >= 2:
        gap = best.confidence - candidates[1].confidence
        min_gap = thresholds.get("min_confidence_gap", config.MATCH_MIN_CONFIDENCE_GAP)
        if gap < min_gap:
            ambiguous = True

    if best.confidence >= thresholds["auto_accept_threshold"] and not ambiguous:
        _apply_match(line, best.item, best.confidence)
    else:
        line.match_score = best.confidence
        line.needs_review = True
        if ambiguous:
            line.match_reason += f"  |  ⚠ مرشّح ثاني قريب جداً ({candidates[1].confidence:.0f}%) - يحتاج مراجعة بشرية"
