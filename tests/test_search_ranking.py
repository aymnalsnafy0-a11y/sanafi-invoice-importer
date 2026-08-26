"""
اختبار ترتيب _search_reference_items (البحث اليدوي بنافذة المراجعة وزر
البحث اليمين) - يثبت الترتيب الفعلي للنتائج (مو بس وجودها)، حسب الأولوية
المطلوبة: باركود تام -> رقم صنف تام -> باركود/رقم صنف جزئي -> اسم تام ->
اسم يبدأ بالاستعلام -> اسم يحتويه -> fuzzy كـfallback بس. أي تطابق حرفي
بالاسم يجب أن يسبق أي نتيجة fuzzy-only، بغض النظر عن رقم التشابه الضبابي
(حالة "ريتا" الحقيقية - راجع app.py::_search_reference_items).
"""

import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, r"D:\scanar\invoice_importer")

results = []


def check(name, condition):
    results.append((name, bool(condition)))
    print(("PASS" if condition else "FAIL"), "-", name)


from app import _search_reference_items
from items import ReferenceItem

reference = [
    ReferenceItem(code="500", name="حليب نادك كامل الدسم 1 لتر", barcode="6281100084013", default_unit="كرتون", internal_id="9", unit_id="2"),
    ReferenceItem(code="601", name="بيبسي 330 مل كرتون 24 حبة", barcode="6281007059183", default_unit="كرتون", internal_id="10", unit_id="3"),
    ReferenceItem(code="700", name="ريتا", barcode="", default_unit="حبة", internal_id="20", unit_id="1"),
    ReferenceItem(code="701", name="عصير ريتا مانجو 1 لتر", barcode="", default_unit="كرتون", internal_id="21", unit_id="2"),
    ReferenceItem(code="702", name="ريتا عصير برتقال 250 مل كرتون 24 حبة", barcode="", default_unit="كرتون", internal_id="22", unit_id="3"),
    ReferenceItem(code="703", name="رتينا كريم مرطب", barcode="", default_unit="حبة", internal_id="23", unit_id="1"),
    ReferenceItem(code="704", name="ريحان أخضر طازج", barcode="", default_unit="حبة", internal_id="24", unit_id="1"),
]

print("--- 1) exact barcode يتصدّر ---")
r = _search_reference_items(reference, "6281100084013")
check("exact barcode: أول نتيجة هي الصنف 500", bool(r) and r[0][1].code == "500")
check("exact barcode: ثقة كاملة 100", r[0][0] == 100.0)

print("\n--- 2) exact item code يتصدّر ---")
r = _search_reference_items(reference, "601")
check("exact code: أول نتيجة هي الصنف 601", bool(r) and r[0][1].code == "601")
check("exact code: ثقة كاملة 100", r[0][0] == 100.0)

print("\n--- 3) باركود/رقم صنف جزئي (contains) ---")
r = _search_reference_items(reference, "84013")
check("partial barcode: أول نتيجة هي الصنف 500 رغم عدم التطابق التام", bool(r) and r[0][1].code == "500")

print("\n--- 4) اسم مطابق تماماً (بعد التطبيع) يسبق startswith/contains لنفس الاستعلام ---")
r = _search_reference_items(reference, "ريتا")
check("exact name: أول نتيجة هي الصنف 700 ('ريتا' تماماً)", bool(r) and r[0][1].code == "700")
codes_order = [item.code for _score, item in r]
check("exact name (700) يسبق startswith (702)", codes_order.index("700") < codes_order.index("702"))
check("startswith (702) يسبق contains (701)", codes_order.index("702") < codes_order.index("701"))

print("\n--- 5) اسم يبدأ بالاستعلام (startswith) بدون تطابق تام ---")
r = _search_reference_items(reference, "ريتا عصير")
check("startswith: أول نتيجة هي الصنف 702 ('ريتا عصير برتقال...' يبدأ بالضبط بهذا)", bool(r) and r[0][1].code == "702")

print("\n--- 6) اسم يحتوي الاستعلام (contains) بدون بداية مطابقة ---")
r = _search_reference_items(reference, "ريتا مانجو")
check("contains: أول نتيجة هي الصنف 701 ('عصير ريتا مانجو...' يحتويها بالمنتصف)", bool(r) and r[0][1].code == "701")

print("\n--- 7) REAL BUG FIX: 'ريتا عصير برتقال...' (تطابق حرفي حقيقي) يسبق 'رتينا'/'ريحان' (fuzzy فقط غير ذات صلة) ---")
r = _search_reference_items(reference, "ريتا")
codes_order = [item.code for _score, item in r]
check("702 (تطابق حرفي - startswith) يسبق 703 ('رتينا' - fuzzy فقط)", codes_order.index("702") < codes_order.index("703"))
check("702 (تطابق حرفي - startswith) يسبق 704 ('ريحان' - fuzzy فقط)", codes_order.index("702") < codes_order.index("704"))
check("701 (تطابق حرفي - contains) يسبق 703 ('رتينا' - fuzzy فقط)", codes_order.index("701") < codes_order.index("703"))

print("\n--- 8) fuzzy fallback لسا يشتغل لما ما فيه أي تطابق حرفي إطلاقاً ---")
r = _search_reference_items(reference, "حليب ندك")  # خطأ إملائي متعمّد - بلا ألف، بلا أي substring حرفي بأي اسم
check("fuzzy fallback: لسا يلقط الصنف 500 (حليب نادك) كأفضل نتيجة رغم غياب أي تطابق حرفي", bool(r) and r[0][1].code == "500")
check("fuzzy fallback: النتيجة ليست 100 (فعلاً طبقة fuzzy، مو تطابق حرفي)", r[0][0] < 100.0)

print("\n--- summary ---")
passed = sum(1 for _, ok in results if ok)
print(f"{passed}/{len(results)} checks passed")
if passed != len(results):
    sys.exit(1)
