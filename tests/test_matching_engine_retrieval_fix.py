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
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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


print("\n=== 7) SAFETY PATCH (2026-08-28) A: صنف واحد بنفس code، 20 صف (وحدة/باركود مختلف لكل صف)، "
      "كلها اسم مطابق حرفياً - ما يستهلك 20 مكان ضامن ===")
reference7 = [
    ReferenceItem(code="MULTI", name="صنف متعدد الوحدات 500 جم", barcode=f"BC{i}",
                  default_unit="حبة", internal_id=str(i), unit_id=str(i))
    for i in range(20)
]
idx7 = matching_engine.build_reference_attrs_index(reference7)
line7 = L("صنف متعدد الوحدات 500 جم")
line7_attrs = item_attributes.extract_attributes(line7.description)
hits7 = matching_engine._retrieve_candidate_hits(line7, line7_attrs, reference7, idx7, set())
guaranteed_raw7 = [i for i in hits7 if matching_engine._has_exact_normalized_name_match(line7_attrs, i, idx7)]
check("تأكيد مسبق: فعلاً كل الـ20 صف تطابق اسماً حرفياً (سيناريو الانفجار قبل الإصلاح)", len(guaranteed_raw7) == 20)
guaranteed_dedup7 = matching_engine._dedupe_guaranteed_by_code(guaranteed_raw7, reference7, hits7)
check("SAFETY PATCH A: بعد الـdedupe، صف واحد بس مضمون لنفس الكود (مو 20)", len(guaranteed_dedup7) == 1)
check("SAFETY PATCH A: الصف المختار فعلاً من نفس الكود MULTI", reference7[guaranteed_dedup7[0]].code == "MULTI")
# الصفوف الأخرى لسا موجودة كاملة بـhits (لم تُحذف) - معلومات بدائل الوحدة محفوظة
check("SAFETY PATCH A: باقي الصفوف (بدائل الوحدة) لسا موجودة كاملة بـhits - ما انحذفت من البنية", len(hits7) == 20)
# ومنطقياً يبقى قادر يوصل بالنتيجة النهائية عبر suggest_candidates
cands7 = matching_engine.suggest_candidates(line7, reference7, reference_attrs_index=idx7, top_n=3)
check("SAFETY PATCH A: الصنف يوصل بالنتيجة النهائية وبثقة عالية رغم الـdedupe", bool(cands7) and cands7[0].item.code == "MULTI" and cands7[0].confidence >= 95)


print("\n=== 8) SAFETY PATCH B: عدة أكواد مختلفة بنفس الاسم المطابَق حرفياً - كل كود يحصل على مكانه الضامن ===")
reference8 = [
    ReferenceItem(code=f"CODE{i}", name="صنف مكرر بأكواد مختلفة 250 مل", barcode=f"BC{i}",
                  default_unit="حبة", internal_id=str(i), unit_id="1")
    for i in range(5)
]
idx8 = matching_engine.build_reference_attrs_index(reference8)
line8 = L("صنف مكرر بأكواد مختلفة 250 مل")
line8_attrs = item_attributes.extract_attributes(line8.description)
hits8 = matching_engine._retrieve_candidate_hits(line8, line8_attrs, reference8, idx8, set())
guaranteed_raw8 = [i for i in hits8 if matching_engine._has_exact_normalized_name_match(line8_attrs, i, idx8)]
check("تأكيد مسبق: فعلاً 5 أكواد مختلفة كلها تطابق اسماً حرفياً", len(guaranteed_raw8) == 5)
guaranteed_dedup8 = matching_engine._dedupe_guaranteed_by_code(guaranteed_raw8, reference8, hits8)
check("SAFETY PATCH B: كل الأكواد الـ5 المختلفة تحتفظ بمكانها الضامن - لا حذف بمجرد تطابق الاسم", len(guaranteed_dedup8) == 5)
check("SAFETY PATCH B: الأكواد المضمونة هي فعلاً الـ5 الأصلية بالضبط", {reference8[i].code for i in guaranteed_dedup8} == {f"CODE{i}" for i in range(5)})


print("\n=== 9) SAFETY PATCH C: عدد كبير جداً من الصفوف المطابقة اسماً (150 صف، أكواد مختلطة) - السقف العام يبقى محترماً ===")
reference9 = (
    [ReferenceItem(code="BIGCODE1", name="صنف ضخم التكرار 1 كغم", barcode=f"A{i}", default_unit="حبة", internal_id=str(i), unit_id=str(i)) for i in range(80)]
    + [ReferenceItem(code="BIGCODE2", name="صنف ضخم التكرار 1 كغم", barcode=f"B{i}", default_unit="حبة", internal_id=str(i), unit_id=str(i)) for i in range(70)]
)
idx9 = matching_engine.build_reference_attrs_index(reference9)
line9 = L("صنف ضخم التكرار 1 كغم")
line9_attrs = item_attributes.extract_attributes(line9.description)
hits9 = matching_engine._retrieve_candidate_hits(line9, line9_attrs, reference9, idx9, set())
guaranteed_raw9 = [i for i in hits9 if matching_engine._has_exact_normalized_name_match(line9_attrs, i, idx9)]
check("تأكيد مسبق: فعلاً 150 صف تطابق اسماً حرفياً (أكبر بكثير من السقف العام 80)", len(guaranteed_raw9) == 150)
ranked9 = matching_engine._rank_and_cap_candidates(hits9, line9, reference9, line9_attrs, idx9)
check("SAFETY PATCH C: السقف الأقصى العام يبقى محترماً حتى مع 150 صف مطابق اسماً حرفياً", len(ranked9) <= matching_engine._MAX_CANDIDATES_BEFORE_SCORING)
check("SAFETY PATCH C: كلا الكودين (BIGCODE1 وBIGCODE2) لسا يقدران يدخلان (dedupe بالكود، مو حذف كود كامل)", {reference9[i].code for i in ranked9} == {"BIGCODE1", "BIGCODE2"})


print("\n=== 10) SAFETY PATCH #2 A: code فارغ لا يُستخدم كهوية مشتركة - أصناف مختلفة بلا كود لا تنهار لمرشح واحد ===")
reference10 = [
    ReferenceItem(code="", name="صنف بلا كود مسجل 100 جم", barcode=f"NC{i}", default_unit="حبة", internal_id=f"INT{i}", unit_id="1")
    for i in range(4)
]
idx10 = matching_engine.build_reference_attrs_index(reference10)
line10 = L("صنف بلا كود مسجل 100 جم")
line10_attrs = item_attributes.extract_attributes(line10.description)
hits10 = matching_engine._retrieve_candidate_hits(line10, line10_attrs, reference10, idx10, set())
guaranteed_raw10 = [i for i in hits10 if matching_engine._has_exact_normalized_name_match(line10_attrs, i, idx10)]
check("تأكيد مسبق: فعلاً 4 صفوف بكود فارغ كلها تطابق اسماً حرفياً", len(guaranteed_raw10) == 4)
guaranteed_dedup10 = matching_engine._dedupe_guaranteed_by_code(guaranteed_raw10, reference10, hits10)
check("SAFETY PATCH #2 A: أصناف مختلفة (internal_id مختلف) بكود فارغ لا تنهار لمرشح واحد خطأً", len(guaranteed_dedup10) == 4)
check("SAFETY PATCH #2 A: كل الـinternal_id الأربعة محفوظة فعلاً بالنتيجة (fallback بالـinternal_id يشتغل)", {reference10[i].internal_id for i in guaranteed_dedup10} == {f"INT{i}" for i in range(4)})

print("\n--- 10ب) نفس الحالة بلا أي هوية إطلاقاً (code وinternal_id فارغين) - كل صف يُعامَل كهوية مستقلة ---")
reference10b = [
    ReferenceItem(code="", name="صنف بلا أي هوية 200 جم", barcode="", default_unit="حبة", internal_id="", unit_id="")
    for _ in range(3)
]
idx10b = matching_engine.build_reference_attrs_index(reference10b)
line10b = L("صنف بلا أي هوية 200 جم")
line10b_attrs = item_attributes.extract_attributes(line10b.description)
hits10b = matching_engine._retrieve_candidate_hits(line10b, line10b_attrs, reference10b, idx10b, set())
guaranteed_raw10b = [i for i in hits10b if matching_engine._has_exact_normalized_name_match(line10b_attrs, i, idx10b)]
check("تأكيد مسبق: فعلاً 3 صفوف بلا أي هوية كلها تطابق اسماً حرفياً", len(guaranteed_raw10b) == 3)
guaranteed_dedup10b = matching_engine._dedupe_guaranteed_by_code(guaranteed_raw10b, reference10b, hits10b)
check("SAFETY PATCH #2 A-fallback: بلا أي هوية موثوقة، كل صف يُعامَل كهوية مستقلة (صفر انهيار كاذب)", len(guaranteed_dedup10b) == 3)


print("\n=== 11) SAFETY PATCH #2 B: 150 صنف منطقي مختلف فعلاً (كل واحد code مختلف) - guaranteed لا يتجاوز السقف العام (Hard Cap) أبداً ===")
reference11 = [
    ReferenceItem(code=f"UNIQUE{i}", name="صنف فريد التكرار 750 مل", barcode=f"U{i}", default_unit="حبة", internal_id=str(i), unit_id="1")
    for i in range(150)
]
idx11 = matching_engine.build_reference_attrs_index(reference11)
line11 = L("صنف فريد التكرار 750 مل")
line11_attrs = item_attributes.extract_attributes(line11.description)
hits11 = matching_engine._retrieve_candidate_hits(line11, line11_attrs, reference11, idx11, set())
guaranteed_raw11 = [i for i in hits11 if matching_engine._has_exact_normalized_name_match(line11_attrs, i, idx11)]
check("تأكيد مسبق: فعلاً 150 كود مختلف كلها تطابق اسماً حرفياً (سيناريو الفيضان الحقيقي)", len(guaranteed_raw11) == 150)
guaranteed_dedup11 = matching_engine._dedupe_guaranteed_by_code(guaranteed_raw11, reference11, hits11)
check("تأكيد مسبق: dedup الهوية ما قلّص شي (150 كود مختلف فعلاً، صفر تكرار حقيقي)", len(guaranteed_dedup11) == 150)
ranked11 = matching_engine._rank_and_cap_candidates(hits11, line11, reference11, line11_attrs, idx11)
check("SAFETY PATCH #2 B: Hard Cap محترم حتى مع 150 هوية منطقية مختلفة فعلاً (لا استثناء)", len(ranked11) <= matching_engine._MAX_CANDIDATES_BEFORE_SCORING)
check("SAFETY PATCH #2 B: القصّ فعلاً وصل السقف الأقصى (80) - مو أقل بشكل عشوائي", len(ranked11) == matching_engine._MAX_CANDIDATES_BEFORE_SCORING)


print("\n=== 12) SAFETY PATCH #2 C: نفس سيناريو الفيضان (150 كود مختلف) بعد خلط ترتيب المرجع عدة مرات - Hard Cap محترم + نفس الناجين تماماً بمصادر متساوية ===")
# ملاحظة مهمة (اكتُشفت أثناء بناء هذا الاختبار): بالمسار الكامل الحقيقي
# (_retrieve_candidate_hits الفعلية)، مصدر "name" نفسه له سقف داخلي على
# عدد النتائج المرتجعة (rapidfuzz process.extract بحد أقصى) - سلوك تقييد
# *موجود مسبقاً* بمرحلة الاسترجاع نفسها (مو شيء أضافه Safety Patch)، وهو
# "scoring/retrieval" محمي صراحة من التعديل بهذا الـpatch. نتيجته: مع 150
# صف بنفس الاسم *حرفياً*، فقط جزء منها يحصل فعلياً على مصدر "name" ضمن
# hits - وأيها بالضبط يعتمد على ترتيب المعالجة الداخلي، فيصير عدد المصادر
# (len(hits[idx])) نفسه غير متساوٍ بين الصفوف حتى لو تطابقت أسماؤها 100%.
# هذا التفاوت مصدره مرحلة *الاسترجاع*، خارج نطاق _guaranteed_overflow_key.
# عشان نختبر Order-Invariance لمنطق *هذا الـpatch تحديداً* (القصّ والفرز
# داخل guaranteed) بمعزل عن هذا التفاوت الموجود مسبقاً بالاسترجاع، نبني
# hits بأنفسنا هنا بمصادر متساوية تماماً لكل الـ150 صف (محاكاة الحالة
# الأسوأ الحقيقية: صفوف متطابقة تماماً بكل شيء إلا الكود) - عندها الفاصل
# الوحيد المتاح لمنطقنا هو الحقول المحتوى-محورة (code...) فقط، وهذا فعلاً
# ما نتحقق من ثباته عبر الخلط.
random.seed(7)
survivor_sets = []
for trial in range(4):
    ref_shuffled = list(reference11)
    random.shuffle(ref_shuffled)
    idx_s = matching_engine.build_reference_attrs_index(ref_shuffled)
    line_s = L("صنف فريد التكرار 750 مل")
    attrs_s = item_attributes.extract_attributes(line_s.description)
    hits_uniform = {i: {"name", "size", "brand"} for i in range(len(ref_shuffled))}
    ranked_s = matching_engine._rank_and_cap_candidates(hits_uniform, line_s, ref_shuffled, attrs_s, idx_s)
    survivor_sets.append(frozenset(ref_shuffled[i].code for i in ranked_s))
    check(f"محاولة خلط {trial + 1}/4: Hard Cap محترم حتى بعد خلط ترتيب المرجع", len(ranked_s) <= matching_engine._MAX_CANDIDATES_BEFORE_SCORING)
check("SAFETY PATCH #2 C: بمصادر متساوية (الفاصل الوحيد: محتوى الصف)، مجموعة الأكواد الناجية مطابقة تماماً عبر كل محاولات الخلط - منطق القصّ بهذا الـpatch Order-Invariant فعلياً", len(set(survivor_sets)) == 1)

# وبالمسار الكامل الحقيقي (بتفاوت مصادر الاسترجاع الموجود مسبقاً): نتأكد
# على الأقل إن Hard Cap يبقى محترماً دائماً (الالتزام الفعلي المطلوب هنا)
ranked_real_trials = []
for trial in range(3):
    ref_shuffled = list(reference11)
    random.shuffle(ref_shuffled)
    idx_r = matching_engine.build_reference_attrs_index(ref_shuffled)
    line_r = L("صنف فريد التكرار 750 مل")
    attrs_r = item_attributes.extract_attributes(line_r.description)
    hits_r = matching_engine._retrieve_candidate_hits(line_r, attrs_r, ref_shuffled, idx_r, set())
    ranked_r = matching_engine._rank_and_cap_candidates(hits_r, line_r, ref_shuffled, attrs_r, idx_r)
    ranked_real_trials.append(len(ranked_r))
check("SAFETY PATCH #2 C (مسار حقيقي كامل): Hard Cap محترم بكل محاولات الخلط الثلاث رغم تفاوت مصادر الاسترجاع المسبق", all(n <= matching_engine._MAX_CANDIDATES_BEFORE_SCORING for n in ranked_real_trials))


print("\n=== 13) SAFETY PATCH #2 D: حالة multi-unit الحالية (صنف 20 صف نفس code) تبقى سليمة بعد إضافة Hard Cap ===")
cands13 = matching_engine.suggest_candidates(line7, reference7, reference_attrs_index=idx7, top_n=3)
check("SAFETY PATCH #2 D: صنف multi-unit (20 صف نفس code) لسا يوصل بثقة عالية بعد إضافة Hard Cap", bool(cands13) and cands13[0].item.code == "MULTI" and cands13[0].confidence >= 95)


print("\n=== 14) SAFETY PATCH #2 E: الوطنية/انتاج (الحالتان الحقيقيتان الأصليتان) تبقيان rank 1 بعد كل تعديلات هذا الـpatch ===")
cands14a = matching_engine.suggest_candidates(line1, reference1, reference_attrs_index=idx1, top_n=3)
check("SAFETY PATCH #2 E: عائلة 'الوطنية' لسا rank 1 بعد Safety Patch #2", bool(cands14a) and cands14a[0].item.code == "CORRECT" and cands14a[0].confidence >= 95)
cands14b = matching_engine.suggest_candidates(line3, reference3, reference_attrs_index=idx3, top_n=3)
check("SAFETY PATCH #2 E: عائلة 'انتاج' لسا rank 1 بعد Safety Patch #2", bool(cands14b) and cands14b[0].item.code == "CORRECT" and cands14b[0].confidence >= 95)


print("\n--- summary ---")
total = len(results)
passed = sum(1 for _, ok in results if ok)
print(f"{passed}/{total} checks passed")
if passed != total:
    sys.exit(1)
