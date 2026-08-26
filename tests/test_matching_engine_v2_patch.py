"""
اختبارات patch v2 على محرك المطابقة: (1) الذاكرة المتعلّمة تشارك بمقارنة
آمنة بدل التصدّر المطلق، (2) سقف المرشّحين يراعي جودة المصدر بدل العدد
الخام، (3) اختلاف تخمين الماركة عقوبة خفيفة بدل سقف صارم.
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
import item_attributes
import matching_engine
import semantic_matcher

# اختبارات محرك محلي بحت - ممنوع تعتمد على اتصال إنترنت حقيقي أو تدفع
# تكلفة فعلية. نعطّل استدعاء AI الحقيقي بغض النظر عن حالة الجهاز الفعلية.
semantic_matcher.rerank = lambda *a, **k: None
from items import ReferenceItem
from line_item import ExtractedLine


def L(description, **kw):
    return ExtractedLine(
        raw_text="x", description=description, quantity=1, unit_price=kw.pop("unit_price", None), total=1,
        ocr_confidence=100, **kw,
    )


print("=== Patch 1: الذاكرة المتعلّمة لا تتصدّر بشكل مطلق - مرشّح أسلم يتصدّرها لو تعارضت ===")
reference1 = [
    ReferenceItem(code="OLD1", name="نيدو حليب مجفف 1800 جم", barcode="", default_unit="", internal_id="1", unit_id="1"),
    ReferenceItem(code="NEW1", name="نيدو حليب مجفف فل كريم 400 جم كرتون 24 حبة", barcode="", default_unit="كرتون", internal_id="2", unit_id="1"),
]
idx1 = matching_engine.build_reference_attrs_index(reference1)
# تأكيد قديم (5 مرات - ثقة عالية جداً) يشير لـOLD1، بس نص الفاتورة الحالي
# فعلياً يطابق NEW1 تماماً (نفس الحجم/العدد/التعبئة) - تعبئة المورد تغيّرت
for _ in range(5):
    learned_matches.record_confirmation("مورد الحليب الجديد", "نيدو 400 جم كرتون 24 حبة", reference1[0])
line1 = L("نيدو 400 جم كرتون 24 حبة")
c1 = matching_engine.suggest_candidates(line1, reference1, supplier_name="مورد الحليب الجديد", reference_attrs_index=idx1, top_n=5)
check("تأكيد مسبق: OLD1 (الذاكرة) لسا يظهر بالقائمة (تعارض معروض، مو مخفي)", any(c.item.code == "OLD1" for c in c1))
check("REAL BUG FIX: المرشّح الأول صار NEW1 (الأسلم فعلياً)، مو OLD1 (الذاكرة المتعارضة) بشكل مطلق", c1[0].item.code == "NEW1")
old1_candidate = next(c for c in c1 if c.item.code == "OLD1")
check("OLD1 نفسه لسا مقصوصة ثقته بسبب التعارض (السلوك الأمني الأساسي محفوظ)", old1_candidate.confidence <= config.MATCH_ATTRIBUTE_CONFLICT_CAP)

matching_engine.enhance_one(line1, reference1, supplier_name="مورد الحليب الجديد", reference_attrs_index=idx1)
check("enhance_one: ما يقبل الذاكرة المتعارضة تلقائياً (best الفعلي صار NEW1، مو OLD1)", line1.matched_item_code != "OLD1")

print("\n--- Patch 1: السلوك القديم (ذاكرة بلا تعارض) يبقى كما هو - تتصدّر بشكل طبيعي ---")
reference1b = [ReferenceItem(code="F1", name="عصير المراعي برتقال 1 لتر", barcode="", default_unit="", internal_id="7", unit_id="1")]
idx1b = matching_engine.build_reference_attrs_index(reference1b)
learned_matches.record_confirmation("مورد العصائر", "برتقال المراعي", reference1b[0])
line1b = L("برتقال المراعي")
c1b = matching_engine.suggest_candidates(line1b, reference1b, supplier_name="مورد العصائر", reference_attrs_index=idx1b, top_n=5)
check("ذاكرة بلا أي تعارض لسا تتصدّر بشكل طبيعي (مو مكسورة بالتعديل)", bool(c1b) and c1b[0].item.code == "F1" and "مؤكَّدة" in c1b[0].reason)

print("\n--- Patch 1: التوافق مع بيانات learned_matches.json قديمة (بنية غير مُعدَّلة) ---")
old_format_file = Path(tmp_dir) / "old_format_learned_matches.json"
old_format_file.write_text(
    """{"matches": {"مورد قديم||صنف قديم النص": {"matched_item_code": "OLDFMT", "matched_item_name": "صنف قديم", "matched_internal_id": "99", "matched_unit_id": "1", "barcode": "", "unit": "", "confirm_count": 3, "first_confirmed": "2025-01-01T00:00:00+00:00", "last_confirmed": "2025-01-01T00:00:00+00:00"}}}""",
    encoding="utf-8",
)
learned_matches._MATCHES_FILE = old_format_file
lookup_result = learned_matches.lookup("مورد قديم", "صنف قديم النص")
check("lookup() يقرأ ملف بصيغة قديمة (قبل هذا الـpatch) بدون أي مشكلة", lookup_result is not None and lookup_result[0]["matched_item_code"] == "OLDFMT")
check("codes_confirmed_for_supplier() يقرأ نفس الملف القديم بدون مشكلة", learned_matches.codes_confirmed_for_supplier("مورد قديم") == {"OLDFMT"})
learned_matches._MATCHES_FILE = Path(tmp_dir) / "learned_matches_test.json"  # نرجّع الملف الطبيعي لباقي الاختبارات


print("\n=== Patch 2: أكثر من 80 مرشّح 'حجم فقط' ما تستبعد مرشّحين أقوى (اسم/تاريخ مورد) من السقف ===")
size_only_refs = [
    ReferenceItem(code=f"SIZE{i}", name=f"صنف عشوائي رقم {i} 1 لتر", barcode="", default_unit="", internal_id=str(i), unit_id="1")
    for i in range(150)  # أكثر بكثير من السقف (80)، كلهم بمصدر "حجم" وحده
]
good_name_ref = ReferenceItem(code="GOODNAME", name="سنافي شاي احمر فاخر توينينغز 1 لتر", barcode="", default_unit="", internal_id="900", unit_id="1")
good_supplier_ref = ReferenceItem(code="GOODSUPP", name="صنف غريب الشكل بلا أي علاقة بالنص إطلاقاً", barcode="", default_unit="", internal_id="901", unit_id="1")
reference2 = size_only_refs + [good_name_ref, good_supplier_ref]
idx2 = matching_engine.build_reference_attrs_index(reference2)

learned_matches.record_confirmation("مورد الشاي", "نص قديم مختلف تماماً", good_supplier_ref)
supplier_codes2 = learned_matches.codes_confirmed_for_supplier("مورد الشاي")

# السطر نفسه فيه "1 لتر" (يطابق حجم كل الـ150 صنف الوهمي كمان) - عشان نختبر
# سيناريو واقعي: الصنف المطلوب فعلياً يشارك نفس الحجم الشائع مع مئات
# البدائل، ولازم لا يضيع بينهم
line2 = L("سنافي شاي احمر فاخر توينينغز 1 لتر")  # يطابق GOODNAME بالاسم، وبنفس الوقت يشغّل مصدر الحجم اللي يطعّم المئة والخمسين المرشّح الوهمي
line2_attrs = item_attributes.extract_attributes(line2.description)
hits2 = matching_engine._retrieve_candidate_hits(line2, line2_attrs, reference2, idx2, supplier_codes2)
size_only_count = sum(1 for srcs in hits2.values() if srcs == {"size"})
check(f"تأكيد مسبق: فعلاً فيه أكثر من 80 مرشّح 'حجم فقط' بهذا السيناريو ({size_only_count})", size_only_count > matching_engine._MAX_CANDIDATES_BEFORE_SCORING)

ranked2 = matching_engine._rank_and_cap_candidates(hits2)
ranked_codes2 = {reference2[i].code for i in ranked2}
check("REAL BUG FIX: مرشّح الاسم القوي (GOODNAME) ينجو من القصّ رغم زحمة مرشّحي الحجم", "GOODNAME" in ranked_codes2)
check("REAL BUG FIX: مرشّح تاريخ المورد (GOODSUPP) ينجو من القصّ رغم زحمة مرشّحي الحجم", "GOODSUPP" in ranked_codes2)
check("السقف الأقصى العام (80) لسا محترم", len(ranked2) <= matching_engine._MAX_CANDIDATES_BEFORE_SCORING)

full_candidates2 = matching_engine.suggest_candidates(line2, reference2, supplier_name="مورد الشاي", reference_attrs_index=idx2, top_n=5)
check("النتيجة النهائية فعلياً تحتوي GOODNAME (يوصل لآخر القائمة، مو بس الفهرسة الداخلية)", any(c.item.code == "GOODNAME" for c in full_candidates2))


print("\n=== Patch 3: كلمة نوع منتج عامة (عصير/حليب) اتخمّنت كماركة - اختلافها ما يرفض مرشّح صحيح ===")
reference3 = [ReferenceItem(code="JUICE1", name="عصير المراعي برتقال طازج 1 لتر", barcode="", default_unit="", internal_id="1", unit_id="1")]
idx3 = matching_engine.build_reference_attrs_index(reference3)
# نفس الصنف فعلياً، بس أول كلمة بالفاتورة "برتقال" (مو "عصير") - يخمّن
# البرنامج ماركتين مختلفتين ("عصير" مقابل "برتقال") لنفس الصنف بالضبط
line3 = L("برتقال طازج المراعي 1 لتر")
brand_line3 = item_attributes.extract_attributes(line3.description).brand
brand_ref3 = item_attributes.extract_attributes(reference3[0].name).brand
check(f"تأكيد مسبق: فعلاً اتخمّنت ماركتان مختلفتان ('{brand_line3}' مقابل '{brand_ref3}')", brand_line3 != brand_ref3 and brand_line3 and brand_ref3)

c3 = matching_engine.suggest_candidates(line3, reference3, supplier_name=None, reference_attrs_index=idx3, top_n=5)
check("REAL BUG FIX: المرشّح الصحيح لسا يظهر رغم اختلاف تخمين الماركة", bool(c3) and c3[0].item.code == "JUICE1")
check("ثقته تبقى عالية معقولة (عقوبة خفيفة بس، مو سقف صارم زي التصميم القديم)", c3[0].confidence >= 70)
check("لا يوجد '⚠' بسبب الماركة تحديداً (مو تعارض حقيقي، إشارة ضعيفة بس)", "اختلاف الماركة" not in c3[0].reason or "⚠" not in c3[0].reason)

print("\n--- Patch 3: مثال ثاني بكلمة 'حليب' (نوع منتج عام) ---")
reference3b = [ReferenceItem(code="MILK1", name="حليب نادك كامل الدسم طويل الأجل 1 لتر", barcode="", default_unit="", internal_id="2", unit_id="1")]
idx3b = matching_engine.build_reference_attrs_index(reference3b)
line3b = L("نادك كامل الدسم طويل الأجل 1 لتر")  # أول كلمة "نادك" (الماركة الحقيقية) مو "حليب"
c3b = matching_engine.suggest_candidates(line3b, reference3b, supplier_name=None, reference_attrs_index=idx3b, top_n=5)
check("مثال 'حليب' الثاني: المرشّح الصحيح يظهر بثقة معقولة رغم اختلاف تخمين الماركة", bool(c3b) and c3b[0].item.code == "MILK1" and c3b[0].confidence >= 70)

print("\n--- Patch 3: موافقة الماركة لسا مكافأة إيجابية (بس خفيفة) ---")
reference3c = [ReferenceItem(code="SAME1", name="ديتول مطهر 1 لتر", barcode="", default_unit="", internal_id="3", unit_id="1")]
idx3c = matching_engine.build_reference_attrs_index(reference3c)
line3c = L("ديتول تعقيم منزلي 1 لتر")  # نفس الماركة "ديتول" بالضبط بالطرفين
c3c = matching_engine.suggest_candidates(line3c, reference3c, supplier_name=None, reference_attrs_index=idx3c, top_n=1)
check("موافقة الماركة لسا تُذكر بالسبب كإشارة إيجابية", bool(c3c) and "الماركة متطابقة" in c3c[0].reason)


print("\n--- summary ---")
total = len(results)
passed = sum(1 for _, ok in results if ok)
print(f"{passed}/{total} checks passed")
if passed != total:
    sys.exit(1)
