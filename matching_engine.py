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
import semantic_matcher
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

# وحدات تعبئة "خارجية" بالجملة (فاتورة المورد غالباً تكتب بها) مقابل "حبة"
# (القاعدة غالباً مسجّلة بسعر أصغر وحدة بيع) - اختلاف طبيعي متوقع بين
# فاتورة جملة وقاعدة صنف، مو دليل صنف مختلف بمفرده (راجع _is_packaging_tier_pair).
_PACKAGING_TIER_OUTER = frozenset({"carton", "box", "bag", "dozen", "pallet"})


def _is_packaging_tier_pair(unit_a: str, unit_b: str) -> bool:
    """True لو الوحدتان بالضبط زوج (تعبئة خارجية بالجملة، حبة) - كرتون/صندوق/
    كيس/دستة/بالة مقابل حبة، بأي اتجاه. مو أي زوج وحدات مختلفتين (مثلاً كرتون
    مقابل كيس يبقى تعارض حقيقي كالمعتاد - غير مطلوب توسيعه لهذا)."""
    pair = {unit_a, unit_b}
    return "piece" in pair and bool(pair & _PACKAGING_TIER_OUTER)


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
        elif (
            _is_packaging_tier_pair(line_unit_canon, ref_unit_canon)
            and name_score >= config.MIN_SUGGEST_THRESHOLD
            and size_stat == "agree"
            and pack_stat != "conflict"
            and unit_word_stat != "conflict"
        ):
            # كرتون/صندوق/كيس.. بالفاتورة مقابل حبة بالقاعدة (أو العكس) طبيعي
            # جداً بفواتير الجملة - القاعدة غالباً مسجّلة بسعر أصغر وحدة بيع
            # بينما المورد يفوتر بالكرتون. **قرار صريح من المستخدم**: هذا
            # وحده ليس تعارض صنف، طالما بقية الأدلة قوية وواضحة (اسم مو
            # ضعيف، حجم/وزن متطابق فعلاً، وعدد القطع/التعبئة إما متطابق أو
            # غير معروف - مو متعارض). الشرط صارم عمداً: لو أي بند ناقص أو
            # فعلاً متعارض، نرجع للسلوك القديم بالـelse أدناه (يبقى سبب مراجعة).
            reason_parts.append(
                f"ملاحظة: وحدة تعبئة مختلفة عن القاعدة بالفاتورة ({line.unit} مقابل {ref_item.default_unit}) - ليست تعارض صنف طالما باقي الأدلة متطابقة"
            )
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
        # نفس استثناء "كرتون بالفاتورة مقابل حبة بالقاعدة" المطبَّق بالتقييم
        # العادي (_score_one_candidate) - بدون شرط اسم هنا (المطابقة أصلاً
        # مؤكَّدة بشرياً سابقاً لنفس الوصف والمورد، هوية الصنف ليست موضع شك،
        # فقط نتأكد بقية الخصائص البنيوية ما تتعارض فعلياً).
        if _is_packaging_tier_pair(line_unit_canon, ref_unit_canon) and size_stat == "agree" and pack_stat != "conflict" and unit_word_stat != "conflict":
            reason_parts.append(
                f"ملاحظة: وحدة تعبئة مختلفة عن القاعدة بالفاتورة ({line.unit} مقابل {learned_item.default_unit}) - ليست تعارض صنف طالما باقي الأدلة متطابقة"
            )
        else:
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


# ============ إعادة الترتيب الدلالي بالذكاء الاصطناعي (طبقة اختيارية) ============
# ملاحظة معمارية مهمة: suggest_candidates() أعلاه تبقى دالة محلية سريعة
# deterministic بدون أي استدعاء شبكي - تُستخدم لوحدها بأي مكان (خصوصاً
# الاختبارات، بدون حاجة لأي محاكاة/موك). طبقة الذكاء الاصطناعي منفصلة
# تماماً هنا (semantic_enhance_candidates) - orchestration فوق
# suggest_candidates، تُستدعى فقط من نقاط التكامل اللي تقبل تأخير شبكي
# محتمل (enhance_one، ونافذتا المراجعة بـapp.py عبر خيط خلفية).


def _should_try_semantic_rerank(candidates: list[MatchCandidate]) -> bool:
    """سياسة التفعيل: استدعِ AI فقط لو القائمة المحلية غير فارغة، و(أفضل
    مرشّح < عتبة القبول التلقائي، أو فجوة الغموض غير كافية). قائمة فارغة
    كلياً تعني مراجعة بشرية مباشرة بدون استدعاء - AI هنا مُعيد ترتيب لقائمة
    موجودة، مو محرك بحث، وممنوع يختار صنفاً غير موجود بالمرشّحين المحليين
    أصلاً. مطابقة باركود/رقم صنف مؤكدة، أو ذاكرة/محلي قوي بلا غموض ولا
    تعارض، أصلاً ما توصل هنا (تُقبل تلقائياً محلياً قبل أي تفكير باستدعاء AI)."""
    if not candidates:
        return False
    thresholds = settings_module.get_settings()
    best = candidates[0]
    if best.confidence < thresholds["auto_accept_threshold"]:
        return True
    if len(candidates) >= 2:
        gap = best.confidence - candidates[1].confidence
        min_gap = thresholds.get("min_confidence_gap", config.MATCH_MIN_CONFIDENCE_GAP)
        if gap < min_gap:
            return True
    return False


def needs_semantic_rerank(candidates: list[MatchCandidate]) -> bool:
    """واجهة عامة رفيعة فوق _should_try_semantic_rerank - يستخدمها app.py
    (بدون الاعتماد على دالة داخلية بادئتها _) عشان يقرر هل يبدأ خيط خلفية
    لإعادة الترتيب الدلالي أو يعرض المرشّحين المحليين فوراً بدون أي تأخير."""
    return _should_try_semantic_rerank(candidates)


def _build_shortlist_for_semantic(
    candidates: list[MatchCandidate], limit: int | None = None
) -> list[MatchCandidate]:
    """يبني قائمة مختصرة متنوعة المصادر لإرسالها لـAI - مو بس أفضل N
    بالثقة المحلية (ممكن يفقد تنوّع مهم، مثلاً مرشّح تاريخ مورد ذو صلة
    حقيقية لكن ثقته المحلية أقل من عدة مرشّحين اسم). نحجز شرائح صغيرة
    لمصادر مميّزة أولاً (تاريخ مورد، تعارض بنيوي - يستاهل AI يشوفه كـ"قريب
    لكن فيه مشكلة")، ثم نعبّي الباقي بالأعلى ثقة عموماً (يغطي تلقائياً
    مرشّحي الاسم/الخصائص القوية، لأن القائمة مرتّبة بالثقة أصلاً)."""
    if limit is None:
        limit = config.SEMANTIC_RERANK_SHORTLIST_SIZE
    if len(candidates) <= limit:
        return list(candidates)

    selected: list[MatchCandidate] = []
    selected_codes: set[str] = set()

    def take(predicate, quota):
        count = 0
        for c in candidates:
            if len(selected) >= limit or count >= quota:
                return
            if c.item.code in selected_codes or not predicate(c):
                continue
            selected.append(c)
            selected_codes.add(c.item.code)
            count += 1

    take(lambda c: c.from_supplier_history, 3)
    take(lambda c: c.has_structural_conflict, 3)

    for c in candidates:
        if len(selected) >= limit:
            break
        if c.item.code in selected_codes:
            continue
        selected.append(c)
        selected_codes.add(c.item.code)

    return selected[:limit]


def _semantic_confidence_boost(ai_confidence: float, ai_ambiguous: bool) -> float:
    """صيغة محافظة عمداً - رقم ثقة AI الخام أبداً ما يساوي ثقة نظامية
    مباشرة. لو AI نفسه أعلن ambiguous=true، صفر بوست (عدم يقين AI ما
    يستاهل رفع ثقة النظام). قيم متحفّظة (8/4/2) مقارنة بإصدار أول اقترحناه
    (15/8/3) - AI بالإصدار الأول أقوى بإعادة الترتيب منه برفع الثقة المطلقة."""
    if ai_ambiguous:
        return 0.0
    if ai_confidence >= 85:
        return 8.0
    if ai_confidence >= 70:
        return 4.0
    if ai_confidence >= 50:
        return 2.0
    return 0.0


_STRONG_LOCAL_SUPPORT_MARKERS = (
    "الحجم/الوزن متطابق",
    "عدد القطع متطابق",
    "نوع التعبئة متطابق",
    "الوحدة الصريحة متطابقة",
    "مطابقة سابقة مؤكَّدة",
)


def _has_strong_local_support(candidate: MatchCandidate) -> bool:
    """"أدلة محلية قوية وواضحة" - شرط إضافي مطلوب عشان AI يُسمح له يساهم
    بعبور عتبة القبول التلقائي (راجع _merge_semantic_result). نطلب إشارتين
    بنيويتين موافقتين على الأقل (أو ذاكرة متعلّمة + إشارة وحدة مثلاً) -
    مجرد "عدم وجود تعارض" لا يكفي، لازم موافقة فعلية مذكورة صراحة بالسبب."""
    hits = sum(1 for marker in _STRONG_LOCAL_SUPPORT_MARKERS if marker in candidate.reason)
    return hits >= 2


def _merge_semantic_result(
    local_candidates: list[MatchCandidate],
    ai_result: semantic_matcher.SemanticRerankResult,
    auto_accept_threshold: float,
) -> tuple[list[MatchCandidate], bool]:
    """يدمج نتيجة AI مع القائمة المحلية. يرجّع (القائمة المحدَّثة،
    ai_was_deciding_factor). العلَم الثاني = True فقط لو AI كان **السبب
    الوحيد** لعبور عتبة القبول التلقائي (المرشّح كان تحتها محلياً، وما فيه
    أدلة محلية قوية داعمة كافية) - بهذا الإصدار الأول، هذي الحالة تبقى
    تحتاج مراجعة بشرية إلزامياً (قرار محافظ متعمَّد، راجع enhance_one)."""
    selected = next((c for c in local_candidates if c.item.code == ai_result.selected_code), None)
    if selected is None:
        # دفاع مضاعف - matching_engine ما يفترض صحة رد semantic_matcher عمياً
        # حتى لو فحصه هو نفسه أصلاً (طبقتان أمان أفضل من طبقة وحدة)
        return local_candidates, False

    boost = _semantic_confidence_boost(ai_result.confidence, ai_result.ambiguous)
    local_hard_cap = config.MATCH_ATTRIBUTE_CONFLICT_CAP if selected.has_structural_conflict else 100.0
    was_below_threshold_locally = selected.confidence < auto_accept_threshold
    boosted_confidence = min(selected.confidence + boost, local_hard_cap, 100.0)

    ai_was_deciding_factor = (
        was_below_threshold_locally
        and boosted_confidence >= auto_accept_threshold
        and not _has_strong_local_support(selected)
    )

    reason_note = f"AI semantic rerank: {ai_result.reason}" if ai_result.reason else "AI semantic rerank: تشابه دلالي قوي"
    if ai_result.ambiguous:
        reason_note += " (AI نفسه غير متأكد)"

    boosted = MatchCandidate(
        item=selected.item,
        confidence=boosted_confidence,
        reason=f"{selected.reason}  |  {reason_note}",
        has_structural_conflict=selected.has_structural_conflict,
        from_supplier_history=selected.from_supplier_history,
    )

    updated = [c for c in local_candidates if c.item.code != ai_result.selected_code]
    updated.append(boosted)
    updated.sort(key=lambda c: c.confidence, reverse=True)
    return updated, ai_was_deciding_factor


def semantic_enhance_candidates(
    line: ExtractedLine,
    reference: list[ReferenceItem],
    supplier_name: str | None = None,
    reference_attrs_index: ReferenceIndex | None = None,
    top_n: int = 5,
) -> tuple[list[MatchCandidate], bool]:
    """طبقة orchestration: تحسب المرشّحين المحليين أولاً (سريع، بدون شبكة)،
    وبس لو سياسة التفعيل قالت الحالة صعبة فعلاً، تستدعي semantic_matcher.rerank()
    (قد يستغرق ثوانٍ - المتصل مسؤول يستدعيها من خيط خلفية لو حساس للتجميد،
    راجع app.py). يرجّع (المرشّحين النهائيين بعد top_n، ai_was_deciding_factor).

    مهم (إصلاح خطأ حقيقي): حجم القائمة المحلية المستخدمة لبناء shortlist
    الذكاء الاصطناعي **مستقل تماماً** عن top_n (عدد النتائج المعروضة
    للمستخدم بنهاية الدالة، يختلف حسب نقطة الاستدعاء - 2/3/5). لو استُخدم
    top_n نفسه لجلب القائمة المحلية، AI ما كان يشوف إلا نفس العدد القليل
    جداً المعروض أصلاً - يفقد بالضبط المرشّح الصحيح لو صياغة اسمه بعيدة
    كفاية عن الفاتورة إنه يترتّب برقم 6 أو 10 محلياً (بالضبط الحالة اللي
    هذي الطبقة أُنشئت عشانها). نجلب pool محلي بحجم
    SEMANTIC_RERANK_SHORTLIST_SIZE على الأقل (suggest_candidates تبقى محلية
    deterministic - top_n هنا مجرد قصّ نهائي على قائمة محسوبة مسبقاً، ما
    يغيّر أي تقييم/ترتيب)، ونقصّ لـtop_n فقط بالنهاية بعد الدمج."""
    pool_size = max(top_n, config.SEMANTIC_RERANK_SHORTLIST_SIZE, 2)
    local_pool = suggest_candidates(line, reference, supplier_name, reference_attrs_index, top_n=pool_size)

    if not _should_try_semantic_rerank(local_pool):
        return local_pool[:top_n], False

    thresholds = settings_module.get_settings()
    line_attrs = extract_attributes(line.description)
    shortlist = _build_shortlist_for_semantic(local_pool)

    ai_inputs = []
    for c in shortlist:
        ref_attrs = extract_attributes(c.item.name)
        ai_inputs.append(
            semantic_matcher.SemanticCandidateInput(
                code=c.item.code,
                name=c.item.name,
                barcode=c.item.barcode,
                unit=c.item.default_unit,
                size_value=ref_attrs.size_value,
                size_unit=ref_attrs.size_unit,
                pack_count=ref_attrs.pack_count,
                unit_word=ref_attrs.unit_word,
                local_confidence=c.confidence,
                local_reason=c.reason,
            )
        )

    ai_result = semantic_matcher.rerank(
        description=line.description,
        supplier_name=supplier_name,
        quantity=line.quantity,
        unit=line.unit,
        unit_price=line.unit_price,
        size_value=line_attrs.size_value,
        size_unit=line_attrs.size_unit,
        pack_count=line_attrs.pack_count,
        unit_word=line_attrs.unit_word,
        candidates=ai_inputs,
    )

    if ai_result is None:
        return local_pool[:top_n], False

    merged, ai_was_deciding_factor = _merge_semantic_result(
        local_pool, ai_result, thresholds["auto_accept_threshold"]
    )
    return merged[:top_n], ai_was_deciding_factor


def enhance_one(
    line: ExtractedLine,
    reference: list[ReferenceItem],
    supplier_name: str | None = None,
    reference_attrs_index: ReferenceIndex | None = None,
    allow_semantic: bool = True,
) -> None:
    """يعيد حساب مطابقة سطر ما تطابق بالباركود من الصفر. لو allow_semantic=True
    (الافتراضي، للتوافق الخلفي مع الاختبارات القديمة)، يحاول أيضاً إعادة
    ترتيب دلالي بالذكاء الاصطناعي للحالات الصعبة (راجع semantic_enhance_candidates).

    قاعدة معمارية صريحة: مسار الاستخراج الفعلي (_extract_one_invoice بـapp.py)
    يستدعي هذي الدالة بـallow_semantic=False دائماً - الاستخراج محلي بحت،
    صفر نداء شبكي لـSemantic AI، حتى لو يشتغل بخيط خلفية أصلاً (كان التبرير
    السابق: "تأخير شبكي محتمل آمن هنا" لأنه خيط خلفية - لكن هذا يعني كل سطر
    "يحتاج مراجعة" بكل فاتورة بالدفعة ينتظر نداء AI متسلسل، وهذا فعلياً كان
    سبب بطء قراءة الفواتير الرئيسي، مو مجرد نظري - قياس فعلي: نداء AI واحد
    ~4.5 ثانية مقابل ~0.08 ثانية للمطابقة المحلية البحتة لنفس السطر). AI يبقى
    حصراً بنافذة المراجعة (_open_review_dialog بـapp.py) عبر خيط خلفية غير
    محاجب - ذاك المسار غير ملموس هنا ولا يستدعي enhance_one إطلاقاً.

    يُطبَّق التطابق فعلياً (matched_item_code وغيره) بس لو تحقق **كل** مما يلي:
    1. ثقة أفضل مرشّح >= عتبة القبول التلقائي.
    2. الفرق عن ثاني أفضل مرشّح كافٍ (فجوة غموض - config.MATCH_MIN_CONFIDENCE_GAP).
    3. لو AI ساهم بالثقة، ما يكون **السبب الوحيد** لعبور العتبة بدون أدلة
       محلية قوية داعمة (راجع _merge_semantic_result - قرار محافظ متعمَّد
       بهذا الإصدار الأول).
    غير كذا يبقى matched_item_code فاضي (نفس مبدأ الأداة: لا تخمّن) بس
    match_score/match_reason يتعبّون لعرض أقرب اقتراح بجدول المراجعة."""
    if reference_attrs_index is None:
        reference_attrs_index = build_reference_attrs_index(reference)

    if allow_semantic:
        candidates, ai_was_deciding_factor = semantic_enhance_candidates(
            line, reference, supplier_name, reference_attrs_index, top_n=2
        )
    else:
        candidates = suggest_candidates(
            line, reference, supplier_name, reference_attrs_index, top_n=2
        )
        ai_was_deciding_factor = False
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

    if best.confidence >= thresholds["auto_accept_threshold"] and not ambiguous and not ai_was_deciding_factor:
        _apply_match(line, best.item, best.confidence)
    else:
        line.match_score = best.confidence
        line.needs_review = True
        if ambiguous:
            line.match_reason += f"  |  ⚠ مرشّح ثاني قريب جداً ({candidates[1].confidence:.0f}%) - يحتاج مراجعة بشرية"
        if ai_was_deciding_factor:
            line.match_reason += (
                "  |  ⚠ الذكاء الاصطناعي هو سبب عبور عتبة القبول لوحده بدون أدلة "
                "محلية قوية كافية - يحتاج مراجعة بشرية"
            )
