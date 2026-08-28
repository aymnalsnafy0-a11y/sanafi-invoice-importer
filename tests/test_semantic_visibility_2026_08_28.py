"""
اختبارات Safety Patch #4 (2026-08-28): فصل قائمة UI عن shortlist Semantic AI
+ حل تشبّع Top15 بالتنويع - بدون تغيير عتبة عرض UI (MIN_SUGGEST_THRESHOLD)
ولا قواعد القبول التلقائي إطلاقاً.

بند 1 (Semantic Visibility): AI يبني shortlist من wide_pool
(_build_wide_pool_for_semantic - مرشّحون حقيقيون من _score_all_candidates،
حتى لو تحت عتبة عرض UI)، بدل local_pool (suggest_candidates، محكوم بالعتبة)
وحدها. لكن القائمة *المُرجَعة/المعروضة فعلياً* تبقى محكومة بنفس عتبة UI
بالضبط (_merge_semantic_result) - مرشّح من wide_pool فقط يظهر بالنتيجة
النهائية فقط لو ثقته بعد بوست AI تعبر نفس العتبة (أو تعارض بنيوي/تاريخ
مورد، نفس استثناءات suggest_candidates الحالية).

بند 2 (تشبّع Top15): _dedupe_and_diversify - dedup بالكود (نفس الصنف بعدة
صفوف وحدة/باركود) + تنويع بالعائلة/الماركة (حد أقصى لكل مفتاح) قبل قصّ
القائمة الموسّعة - يمنع عائلة كبيرة واحدة من ابتلاع القائمة قبل دخول مرشّح
صحيح من عائلة أقل تكراراً.
"""

import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

results = []


def check(name, condition):
    results.append((name, bool(condition)))
    print(("PASS" if condition else "FAIL"), "-", name)


import config
import item_attributes
import matching_engine
import semantic_matcher
from items import ReferenceItem
from line_item import ExtractedLine

AUTO_ACCEPT = config.MATCH_AUTO_ACCEPT_THRESHOLD
UI_THRESHOLD = config.MIN_SUGGEST_THRESHOLD


def L(description, **kw):
    return ExtractedLine(
        raw_text="x", description=description, quantity=kw.pop("quantity", 1),
        unit_price=kw.pop("unit_price", None), total=1, ocr_confidence=100, **kw,
    )


def C(code, confidence, reason="", has_structural_conflict=False, from_supplier_history=False, name=None):
    item = ReferenceItem(code=code, name=name or f"صنف {code}", barcode="", default_unit="", internal_id=code, unit_id="1")
    return matching_engine.MatchCandidate(
        item=item, confidence=confidence, reason=reason,
        has_structural_conflict=has_structural_conflict, from_supplier_history=from_supplier_history,
    )


print("=== 1) بند 2: dedup بالكود - نفس الصنف بـ5 صفوف وحدة/باركود ما يستهلك 5 مقاعد ===")
same_code_rows = [C("SAME1", 70 - i, reason="", name="صنف مكرر A") for i in range(5)]
distinct = [C(f"OTHER{i}", 40, reason="", name=f"صنف مختلف {i}") for i in range(3)]
deduped = matching_engine._dedupe_and_diversify(same_code_rows + distinct, limit=15, max_per_family=3)
check("REAL BUG FIX: 5 صفوف لنفس الكود -> مقعد واحد بس بعد dedup", sum(1 for c in deduped if c.item.code == "SAME1") == 1)
check("أفضل ثقة بين الصفوف الخمسة هي اللي احتُفظ بها", next(c for c in deduped if c.item.code == "SAME1").confidence == 70)


print("\n=== 2) بند 2: تنويع بالعائلة - عائلة واحدة كبيرة ما تبتلع القائمة قبل عائلة نادرة ===")
family_a = [C(f"FAM_A_{i}", 90 - i, reason="", name=f"لوزين صنف {i}") for i in range(10)]  # نفس الماركة "لوزين"، 10 أصناف
family_b = [C("RARE", 55, reason="", name="كاكو صنف نادر")]  # ماركة مختلفة تماماً، ثقة أقل من أغلب family_a
# limit=4 بالضبط (حصة لوزين 3 + مقعد واحد للنادر) - يفحص الحد الأقصى الصارم
# بلا لمس سلوك "تعبئة الفائض" المقصود (بند 2ج تحت) عمداً
diversified = matching_engine._dedupe_and_diversify(family_a + family_b, limit=4, max_per_family=3)
check("تأكيد مسبق: family_a فعلاً كلها نفس الماركة المخمَّنة (لوزين)", len({item_attributes.extract_attributes(c.item.name).brand for c in family_a}) == 1)
check("REAL BUG FIX: عائلة 'لوزين' الكبيرة محدودة بـ3 مقاعد بس رغم ثقتها الأعلى عموماً", sum(1 for c in diversified if item_attributes.extract_attributes(c.item.name).brand == "لوزين") <= 3)
check("REAL BUG FIX: الصنف النادر (عائلة مختلفة) دخل القائمة الموسّعة رغم ثقته الأقل من أغلب المنافسين", any(c.item.code == "RARE" for c in diversified))

print("\n--- 2ج) سلوك مقصود: لو التنوّع الحقيقي أقل من limit، الفائض يُعبَّى من نفس العائلة (ما نرجّع قائمة أصغر تعسفاً) ---")
diversified_overflow = matching_engine._dedupe_and_diversify(family_a + family_b, limit=6, max_per_family=3)
check("سلوك مقصود: مع limit=6 وعائلتين بس، القائمة تمتلئ كاملة (تعبئة فائض من نفس العائلة بدل قائمة ناقصة)", len(diversified_overflow) == 6)
check("سلوك مقصود: الصنف النادر لسا موجود حتى مع تعبئة الفائض", any(c.item.code == "RARE" for c in diversified_overflow))

print("\n--- 2ب) سلامة: لو ما فيه ازدحام حقيقي (تنوّع كافٍ أصلاً)، صفر مقاعد تُهدَر تعسفاً ---")
diverse_pool = [C(f"D{i}", 90 - i, reason="", name=f"ماركة{i} صنف مختلف تماماً") for i in range(10)]
check("تأكيد مسبق: كل صنف بعائلة مختلفة فعلاً", len({item_attributes.extract_attributes(c.item.name).brand for c in diverse_pool}) == 10)
result_diverse = matching_engine._dedupe_and_diversify(diverse_pool, limit=8, max_per_family=3)
check("سلامة: تنوّع طبيعي كافٍ -> القائمة تمتلئ كاملة للحد الأقصى بلا نقص تعسفي", len(result_diverse) == 8)


print("\n=== 3) بند 1: مرشّح موجود بـwide_pool فقط (تحت عتبة UI محلياً) - AI يختاره وثقته بعد البوست تعبر العتبة -> يظهر بالنتيجة ===")
local_pool_3 = [C("WRONG", 50, reason="الاسم 50%")]  # تحت عتبة UI (55) - ما دخل local_pool أصلاً بالواقع، لكن هنا لتمثيل قائمة UI الأساسية فقط
wide_only_candidate = C("HIDDEN_BUT_RIGHT", 48, reason="الاسم 48%، الحجم/الوزن متطابق")  # 48+8(بوست أقصى)=56 >= 55
wide_pool_3 = local_pool_3 + [wide_only_candidate]
ai_result_3 = semantic_matcher.SemanticRerankResult(selected_code="HIDDEN_BUT_RIGHT", confidence=90, reason="نفس الصنف فعلياً", ambiguous=False)
merged_3, deciding_3 = matching_engine._merge_semantic_result(local_pool_3, wide_pool_3, ai_result_3, AUTO_ACCEPT)
check("تأكيد مسبق: المرشّح الصحيح فعلاً غير موجود بـlocal_pool (كان سيُحجَب قبل هذا الـpatch)", not any(c.item.code == "HIDDEN_BUT_RIGHT" for c in local_pool_3))
check("REAL BUG FIX: بعد بوست AI، الثقة تعبر عتبة UI (48+8=56>=55)", next(c for c in merged_3 if c.item.code == "HIDDEN_BUT_RIGHT").confidence >= UI_THRESHOLD)
check("REAL BUG FIX: المرشّح (من wide_pool فقط) ظهر فعلاً بالنتيجة النهائية المُرجَعة", any(c.item.code == "HIDDEN_BUT_RIGHT" for c in merged_3))


print("\n=== 4) REAL SAFETY: مرشّح من wide_pool فقط، ثقته بعد البوست ما تعبر عتبة UI -> يبقى مخفياً (UI محافظ كما هو) ===")
local_pool_4 = [C("WRONG", 50, reason="الاسم 50%")]
wide_only_weak = C("WEAK_MAYBE", 30, reason="الاسم 30%")  # 30+8=38 < 55 - أضعف من إنه يستاهل عرض حتى بعد بوست AI الأقصى
wide_pool_4 = local_pool_4 + [wide_only_weak]
ai_result_4 = semantic_matcher.SemanticRerankResult(selected_code="WEAK_MAYBE", confidence=95, reason="", ambiguous=False)
merged_4, deciding_4 = matching_engine._merge_semantic_result(local_pool_4, wide_pool_4, ai_result_4, AUTO_ACCEPT)
check("REAL SAFETY: مرشّح ضعيف من wide_pool فقط، حتى بثقة AI القصوى، لا يعبر عتبة UI -> يبقى مخفياً", not any(c.item.code == "WEAK_MAYBE" for c in merged_4))
check("REAL SAFETY: القائمة المُرجَعة تبقى local_pool الأصلية بدون تغيير", [c.item.code for c in merged_4] == [c.item.code for c in local_pool_4])
check("REAL SAFETY: ai_was_deciding_factor=False (لم يُطبَّق شي)", deciding_4 is False)


print("\n=== 5) REAL SAFETY: مرشّح من wide_pool فقط بتعارض بنيوي حقيقي - يظهر (نفس استثناء suggest_candidates الحالي) لكن السقف الصارم يبقى نافذاً ===")
local_pool_5 = [C("WRONG", 50, reason="الاسم 50%")]
wide_conflicted = C("CONFLICTED", 30, reason="⚠ اختلاف الحجم/الوزن: 250مل مقابل 500مل", has_structural_conflict=True)
wide_pool_5 = local_pool_5 + [wide_conflicted]
ai_result_5 = semantic_matcher.SemanticRerankResult(selected_code="CONFLICTED", confidence=100, reason="متأكد جداً", ambiguous=False)
merged_5, deciding_5 = matching_engine._merge_semantic_result(local_pool_5, wide_pool_5, ai_result_5, AUTO_ACCEPT)
check("REAL SAFETY: مرشّح تعارض بنيوي من wide_pool فقط يظهر (نفس منطق suggest_candidates - 'قريب لكن فيه مشكلة')", any(c.item.code == "CONFLICTED" for c in merged_5))
check("REAL SAFETY: السقف الصارم (MATCH_ATTRIBUTE_CONFLICT_CAP) يبقى نافذاً حتى بثقة AI=100 من wide_pool", next(c for c in merged_5 if c.item.code == "CONFLICTED").confidence <= config.MATCH_ATTRIBUTE_CONFLICT_CAP)
check("REAL SAFETY: أبداً ما يوصل لعتبة القبول التلقائي", deciding_5 is False)


print("\n=== 6) دفاع مضاعف: AI يختار كوداً غير موجود حتى بـwide_pool -> القائمة ترجع بدون تغيير ===")
local_pool_6 = [C("A", 50, reason="")]
wide_pool_6 = local_pool_6 + [C("B", 45, reason="")]
ai_result_6 = semantic_matcher.SemanticRerankResult(selected_code="INVENTED", confidence=99, reason="", ambiguous=False)
merged_6, deciding_6 = matching_engine._merge_semantic_result(local_pool_6, wide_pool_6, ai_result_6, AUTO_ACCEPT)
check("REAL SAFETY: كود مُخترَع (غير موجود حتى بالقائمة الموسّعة) -> يُرفض، القائمة الأصلية بدون تغيير", [c.item.code for c in merged_6] == ["A"] and deciding_6 is False)


print("\n=== 7) تكامل حقيقي: _build_wide_pool_for_semantic على كتالوج حقيقي - يلقط مرشّحاً صحيحاً تحت عتبة UI رغم عائلة كبيرة منافسة ===")
# محاكاة حالة 'فطيرة الشوكولاتة' الحقيقية: عائلة كبيرة (نفس الماركة) بثقة
# أعلى نسبياً بس غلط، ومرشّح صحيح (ماركة مختلفة تماماً) بثقة أضعف من عتبة UI
crowd_family = [
    ReferenceItem(code=f"CROWD{i}", name=f"يومي فطيره نكهة{i} 70جم", barcode="", default_unit="حبة", internal_id=str(i), unit_id="1")
    for i in range(20)
]
correct_item = ReferenceItem(code="CORRECT_RARE", name="كاكو فطيره خاصة جداً 70جم", barcode="", default_unit="حبة", internal_id="99", unit_id="1")
reference7 = crowd_family + [correct_item]
idx7 = matching_engine.build_reference_attrs_index(reference7)
line7 = L("فطيره غريبة تماما عن الكل 70جم")

wide7 = matching_engine._build_wide_pool_for_semantic(line7, reference7, idx7, None)
check("تأكيد مسبق: القائمة الموسّعة محدودة (مو كل الكتالوج الـ21 صنف)", 0 < len(wide7) <= matching_engine._SEMANTIC_WIDE_POOL_SIZE)
check("REAL BUG FIX: الصنف النادر (عائلة مختلفة تماماً عن الزحمة) دخل القائمة الموسّعة الحقيقية فعلاً", any(c.item.code == "CORRECT_RARE" for c in wide7))
# ملاحظة: ضمان "لا تستهلك عائلة واحدة أكثر من max_per_family" مُختبَر بدقة
# على مستوى الوحدة ببند 2 أعلاه (بمعزل عن سلوك "تعبئة الفائض" المقصود لو
# التنوّع الحقيقي أقل من limit=30 - هذا السيناريو الاصطناعي هنا (21 صنف
# بمجموعه) أصغر من الـlimit فيُفعِّل تعبئة الفائض عمداً، وهذا سلوك صحيح
# موثَّق ببند 2ج، مو خللاً).


print("\n=== 8) REGRESSION: الوطنية/انتاج لسا rank 1 (بلا تأثير من كل تعديلات هذا الـpatch) ===")
_FLAVORS_40 = [f"نكهة{i}" for i in range(39)] + ["نكهة الصنف الصحيح"]
reference8 = [
    ReferenceItem(code=("CORRECT" if f == "نكهة الصنف الصحيح" else f"OTHER{i}"), name=f"الوطنية {f} 350جم", barcode="", default_unit="حبة", internal_id=str(i), unit_id="1")
    for i, f in enumerate(_FLAVORS_40)
]
idx8 = matching_engine.build_reference_attrs_index(reference8)
line8 = L("الوطنية نكهة الصنف الصحيح 350جم")
cands8 = matching_engine.suggest_candidates(line8, reference8, reference_attrs_index=idx8, top_n=5)
check("REGRESSION: عائلة 'الوطنية' لسا rank 1 بعد Safety Patch #4", bool(cands8) and cands8[0].item.code == "CORRECT" and cands8[0].confidence >= 95)


print("\n--- summary ---")
total = len(results)
passed = sum(1 for _, ok in results if ok)
print(f"{passed}/{total} checks passed")
if passed != total:
    sys.exit(1)
