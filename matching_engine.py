"""
محرك المطابقة الذكي المتدرّج - يعمل فقط على أسطر ما تطابقت بالباركود
(المتصل مسؤول عن هذا الفحص، راجع app.py::_extract_one_invoice). لا يلمس
items.py ولا match_line_items() إطلاقاً - يُستدعى بعدها، مكمّل لها.

أولوية المطابقة هنا لأي سطر بدون باركود:
1. مطابقة متعلّمة سابقة (نفس المورد + نفس/قريب من نص الصنف) - learned_matches.py
2. تشابه اسم + خصائص (حجم/عدد قطع/تعبئة) مجتمعين، مع سعر كإشارة مساعدة خفيفة جداً

أي مرشّح، من أي طبقة (حتى المتعلّمة - احتياط لو تغيّرت تعبئة المورد)، يمر
بفحص تعارض الخصائص كخطوة أخيرة: لو تعارض حقيقي (حجم/عدد/تعبئة مختلف
فعلياً)، الثقة تُقصّ بشدة بغض النظر عن مصدرها أو تشابه الاسم.
"""

from dataclasses import dataclass

from rapidfuzz import fuzz, process

import config
import learned_matches
import settings as settings_module
from item_attributes import ItemAttributes, check_attribute_conflict, extract_attributes
from items import ReferenceItem, _apply_match
from line_item import ExtractedLine

_CANDIDATE_POOL_SIZE = 30  # مرشّحين بالتشابه الاسمي الخام قبل إعادة الترتيب بالخصائص


@dataclass
class MatchCandidate:
    item: ReferenceItem
    confidence: float
    reason: str


def build_reference_attrs_index(reference: list[ReferenceItem]) -> dict[int, ItemAttributes]:
    """يحسب خصائص كل صنف بالقائمة المرجعية مرة وحدة - يُمرَّر لبقية دوال
    هذا الملف بدل إعادة الحساب لكل سطر فاتورة (القائمة ممكن تكون عشرات
    الآلاف من الأصناف)."""
    return {i: extract_attributes(item.name) for i, item in enumerate(reference)}


def _find_by_code(reference: list[ReferenceItem], code: str) -> ReferenceItem | None:
    for item in reference:
        if item.code == code:
            return item
    return None


def _price_bonus(line_price: float | None, ref_item: ReferenceItem) -> float:
    """إشارة مساعدة خفيفة جداً - السعر أبداً لا يكفي وحده لقبول مطابقة، بس
    ممكن يرجّح مرشّح على ثاني لو قريب من آخر سعر شراء معروف. ReferenceItem
    ما فيه حالياً حقل last_purchase_price (يحتاج توسعة items.py/db_items.py
    مؤجلة لمرحلة لاحقة - غير مطلوبة بهذي الخطة) - getattr الآمن يخلي هذي
    الطبقة معطّلة بهدوء (وزن صفر) بدل ما تكسر شي."""
    ref_price = getattr(ref_item, "last_purchase_price", None)
    if line_price is None or not ref_price:
        return 0.0
    bigger = max(line_price, ref_price)
    smaller = min(line_price, ref_price)
    if bigger <= 0:
        return 0.0
    closeness = smaller / bigger
    if closeness >= 0.9:
        return 5.0
    if closeness >= 0.75:
        return 2.0
    return 0.0


def _score_candidates(
    line: ExtractedLine,
    reference: list[ReferenceItem],
    reference_attrs_index: dict[int, ItemAttributes],
) -> list[MatchCandidate]:
    if not line.description or not reference:
        return []

    line_attrs = extract_attributes(line.description)
    choices = {i: item.name for i, item in enumerate(reference)}
    pool = process.extract(line.description, choices, scorer=fuzz.token_sort_ratio, limit=_CANDIDATE_POOL_SIZE)

    candidates: list[MatchCandidate] = []
    for _, name_score, idx in pool:
        ref_item = reference[idx]
        ref_attrs = reference_attrs_index.get(idx) or extract_attributes(ref_item.name)

        confidence = float(name_score)
        reason_parts = [f"تشابه الاسم {name_score:.0f}%"]

        both_have_size = line_attrs.size_value_base is not None and ref_attrs.size_value_base is not None
        both_have_pack = line_attrs.pack_count is not None and ref_attrs.pack_count is not None
        if both_have_size and both_have_pack:
            confidence = min(100.0, confidence + 8)
            reason_parts.append("الحجم وعدد القطع متطابقين")
        elif both_have_size:
            confidence = min(100.0, confidence + 5)
            reason_parts.append("الحجم متطابق")

        conflict, conflict_reason = check_attribute_conflict(line_attrs, ref_attrs)
        if conflict:
            confidence = min(confidence, config.MATCH_ATTRIBUTE_CONFLICT_CAP)
            reason_parts.append(f"⚠ {conflict_reason}")

        price_bonus = _price_bonus(line.unit_price, ref_item)
        if price_bonus:
            confidence = min(100.0, confidence + price_bonus)
            reason_parts.append("السعر قريب من آخر سعر شراء")

        candidates.append(MatchCandidate(item=ref_item, confidence=confidence, reason="، ".join(reason_parts)))

    candidates.sort(key=lambda c: c.confidence, reverse=True)
    return candidates


def suggest_candidates(
    line: ExtractedLine,
    reference: list[ReferenceItem],
    supplier_name: str | None = None,
    reference_attrs_index: dict[int, ItemAttributes] | None = None,
    top_n: int = 5,
) -> list[MatchCandidate]:
    """يرجّع أفضل top_n مرشّحين لعرضهم بنافذة المراجعة. مرشّح الذاكرة
    المتعلّمة (لو موجود) يحتل المرتبة الأولى **دايماً**، بغض النظر عن رقم
    ثقته مقارنة بالتشابه الاسمي الخام - هذا أولوية بالتصميم (نفس ترتيب
    أولويات المستخدم الأصلي: مطابقة سابقة مؤكدة قبل تشابه الاسم)، مو مجرد
    تنافس أرقام بمجموعة واحدة قد يطيح فيها تشابه اسمي عالي صدفة."""
    if reference_attrs_index is None:
        reference_attrs_index = build_reference_attrs_index(reference)

    learned_candidate: MatchCandidate | None = None
    learned = learned_matches.lookup(supplier_name, line.description)
    if learned is not None:
        entry, confirm_count = learned
        learned_item = _find_by_code(reference, entry["matched_item_code"])
        if learned_item is not None:
            confidence = learned_matches.confidence_for_confirm_count(confirm_count)
            line_attrs = extract_attributes(line.description)
            ref_attrs = extract_attributes(learned_item.name)
            conflict, reason = check_attribute_conflict(line_attrs, ref_attrs)
            note = f"مطابقة سابقة مؤكَّدة ({confirm_count} مرة)"
            if conflict:
                confidence = min(confidence, config.MATCH_ATTRIBUTE_CONFLICT_CAP)
                note += f" - ⚠ {reason}"
            learned_candidate = MatchCandidate(item=learned_item, confidence=confidence, reason=note)

    excluded_code = learned_candidate.item.code if learned_candidate else None
    fuzzy_candidates = _score_candidates(line, reference, reference_attrs_index)
    # نستبعد مرشّح الذاكرة نفسه من هذي القائمة (يظهر مرة وحدة بس بالأول)،
    # ونسمح بمرشّح "معطوب" بتعارض خصائص يمر رغم ثقته المقصوصة - سبب الرفض
    # نفسه معلومة مفيدة للمراجع البشري، ما نبيه يختفي بصمت
    rest = [
        c
        for c in fuzzy_candidates
        if c.item.code != excluded_code and (c.confidence >= config.MIN_SUGGEST_THRESHOLD or "⚠" in c.reason)
    ]
    rest.sort(key=lambda c: c.confidence, reverse=True)

    # الصنف الواحد بالمرجع ممكن يتكرر بعدة صفوف (باركود/وحدة مختلفة لكل
    # صف - راجع items.py::_load_from_amnc_xml) - كلها بنفس الاسم فتاخذ نفس
    # درجة التشابه، فتمتلئ قائمة الاقتراحات بنسخ متطابقة الشكل لنفس الصنف
    # بدل بدائل حقيقية مختلفة. نبقي أفضل صف بس لكل رقم صنف.
    seen_codes = set()
    deduped_rest = []
    for c in rest:
        if c.item.code in seen_codes:
            continue
        seen_codes.add(c.item.code)
        deduped_rest.append(c)

    results = ([learned_candidate] if learned_candidate else []) + deduped_rest
    return results[:top_n]


def enhance_one(
    line: ExtractedLine,
    reference: list[ReferenceItem],
    supplier_name: str | None = None,
    reference_attrs_index: dict[int, ItemAttributes] | None = None,
) -> None:
    """يعيد حساب مطابقة سطر ما تطابق بالباركود من الصفر. يُطبَّق التطابق
    فعلياً (matched_item_code وغيره) بس لو الثقة عدّت عتبة القبول التلقائي؛
    غير كذا يبقى matched_item_code فاضي (نفس مبدأ الأداة: لا تخمّن) بس
    match_score/match_reason يتعبّون لعرض أقرب اقتراح بجدول المراجعة."""
    if reference_attrs_index is None:
        reference_attrs_index = build_reference_attrs_index(reference)

    candidates = suggest_candidates(line, reference, supplier_name, reference_attrs_index, top_n=1)
    if not candidates:
        line.needs_review = True
        return

    best = candidates[0]
    thresholds = settings_module.get_settings()
    line.match_reason = best.reason
    if best.confidence >= thresholds["auto_accept_threshold"]:
        _apply_match(line, best.item, best.confidence)
    else:
        line.match_score = best.confidence
        line.needs_review = True
