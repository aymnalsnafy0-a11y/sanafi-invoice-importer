"""
اختبارات 3 أنماط فشل Retrieval حقيقية (Benchmark حقيقي على فاتورة المراعي
2026-08-16، 12 سطر، مؤكَّدة بـGround Truth) - قبل الإصلاح كانت 5 من 12 سطر
تفشل الاسترجاع تماماً (retrieval_rank=None، not_found=5):

A) اختلاف تهجئة عربية شائعة لنفس الكلمة: فاتورة "شوكولاتة" مقابل قاعدة
   "شوكلاته" (حرف مد "و" ساقط) - _SPELLING_ALIASES بـitem_attributes.py.
B) اختلاف كتابة اسم ماركة تجارية: فاتورة "7 دايز" مقابل قاعدة "سفن دايز" -
   _BRAND_ALIASES بـitem_attributes.py.
C) الكتالوج يستخدم اسم "مظلة" عام ("نكهات") يغطي عدة نكهات فعلية بكود واحد،
   بينما الفاتورة تطبع النكهة المحدَّدة - مصدر استرجاع جديد "generic_variant"
   بـmatching_engine.py (محافظ: يتطلب تشارك كلمة عائلة المنتج الفعلية، ولا
   يمنع صنفاً محدداً بكوده الخاص من المنافسة العادية لو موجود).

هذا الملف بيانات مصطنعة بالكامل (بدون حاجة لملف AmnC حقيقي) - يعيد إنتاج
نفس الأنماط الثلاثة بشكل معزول وقابل للتكرار، بالإضافة لاختبارات سلامة
(الأنماط لا تدمج كلمات مختلفة فعلاً، ولا تتجاهل النكهة لو موجودة كأكواد
منفصلة).
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


import item_attributes
import matching_engine
from items import ReferenceItem
from line_item import ExtractedLine


def L(description, **kw):
    return ExtractedLine(
        raw_text="x", description=description, quantity=kw.pop("quantity", 1),
        unit_price=kw.pop("unit_price", None), total=1, ocr_confidence=100, **kw,
    )


print("=== A) SPELLING ALIAS: 'شوكولاتة' (فاتورة) مقابل 'شوكلاته' (قاعدة، حرف و ساقط) ===")
attrs_invoice_a = item_attributes.extract_attributes("كب كيك الشوكولاتة 60")
attrs_catalog_a = item_attributes.extract_attributes("لوزين كب كيك كراميل/شوكلاته/فراوله 60جم")
check("REAL BUG FIX A: الكلمتان تصيران نفس النص المطبَّع بعد الـalias (شوكولاته)", "شوكولاته" in attrs_invoice_a.normalized_text and "شوكولاته" in attrs_catalog_a.normalized_text)
check("سلامة: كلمات مختلفة فعلاً (كراميل/فراوله) ما تحوّلت لشوكولاته بالغلط", "كراميل" in attrs_catalog_a.normalized_text and "فراوله" in attrs_catalog_a.normalized_text)

reference_a = [
    ReferenceItem(code="CORRECT_A", name="لوزين كب كيك كراميل/شوكلاته/فراوله 60جم", barcode="6281007044103", default_unit="حبة", internal_id="1", unit_id="1"),
] + [
    ReferenceItem(code=f"NOISE{i}", name=f"صنف مختلف تماماً {i} 500جم", barcode="", default_unit="حبة", internal_id=str(i), unit_id="1")
    for i in range(40)
]
idx_a = matching_engine.build_reference_attrs_index(reference_a)
line_a = L("كب كيك الشوكولاتة 60")
line_a_attrs = item_attributes.extract_attributes(line_a.description)
hits_a = matching_engine._retrieve_candidate_hits(line_a, line_a_attrs, reference_a, idx_a, set())
check("REAL BUG FIX A: الصنف الصحيح صار يدخل الاسترجاع (كان يفشل تماماً قبل الإصلاح)", 0 in hits_a)
ranked_a = matching_engine._rank_and_cap_candidates(hits_a, line_a, reference_a, line_a_attrs, idx_a)
check("REAL BUG FIX A: الصنف الصحيح يوصل لمجموعة المرشّحين بعد القصّ (retrieval نجح فعلياً - هذا مقياس الاسترجاع الحقيقي، مو عتبة العرض النهائي المتأثرة بقوة إشارات ثانية غير متعلقة بهذا الإصلاح)", 0 in ranked_a)


print("\n=== B) BRAND ALIAS: '7 دايز' (فاتورة) مقابل 'سفن دايز' (قاعدة) ===")
attrs_invoice_b = item_attributes.extract_attributes("7 دايز عرض كرواسان")
attrs_catalog_b = item_attributes.extract_attributes("سفن دايز نكهات 55جم")
check("REAL BUG FIX B: '7 دايز' و'سفن دايز' يصيران نفس النص بعد الـalias", "سفن دايز" in attrs_invoice_b.normalized_text and "سفن دايز" in attrs_catalog_b.normalized_text)
check("REAL BUG FIX B (جانبي): الماركة صارت تُخمَّن (كانت None قبل الإصلاح - أول رمز رقم '7' كان يوقف التخمين)", attrs_invoice_b.brand is not None)

reference_b = [
    ReferenceItem(code="CORRECT_B", name="سفن دايز نكهات 55جم", barcode="6281183000061", default_unit="حبة", internal_id="1", unit_id="1"),
] + [
    ReferenceItem(code=f"NOISE{i}", name=f"صنف مختلف تماماً {i} 500جم", barcode="", default_unit="حبة", internal_id=str(i), unit_id="1")
    for i in range(40)
]
idx_b = matching_engine.build_reference_attrs_index(reference_b)
line_b = L("7 دايز عرض كرواسان")
line_b_attrs = item_attributes.extract_attributes(line_b.description)
hits_b = matching_engine._retrieve_candidate_hits(line_b, line_b_attrs, reference_b, idx_b, set())
check("REAL BUG FIX B: الصنف الصحيح صار يدخل الاسترجاع (كان يفشل تماماً قبل الإصلاح)", 0 in hits_b)
ranked_b = matching_engine._rank_and_cap_candidates(hits_b, line_b, reference_b, line_b_attrs, idx_b)
check("REAL BUG FIX B: الصنف الصحيح يوصل لمجموعة المرشّحين بعد القصّ", 0 in ranked_b)


print("\n=== C) GENERIC VARIANT: كتالوج 'كرواسان نكهات' (عام) مقابل فاتورة 'كرواسان الزعتر' (نكهة محدَّدة) ===")
reference_c = [
    ReferenceItem(code="CORRECT_C", name="لوزين كرواسان نكهات 60جم", barcode="6281100086116", default_unit="حبة", internal_id="1", unit_id="1"),
] + [
    ReferenceItem(code=f"NOISE{i}", name=f"صنف مختلف تماماً {i} 500جم", barcode="", default_unit="حبة", internal_id=str(i), unit_id="1")
    for i in range(40)
]
idx_c = matching_engine.build_reference_attrs_index(reference_c)
check("تأكيد مسبق: الفهرس فعلاً سجّل صف 'نكهات' تحت كلمة العائلة 'كرواسان'", 0 in idx_c.by_generic_variant_token.get("كرواسان", []))
line_c = L("كرواسان الزعتر 60غم")
line_c_attrs = item_attributes.extract_attributes(line_c.description)
hits_c = matching_engine._retrieve_candidate_hits(line_c, line_c_attrs, reference_c, idx_c, set())
check("REAL BUG FIX C: الصنف الصحيح ('نكهات' العام) صار يدخل الاسترجاع عبر مصدر generic_variant", 0 in hits_c and "generic_variant" in hits_c[0])
ranked_c = matching_engine._rank_and_cap_candidates(hits_c, line_c, reference_c, line_c_attrs, idx_c)
check("REAL BUG FIX C: الصنف الصحيح يوصل لمجموعة المرشّحين بعد القصّ", 0 in ranked_c)

print("\n--- Cب) سلامة: منتج عائلة مختلفة تماماً ما يُسحب بالغلط عبر نفس كلمة 'نكهات' ---")
reference_c2 = [
    ReferenceItem(code="MILK_GENERIC", name="لبن نكهات 200مل", barcode="", default_unit="حبة", internal_id="1", unit_id="1"),
    ReferenceItem(code="CROISSANT_GENERIC", name="لوزين كرواسان نكهات 60جم", barcode="", default_unit="حبة", internal_id="2", unit_id="1"),
]
idx_c2 = matching_engine.build_reference_attrs_index(reference_c2)
line_c2 = L("كرواسان الزعتر 60غم")
line_c2_attrs = item_attributes.extract_attributes(line_c2.description)
hits_c2 = matching_engine._retrieve_candidate_hits(line_c2, line_c2_attrs, reference_c2, idx_c2, set())
check("سلامة: صنف 'لبن نكهات' (عائلة مختلفة تماماً) ما دخل الاسترجاع عبر generic_variant لسطر كرواسان", "generic_variant" not in hits_c2.get(0, set()))
check("سلامة: صنف 'كرواسان نكهات' (نفس العائلة) لسا يدخل عادي", "generic_variant" in hits_c2.get(1, set()))

print("\n--- Cج) سلامة الأهم: لو النكهة المحدَّدة موجودة كصنف مستقل بكوده الخاص، ما تُستبعد لصالح 'نكهات' العام ---")
reference_c3 = [
    ReferenceItem(code="SPECIFIC_ZAATAR", name="لوزين كرواسان الزعتر 60جم", barcode="", default_unit="حبة", internal_id="1", unit_id="1"),
    ReferenceItem(code="GENERIC_FLAVORS", name="لوزين كرواسان نكهات 60جم", barcode="", default_unit="حبة", internal_id="2", unit_id="1"),
]
idx_c3 = matching_engine.build_reference_attrs_index(reference_c3)
line_c3 = L("كرواسان الزعتر 60غم")
line_c3_attrs = item_attributes.extract_attributes(line_c3.description)
hits_c3 = matching_engine._retrieve_candidate_hits(line_c3, line_c3_attrs, reference_c3, idx_c3, set())
ranked_c3 = matching_engine._rank_and_cap_candidates(hits_c3, line_c3, reference_c3, line_c3_attrs, idx_c3)
check("سلامة: كلا الصنفين (المحدَّد والعام) يدخلان مجموعة المرشّحين بعد القصّ (صفر أحدهما يُستبعد بصمت من الاسترجاع)", {reference_c3[i].code for i in ranked_c3} == {"SPECIFIC_ZAATAR", "GENERIC_FLAVORS"})
scored_c3 = sorted(matching_engine._score_all_candidates(line_c3, reference_c3, idx_c3, set()), key=lambda c: -c.confidence)
check("REAL SAFETY: الصنف المحدَّد (تطابق اسم أدق فعلياً) يتصدّر فعلياً بالثقة على العام (وليس بفارق بسيط)", scored_c3[0].item.code == "SPECIFIC_ZAATAR" and scored_c3[0].confidence > scored_c3[1].confidence + 10)


print("\n=== D) إعادة تأكيد (regression): الوطنية/انتاج + Order-Invariance لسا سليمة بعد كل تعديلات هذا الملف ===")
_FLAVORS_40 = [f"نكهة{i}" for i in range(39)] + ["نكهة الصنف الصحيح"]
reference_d = [
    ReferenceItem(code=("CORRECT" if f == "نكهة الصنف الصحيح" else f"OTHER{i}"), name=f"الوطنية {f} 350جم", barcode="", default_unit="حبة", internal_id=str(i), unit_id="1")
    for i, f in enumerate(_FLAVORS_40)
]
idx_d = matching_engine.build_reference_attrs_index(reference_d)
line_d = L("الوطنية نكهة الصنف الصحيح 350جم")
cands_d = matching_engine.suggest_candidates(line_d, reference_d, reference_attrs_index=idx_d, top_n=5)
check("REGRESSION: عائلة 'الوطنية' لسا rank 1 بعد أنماط الإصلاح الثلاثة", bool(cands_d) and cands_d[0].item.code == "CORRECT" and cands_d[0].confidence >= 95)


print("\n--- summary ---")
total = len(results)
passed = sum(1 for _, ok in results if ok)
print(f"{passed}/{total} checks passed")
if passed != total:
    sys.exit(1)
