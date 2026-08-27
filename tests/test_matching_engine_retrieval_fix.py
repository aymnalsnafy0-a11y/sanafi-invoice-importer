"""
اختبارات إصلاح خلل Retrieval الحقيقي (2026-08-28، مؤكَّد بـBenchmark حقيقي
مرتين على عائلتين مختلفتين - "الوطنية" و"انتاج"): _rank_and_cap_candidates
كانت ترتّب *داخل* كل رتبة أولوية بعدد المصادر المطابقة فقط (len(hits[idx]))،
رقم يتعادل بسهولة بين عشرات المرشّحين بنفس العائلة، فيصير ترتيب الفهرسة
الخام (مو أي مؤشر جودة) هو الحكم الفعلي عند التعادل - مرشّح بتطابق اسم 100%
حرفي ممكن يُقصّ عشوائياً قبل حتى مرحلة التقييم الكامل.

هذا الملف بيانات مصطنعة بالكامل (بدون حاجة لملف AmnC حقيقي) - يعيد إنتاج
نفس نمط الخلل (عائلة كبيرة، تعادل عدد مصادر) بشكل معزول وقابل للتكرار
بأي بيئة، بالإضافة لاختبارات ثبات الترتيب (Order-Invariance) والتأكد من
عدم كسر أي شبكة أمان قائمة (تعارض بنيوي يبقى يمنع القبول التلقائي حتى لو
المرشّح "مضمون الدخول").
"""

import io
import random
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, r"D:\scanar\invoice_importer")

results = []


def check(name, condition):
    results.append((name, bool(condition)))
    print(("PASS" if condition else "FAIL"), "-", name)


import item_attributes
import matching_engine
from items import ReferenceItem
from line_item import ExtractedLine


def L(description, **kw):
    return ExtractedLine(
        raw_text="x", description=description, quantity=kw.pop("quantity", 1),
        unit_price=kw.pop("unit_price", None), total=1, ocr_confidence=100, **kw,
    )


def make_family(brand_word, flavors, correct_flavor, correct_code="CORRECT", size="350جم"):
    """عائلة كبيرة من الأصناف بنفس الماركة (نفس أول كلمة غير عامة بالاسم)
    وأغلبها بنفس الحجم - نفس نمط "الوطنية"/"انتاج" الحقيقي بالضبط - عشان
    تتزاحم كلها على نفس رتبة الأولوية (brand + structural واحد على الأقل)."""
    items = []
    for i, flavor in enumerate(flavors):
        code = correct_code if flavor == correct_flavor else f"OTHER{i}"
        items.append(ReferenceItem(
            code=code, name=f"{brand_word} {flavor} {size}", barcode="",
            default_unit="حبة", internal_id=str(i), unit_id="1",
        ))
    return items


_FLAVORS_40 = [f"نكهة{i}" for i in range(39)] + ["نكهة الصنف الصحيح"]


print("=== 1) REAL BUG FIX: عائلة كبيرة (40 صنف)، الصنف الصحيح بآخر ترتيب المرجع عمداً - يجب ألا يسقط بسبب الحصة ===")
reference1 = make_family("الوطنية", _FLAVORS_40, "نكهة الصنف الصحيح")
check("تأكيد مسبق: الصنف الصحيح فعلاً آخر عنصر بالمرجع (أسوأ حالة لخلل ترتيب الفهرسة القديم)", reference1[-1].code == "CORRECT")
idx1 = matching_engine.build_reference_attrs_index(reference1)
line1 = L("الوطنية نكهة الصنف الصحيح 350جم")  # يطابق اسم الصنف الصحيح حرفياً بعد التطبيع

cands1 = matching_engine.suggest_candidates(line1, reference1, reference_attrs_index=idx1, top_n=5)
check("REAL BUG FIX: الصنف الصحيح موجود بالنتيجة النهائية", any(c.item.code == "CORRECT" for c in cands1))
check("REAL BUG FIX: الصنف الصحيح فعلاً بالمرتبة الأولى (تطابق اسم 100% حرفي)", bool(cands1) and cands1[0].item.code == "CORRECT" and cands1[0].confidence >= 95)

line_attrs1 = item_attributes.extract_attributes(line1.description)
supplier_codes1 = set()
hits1 = matching_engine._retrieve_candidate_hits(line1, line_attrs1, reference1, idx1, supplier_codes1)
ranked1 = matching_engine._rank_and_cap_candidates(hits1, line1, reference1, line_attrs1, idx1)
correct_ref_idx1 = next(i for i, it in enumerate(reference1) if it.code == "CORRECT")
check("REAL FEATURE: الصنف الصحيح ضمن المرشّحين *بعد القصّ مباشرة* (قبل التقييم الكامل حتى)، رغم ازدحام رتبته", correct_ref_idx1 in ranked1)
check("السقف الأقصى العام لسا محترم (الإصلاح ما ألغى القصّ، بس رتّبه بجودة)", len(ranked1) <= matching_engine._MAX_CANDIDATES_BEFORE_SCORING)


print("\n=== 2) Order-Invariance: نفس العائلة بترتيب مرجعي مختلف (shuffle) - نفس النتيجة الأساسية ===")
random.seed(42)
for trial in range(5):
    reference2 = make_family("الوطنية", _FLAVORS_40, "نكهة الصنف الصحيح")
    random.shuffle(reference2)
    idx2 = matching_engine.build_reference_attrs_index(reference2)
    line2 = L("الوطنية نكهة الصنف الصحيح 350جم")
    cands2 = matching_engine.suggest_candidates(line2, reference2, reference_attrs_index=idx2, top_n=5)
    found = any(c.item.code == "CORRECT" for c in cands2)
    top1_correct = bool(cands2) and cands2[0].item.code == "CORRECT"
    check(f"محاولة خلط {trial + 1}/5: الصنف الصحيح لسا موجود بالنتيجة", found)
    check(f"محاولة خلط {trial + 1}/5: الصنف الصحيح لسا بالمرتبة الأولى (ترتيب المرجع ما أثّر)", top1_correct)


print("\n=== 3) عائلة أكبر بكثير من عائلتي الحالة الحقيقية (32 صنف) - عائلة 60 صنف، حصة الرتبة 10 فقط ===")
_FLAVORS_60 = [f"نكهة{i}" for i in range(59)] + ["نكهة الصنف الصحيح النادرة جداً"]
reference3 = make_family("انتاج", _FLAVORS_60, "نكهة الصنف الصحيح النادرة جداً")
idx3 = matching_engine.build_reference_attrs_index(reference3)
line3 = L("انتاج نكهة الصنف الصحيح النادرة جداً 350جم")
cands3 = matching_engine.suggest_candidates(line3, reference3, reference_attrs_index=idx3, top_n=3)
check("REAL FEATURE: يشتغل حتى مع عائلة أكبر بكثير (60 صنف مقابل حصة 10) من الحالة الحقيقية (32 صنف)", bool(cands3) and cands3[0].item.code == "CORRECT")


print("\n=== 4) منافسة أصناف حقيقية من نفس العائلة - كلاهما يدخل الاسترجاع، الاسم الأدق يفوز بالتقييم النهائي ===")
# محاكاة حالة "العيدان بهارات فلفل احمر" مقابل "العيدان بهارات منوع" الحقيقية -
# نفس الماركة/الحجم، نكهة مختلفة فعلاً - كلاهما يستاهل يظهر بالاقتراحات
reference4 = [
    ReferenceItem(code="RED", name="العيدان بهارات فلفل احمر 200 جم", barcode="", default_unit="حبة", internal_id="1", unit_id="1"),
    ReferenceItem(code="MIXED", name="العيدان بهارات منوع 200 جم", barcode="", default_unit="حبة", internal_id="2", unit_id="1"),
]
idx4 = matching_engine.build_reference_attrs_index(reference4)
line4 = L("العيدان بهارات فلفل احمر 200 جم")
cands4 = matching_engine.suggest_candidates(line4, reference4, reference_attrs_index=idx4, top_n=5)
check("كلا الصنفين المتشابهين يدخلان الاسترجاع (صفر أحدهما يضيع)", {c.item.code for c in cands4} == {"RED", "MIXED"})
check("REAL SAFETY: الصنف الصحيح (تطابق اسم حرفي) يتصدّر فعلياً، مو مجرد يظهر", cands4[0].item.code == "RED" and cands4[0].confidence >= cands4[1].confidence)


print("\n=== 5) REAL SAFETY: 'مضمون الدخول' لا يعني 'مضمون القبول' - تعارض بنيوي حقيقي يبقى يمنع القبول التلقائي ===")
# صنفان بنفس الاسم *حرفياً* بعد التطبيع (يحصل بقواعد حقيقية - نفس المنتج
# مسجّل مرتين بخصائص مختلفة) - المطابقة الحرفية تضمن الدخول للاثنين، لكن
# التعارض البنيوي (حجم مختلف فعلياً) لازم يبقى يمنع القبول التلقائي
reference5 = [
    ReferenceItem(code="SMALL", name="صنف مطابق اسماً 250 مل", barcode="", default_unit="حبة", internal_id="1", unit_id="1"),
]
idx5 = matching_engine.build_reference_attrs_index(reference5)
line5 = L("صنف مطابق اسماً 330 مل")  # نفس النص الأساسي، حجم مختلف فعلياً بالوصف
cands5 = matching_engine.suggest_candidates(line5, reference5, reference_attrs_index=idx5, top_n=3)
check("تأكيد مسبق: فيه مرشّح واحد بس (نفس الاسم تقريباً، حجم مختلف)", len(cands5) == 1)
check("REAL SAFETY: تعارض الحجم الحقيقي يبقى يقصّ الثقة رغم قوة تشابه الاسم النصي", cands5[0].confidence <= matching_engine.config.MATCH_ATTRIBUTE_CONFLICT_CAP)

line5b = L("صنف مطابق اسماً 250 مل")  # تطابق حرفي فعلي هذي المرة
cands5b = matching_engine.suggest_candidates(line5b, reference5, reference_attrs_index=idx5, top_n=3)
matching_engine.enhance_one(line5b, reference5, reference_attrs_index=idx5, allow_semantic=False)
check("لما التطابق حرفي فعلاً بلا تعارض، القبول التلقائي يشتغل طبيعياً (الإصلاح ما عطّل شي)", line5b.matched_item_code == "SMALL")


print("\n=== 6) صفر false auto-accept جديد: عائلة كبيرة فيها منافس بتشابه اسم قريب لكن مو مطابقة حرفية ===")
# الصنف الصحيح تطابق حرفي، لكن فيه منافس بنفس الرتبة بتشابه عالٍ (مو حرفي) -
# لازم القبول التلقائي يذهب للصنف الحرفي الصحيح، مو المنافس القريب
flavors6 = [f"نكهة{i}" for i in range(38)] + ["نكهة قريبة جداً من الصحيحة", "الصنف الصحيح تماماً"]
reference6 = make_family("سنافي", flavors6, "الصنف الصحيح تماماً")
idx6 = matching_engine.build_reference_attrs_index(reference6)
line6 = L("سنافي الصنف الصحيح تماماً 350جم")
matching_engine.enhance_one(line6, reference6, reference_attrs_index=idx6, allow_semantic=False)
check("REAL SAFETY: القبول التلقائي (لو صار) يذهب للصنف الصحيح فعلاً، مو أي منافس بنفس العائلة", line6.matched_item_code in ("CORRECT", ""))
if line6.matched_item_code:
    check("تأكيد: الكود المقبول تلقائياً هو فعلاً الصحيح", line6.matched_item_code == "CORRECT")


print("\n--- summary ---")
total = len(results)
passed = sum(1 for _, ok in results if ok)
print(f"{passed}/{total} checks passed")
if passed != total:
    sys.exit(1)
