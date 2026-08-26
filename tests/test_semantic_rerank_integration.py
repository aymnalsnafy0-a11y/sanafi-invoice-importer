"""
اختبارات طبقة التنسيق (orchestration) لإعادة الترتيب الدلالي بـmatching_engine.py:
سياسة التفعيل، عدم استدعاء AI بدون داعٍ، تنوّع القائمة المختصرة، وأهم قاعدة
أمان بهذا الإصدار: AI وحده (بدون أدلة محلية قوية داعمة) ممنوع يُعبّر مرشّحاً
كان تحت عتبة القبول التلقائي إلى القبول التلقائي. semantic_matcher.rerank
مموّه بالكامل بكل الاختبارات هنا - صفر اتصال إنترنت حقيقي أو تكلفة فعلية.
"""

import io
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, r"D:\scanar\invoice_importer")

results = []


def check(name, condition):
    results.append((name, bool(condition)))
    print(("PASS" if condition else "FAIL"), "-", name)


import learned_matches
import settings as settings_module

tmp_dir = tempfile.mkdtemp()
learned_matches._MATCHES_FILE = Path(tmp_dir) / "learned_matches_test.json"
settings_module._SETTINGS_FILE = Path(tmp_dir) / "matching_settings_test.json"

import config
import matching_engine
import semantic_matcher
from items import ReferenceItem
from line_item import ExtractedLine

_original_rerank = semantic_matcher.rerank


def L(description, **kw):
    return ExtractedLine(
        raw_text="x", description=description, quantity=kw.pop("quantity", 1),
        unit_price=kw.pop("unit_price", None), total=1, ocr_confidence=100, **kw,
    )


def C(code, confidence, reason="", has_structural_conflict=False, from_supplier_history=False):
    item = ReferenceItem(code=code, name=f"صنف {code}", barcode="", default_unit="", internal_id=code, unit_id="1")
    return matching_engine.MatchCandidate(
        item=item, confidence=confidence, reason=reason,
        has_structural_conflict=has_structural_conflict, from_supplier_history=from_supplier_history,
    )


AUTO_ACCEPT = config.MATCH_AUTO_ACCEPT_THRESHOLD  # 95 افتراضياً


print("=== 1) سياسة التفعيل (_should_try_semantic_rerank / needs_semantic_rerank) ===")
check("قائمة فارغة -> لا حاجة لـAI", matching_engine.needs_semantic_rerank([]) is False)

strong_unambiguous = [C("A", 98, reason="الاسم 98%"), C("B", 40, reason="الاسم 40%")]
check("مرشّح أول قوي جداً بلا غموض -> لا حاجة لـAI", matching_engine.needs_semantic_rerank(strong_unambiguous) is False)

below_threshold = [C("A", 80, reason="الاسم 80%")]
check("أفضل مرشّح تحت عتبة القبول -> يحتاج AI", matching_engine.needs_semantic_rerank(below_threshold) is True)

ambiguous_gap = [C("A", 96, reason="الاسم 96%"), C("B", 93, reason="الاسم 93%")]
check("فجوة غموض بين أفضل مرشّحين (أقل من الحد الأدنى) -> يحتاج AI", matching_engine.needs_semantic_rerank(ambiguous_gap) is True)


print("\n=== 2) semantic_enhance_candidates: AI ما يُستدعى إلا لما فعلاً محتاج ===")
call_log = []


def counting_rerank(*args, **kwargs):
    call_log.append(1)
    return None


semantic_matcher.rerank = counting_rerank

reference_empty_line = []
line_no_desc = L("")
call_log.clear()
result_empty, deciding = matching_engine.semantic_enhance_candidates(line_no_desc, reference_empty_line)
check("سطر بدون وصف / مرجع فاضي -> صفر مرشّحين، صفر نداء AI", result_empty == [] and len(call_log) == 0)

reference_strong = [ReferenceItem(code="500", name="حليب نادك كامل الدسم 1 لتر", barcode="", default_unit="كرتون", internal_id="9", unit_id="2")]
idx_strong = matching_engine.build_reference_attrs_index(reference_strong)
line_strong = L("حليب نادك كامل الدسم 1 لتر")
call_log.clear()
result_strong, deciding_strong = matching_engine.semantic_enhance_candidates(line_strong, reference_strong, reference_attrs_index=idx_strong)
check("تطابق محلي قوي بلا غموض -> صفر نداء AI (وفّرنا التكلفة)", len(call_log) == 0)
check("النتيجة المحلية القوية رجعت كما هي", bool(result_strong) and result_strong[0].item.code == "500")

reference_weak = [
    ReferenceItem(code="500", name="حليب نادك كامل الدسم 1 لتر", barcode="", default_unit="كرتون", internal_id="9", unit_id="2"),
    ReferenceItem(code="600", name="عصير لونا برتقال 1 لتر", barcode="", default_unit="كرتون", internal_id="10", unit_id="2"),
]
idx_weak = matching_engine.build_reference_attrs_index(reference_weak)
line_weak = L("حليب سائل كامل الدسم عبوة كبيرة")  # صياغة مختلفة كفاية عشان الثقة المحلية تطلع تحت العتبة
call_log.clear()
result_weak, _ = matching_engine.semantic_enhance_candidates(line_weak, reference_weak, reference_attrs_index=idx_weak)
check("حالة غامضة/ضعيفة محلياً -> AI استُدعي فعلاً", len(call_log) == 1)

semantic_matcher.rerank = _original_rerank


print("\n=== 3) القائمة المختصرة (_build_shortlist_for_semantic) تحافظ على التنوّع مع أكثر من 15 مرشّح ===")
plain = [C(f"PLAIN{i}", 90 - i, reason=f"الاسم {90-i}%") for i in range(20)]
history = [C(f"HIST{i}", 30, reason="الاسم 30%", from_supplier_history=True) for i in range(3)]
conflict = [C(f"CONFLICT{i}", 25, reason="⚠ اختلاف الحجم/الوزن", has_structural_conflict=True) for i in range(3)]
big_pool = plain + history + conflict
big_pool.sort(key=lambda c: c.confidence, reverse=True)
check("تأكيد مسبق: فعلاً أكثر من 15 مرشّح بالمجموع", len(big_pool) > 15)

shortlist = matching_engine._build_shortlist_for_semantic(big_pool, limit=15)
shortlist_codes = {c.item.code for c in shortlist}
check("الحد الأقصى (15) محترم", len(shortlist) <= 15)
check("REAL FEATURE: كل مرشّحي تاريخ المورد الـ3 نجوا رغم ثقتهم المحلية المنخفضة (30%)", all(f"HIST{i}" in shortlist_codes for i in range(3)))
check("REAL FEATURE: كل مرشّحي التعارض البنيوي الـ3 نجوا (يستاهل AI يشوفهم)", all(f"CONFLICT{i}" in shortlist_codes for i in range(3)))
check("باقي المقاعد تعبّت بأعلى المرشّحين العاديين ثقة", "PLAIN0" in shortlist_codes and "PLAIN1" in shortlist_codes)

small_pool = [C(f"P{i}", 50, reason="") for i in range(5)]
shortlist_small = matching_engine._build_shortlist_for_semantic(small_pool, limit=15)
check("قائمة أصغر من الحد -> ترجع كاملة بدون قصّ", len(shortlist_small) == 5)


print("\n=== 4) قاعدة الأمان المحافظة: AI وحده ما يُعبّر مرشّحاً ضعيفاً محلياً لقبول تلقائي بدون أدلة قوية ===")

# 4أ) local=90 (تحت العتبة 95) بلا أي إشارة داعمة قوية بالسبب + AI ثقة عالية (95 -> بوست 8) = 98 (يعبر 95)
weak_support_candidate = C("A", 90, reason="الاسم 90%")  # صفر إشارات "متطابق" - دعم ضعيف
local_list_a = [weak_support_candidate, C("B", 40, reason="الاسم 40%")]
ai_result_a = semantic_matcher.SemanticRerankResult(selected_code="A", confidence=95, reason="نفس المعنى بصياغة مختلفة", ambiguous=False)
merged_a, deciding_a = matching_engine._merge_semantic_result(local_list_a, ai_result_a, AUTO_ACCEPT)
best_a = next(c for c in merged_a if c.item.code == "A")
check("الثقة بعد البوست فعلاً عدّت العتبة (90+8=98>=95)", best_a.confidence >= AUTO_ACCEPT)
check("REAL SAFETY: ai_was_deciding_factor=True لأن الدعم المحلي ضعيف -> يُرفض القبول التلقائي رغم الرقم النهائي", deciding_a is True)

# 4ب) local=92 لكن بإشارتين بنيويتين موافقتين صريحتين (دعم محلي قوي) + AI قوي -> يُسمح
strong_support_candidate = C("A", 92, reason="الاسم 80%، الحجم/الوزن متطابق، عدد القطع متطابق")
local_list_b = [strong_support_candidate, C("B", 40, reason="الاسم 40%")]
ai_result_b = semantic_matcher.SemanticRerankResult(selected_code="A", confidence=95, reason="نفس الصنف بالضبط", ambiguous=False)
merged_b, deciding_b = matching_engine._merge_semantic_result(local_list_b, ai_result_b, AUTO_ACCEPT)
best_b = next(c for c in merged_b if c.item.code == "A")
check("الثقة بعد البوست عدّت العتبة (92+8=100)", best_b.confidence >= AUTO_ACCEPT)
check("REAL POLICY: ai_was_deciding_factor=False لوجود دعم محلي قوي (إشارتين متطابقتين) -> القبول التلقائي مسموح", deciding_b is False)

# 4ج) AI يختار مرشّحاً عنده تعارض بنيوي حقيقي -> السقف الصارم (40) يبقى نافذاً حتى بثقة AI القصوى
conflicted_candidate = C("A", 35, reason="⚠ اختلاف الحجم/الوزن: 400جم مقابل 1800جم", has_structural_conflict=True)
local_list_c = [conflicted_candidate]
ai_result_c = semantic_matcher.SemanticRerankResult(selected_code="A", confidence=100, reason="متأكد جداً", ambiguous=False)
merged_c, deciding_c = matching_engine._merge_semantic_result(local_list_c, ai_result_c, AUTO_ACCEPT)
best_c = next(c for c in merged_c if c.item.code == "A")
check("REAL SAFETY: تعارض بنيوي حقيقي -> السقف (40) يبقى نافذاً حتى مع ثقة AI=100", best_c.confidence <= config.MATCH_ATTRIBUTE_CONFLICT_CAP)
check("النتيجة أبداً ما توصل لعتبة القبول التلقائي رغم اختيار AI الواثق", deciding_c is False and best_c.confidence < AUTO_ACCEPT)

# 4د) AI ambiguous=True -> صفر بوست مهما كانت ثقته الرقمية
ai_result_d = semantic_matcher.SemanticRerankResult(selected_code="A", confidence=99, reason="مو متأكد", ambiguous=True)
merged_d, deciding_d = matching_engine._merge_semantic_result([C("A", 90, reason="الاسم 90%")], ai_result_d, AUTO_ACCEPT)
best_d = next(c for c in merged_d if c.item.code == "A")
check("REAL SAFETY: AI نفسه أعلن ambiguous=True -> صفر بوست بغض النظر عن رقم الثقة", best_d.confidence == 90)

# 4هـ) دفاع مضاعف: AI يختار كوداً غير موجود بالقائمة المحلية أصلاً
ai_result_e = semantic_matcher.SemanticRerankResult(selected_code="NOTLOCAL", confidence=99, reason="", ambiguous=False)
merged_e, deciding_e = matching_engine._merge_semantic_result([C("A", 50, reason="")], ai_result_e, AUTO_ACCEPT)
check("REAL SAFETY (دفاع مضاعف): كود AI غير موجود بالقائمة المحلية -> القائمة ترجع بدون تغيير", [c.item.code for c in merged_e] == ["A"] and deciding_e is False)


print("\n=== 5) سيناريو واقعي: صياغة مختلفة (نفس اللغة) بين مرشّحين متقاربين - AI يرجّح الصحيح ===")
reference5 = [
    ReferenceItem(code="DET1", name="منظف ارضيات ليزول لافندر 3 لتر", barcode="", default_unit="كرتون", internal_id="1", unit_id="1"),
    ReferenceItem(code="DET2", name="معطر جو ليزول لافندر 300 مل", barcode="", default_unit="كرتون", internal_id="2", unit_id="1"),
]
idx5 = matching_engine.build_reference_attrs_index(reference5)
line5 = L("ليزول تنظيف الأرضيات لافندر 3 لتر")

local5_check = matching_engine.suggest_candidates(line5, reference5, reference_attrs_index=idx5, top_n=5)
check("تأكيد مسبق: أفضل مرشّح محلياً تحت عتبة القبول التلقائي (يحتاج مساعدة)", bool(local5_check) and local5_check[0].confidence < AUTO_ACCEPT)


def fake_rerank_correct_choice(*args, **kwargs):
    return semantic_matcher.SemanticRerankResult(
        selected_code="DET1", confidence=92, reason="نفس المنتج (منظف أرضيات)، نفس الحجم 3 لتر - المرشّح الثاني معطر جو بحجم مختلف تماماً", ambiguous=False,
    )


semantic_matcher.rerank = fake_rerank_correct_choice
enhanced5, deciding5 = matching_engine.semantic_enhance_candidates(line5, reference5, reference_attrs_index=idx5, top_n=3)
semantic_matcher.rerank = _original_rerank

check("AI رجّح الصنف الصحيح (منظف الأرضيات) رغم صياغة مختلفة عن الفاتورة", bool(enhanced5) and enhanced5[0].item.code == "DET1")
check("ثقته ارتفعت بعد مساهمة AI مقارنة بالمحلي وحده", enhanced5[0].confidence > local5_check[0].confidence)
check("سبب النتيجة يوثّق مساهمة AI بوضوح للمراجع البشري", "AI semantic rerank" in enhanced5[0].reason)


print("\n=== 6) ذاكرة متعلّمة بثقة عالية بلا غموض -> AI ما يُستدعى (وفّرنا التكلفة) ===")
reference6 = [ReferenceItem(code="700", name="بيبسي 330 مل كرتون 24 حبة", barcode="", default_unit="كرتون", internal_id="11", unit_id="1")]
idx6 = matching_engine.build_reference_attrs_index(reference6)
for _ in range(5):
    learned_matches.record_confirmation("مورد المشروبات الكبير", "بيبسي 330 مل كرتون 24 حبة", reference6[0])
line6 = L("بيبسي 330 مل كرتون 24 حبة")

call_log.clear()
semantic_matcher.rerank = counting_rerank
result6, _ = matching_engine.semantic_enhance_candidates(line6, reference6, supplier_name="مورد المشروبات الكبير", reference_attrs_index=idx6)
semantic_matcher.rerank = _original_rerank
check("ذاكرة متعلّمة قوية بلا تعارض/غموض -> صفر نداء AI", len(call_log) == 0)
check("النتيجة صحيحة (من الذاكرة/الدليل العادي المدموج)", bool(result6) and result6[0].item.code == "700")


print("\n=== 7) اختبار شامل على الـorchestration نفسه (مو helper منفصل): الصنف الصحيح برتبة ضعيفة محلياً -> يوصل AI ويصعد بالنتيجة النهائية ===")
# REAL BUG FIX (سبق دمجه): semantic_enhance_candidates كانت تجلب القائمة
# المحلية بحجم max(top_n, 2) بس - يعني قبل الإصلاح، استدعاء بـtop_n=2 كان
# يجلب مرشّحين اثنين بس محلياً، فأي صنف صحيح لكن بصياغة اسم بعيدة (رتبته
# الثامنة مثلاً) ما كان يوصل حتى لقائمة AI أصلاً - يتفحّص هنا مباشرة عبر
# الدالة الحقيقية end-to-end، مو بفحص القيمة الداخلية لوحدها.
strong_wrong = [C(f"WRONG{i}", 63 + i, reason=f"الاسم {63+i}%") for i in range(7)]  # 7 مرشّحين أقوى بالاسم شوي لكن غلط - يسبقون الصحيح بالترتيب بفارق ضيق (69..63)
correct_candidate7 = C("CORRECT8", 62, reason="الاسم 55%، الحجم/الوزن متطابق")  # الصنف الصحيح - رتبته الثامنة (تشابه اسم أضعف بفارق ضيق بس فعلاً هو الصح، وله دعم بنيوي حقيقي)
weaker_tail = [C(f"WEAK{i}", 30 - i, reason=f"الاسم {30-i}%") for i in range(5)]  # مرشّحين إضافيين أضعف بعده
full_pool7 = strong_wrong + [correct_candidate7] + weaker_tail
full_pool7.sort(key=lambda c: c.confidence, reverse=True)

check("تأكيد مسبق: فعلاً أكثر من 10 مرشّحين بالمجموع", len(full_pool7) > 10)
correct_rank7 = full_pool7.index(correct_candidate7)
check(f"تأكيد مسبق: الصنف الصحيح فعلاً برتبة ضعيفة محلياً (رتبة {correct_rank7}, تحت top_n=2 المطلوب للعرض)", correct_rank7 >= 2)

original_suggest_candidates = matching_engine.suggest_candidates
matching_engine.suggest_candidates = lambda *a, **k: full_pool7[: k.get("top_n", 5)]

captured7 = {}


def recording_rerank_finds_correct(*args, **kwargs):
    captured7["received_codes"] = {c.code for c in kwargs["candidates"]}
    return semantic_matcher.SemanticRerankResult(
        selected_code="CORRECT8", confidence=90, reason="نفس المنتج فعلياً رغم اختلاف الصياغة", ambiguous=False,
    )


semantic_matcher.rerank = recording_rerank_finds_correct
final7, deciding7 = matching_engine.semantic_enhance_candidates(L("وصف بصياغة بعيدة عن اسم القاعدة"), [], top_n=2)
matching_engine.suggest_candidates = original_suggest_candidates
semantic_matcher.rerank = _original_rerank

check(
    "REAL BUG FIX: AI فعلاً استلم الصنف الصحيح (رتبة ضعيفة محلياً) ضمن قائمة مرشّحيه، رغم أن top_n المطلوب للعرض=2 فقط",
    "CORRECT8" in captured7.get("received_codes", set()),
)
check("بعد الترجيح، الصنف الصحيح صعد ضمن أفضل top_n=2 نتيجة نهائية معروضة", any(c.item.code == "CORRECT8" for c in final7))
check("النتيجة الأولى فعلاً هو الصنف الصحيح بعد الترجيح", bool(final7) and final7[0].item.code == "CORRECT8")
check("عدد النتائج المُرجعة للعرض لسا محترم لـtop_n=2 (مو حجم الـpool الداخلي)", len(final7) <= 2)


print("\n=== 8) semantic_enhance_candidates(top_n=3): يرسل حتى 15 مرشّح لـAI عند الحاجة، لكن يرجّع 3 فقط للواجهة ===")
big_pool8 = [C(f"P{i}", 90 - i, reason=f"الاسم {90-i}%") for i in range(20)]  # 20 مرشّح - أكبر من SEMANTIC_RERANK_SHORTLIST_SIZE (15)
check("تأكيد مسبق: فعلاً أكثر من 15 مرشّح بالمجموع", len(big_pool8) > config.SEMANTIC_RERANK_SHORTLIST_SIZE)

matching_engine.suggest_candidates = lambda *a, **k: big_pool8[: k.get("top_n", 5)]

captured8 = {}


def recording_rerank_counts(*args, **kwargs):
    captured8["received_codes"] = {c.code for c in kwargs["candidates"]}
    return semantic_matcher.SemanticRerankResult(selected_code="P0", confidence=80, reason="", ambiguous=False)


semantic_matcher.rerank = recording_rerank_counts
final8, _ = matching_engine.semantic_enhance_candidates(L("وصف عام"), [], top_n=3)
matching_engine.suggest_candidates = original_suggest_candidates
semantic_matcher.rerank = _original_rerank

check(
    f"REAL FEATURE: AI استلم حتى حد السقف (15) رغم أن top_n المطلوب للعرض=3 - العدد الفعلي: {len(captured8.get('received_codes', set()))}",
    len(captured8.get("received_codes", set())) == config.SEMANTIC_RERANK_SHORTLIST_SIZE,
)
check("رغم ذلك، النتيجة النهائية المُرجعة للواجهة فعلاً 3 بس (top_n محترم بالعرض)", len(final8) == 3)


print("\n--- summary ---")
total = len(results)
passed = sum(1 for _, ok in results if ok)
print(f"{passed}/{total} checks passed")
if passed != total:
    sys.exit(1)
