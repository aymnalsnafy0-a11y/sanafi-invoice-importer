import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, r"D:\scanar\invoice_importer")

results = []


def check(name, condition):
    results.append((name, bool(condition)))
    print(("PASS" if condition else "FAIL"), "-", name)


import matching_engine
from items import ReferenceItem
from line_item import ExtractedLine

# محاكاة بالضبط سيناريو المستخدم الحقيقي: صنف واحد "أرز أبو كاس 10 كيلو"
# له 3 صفوف بالمرجع (باركود/وحدة مختلفة لكل صف - نفس رقم الصنف 2027 ونفس
# الاسم بالضبط)، تماماً زي ما يصير من _load_from_amnc_xml لصنف متعدد الوحدات
reference = [
    ReferenceItem(code="2027", name="ارز ابو كاس 10 ك", barcode="1111111111111", default_unit="حبة", internal_id="55", unit_id="1"),
    ReferenceItem(code="2027", name="ارز ابو كاس 10 ك", barcode="2222222222222", default_unit="كرتون", internal_id="55", unit_id="2"),
    ReferenceItem(code="2027", name="ارز ابو كاس 10 ك", barcode="3333333333333", default_unit="بالة", internal_id="55", unit_id="3"),
    ReferenceItem(code="9999", name="صنف ثاني مختلف تماماً", barcode="4444444444444", default_unit="حبة", internal_id="66", unit_id="1"),
]
attrs_index = matching_engine.build_reference_attrs_index(reference)

line = ExtractedLine(
    raw_text="x", description="ارز ابو كاس 10 كيلو بستي", quantity=1, unit_price=200, total=200, ocr_confidence=100,
)

print("--- REAL BUG FIX: an item with 3 reference rows (one per unit/barcode) no longer shows as 3 identical suggestions ---")
candidates = matching_engine.suggest_candidates(line, reference, supplier_name="مورد تجريبي", reference_attrs_index=attrs_index, top_n=5)
codes_seen = [c.item.code for c in candidates]
check("code 2027 appears only ONCE in the suggestion list (not 3 times)", codes_seen.count("2027") == 1)
check("the single 2027 suggestion is the list's only entry (irrelevant item 9999 correctly filtered out, unrelated to dedup)", codes_seen == ["2027"])

print("\n--- sanity: a genuinely relevant SECOND item still gets its own slot alongside the deduped one ---")
reference2 = reference + [
    ReferenceItem(code="3040", name="ارز ابو كاس 5 ك", barcode="5555555555555", default_unit="حبة", internal_id="77", unit_id="1"),
]
attrs_index2 = matching_engine.build_reference_attrs_index(reference2)
candidates2 = matching_engine.suggest_candidates(line, reference2, supplier_name="مورد تجريبي", reference_attrs_index=attrs_index2, top_n=5)
codes_seen2 = [c.item.code for c in candidates2]
check("both distinct, relevant items appear (2027 once, 3040 once) - not squeezed out by duplicate rows", codes_seen2.count("2027") == 1 and "3040" in codes_seen2)

print("\n--- summary ---")
total = len(results)
passed = sum(1 for _, ok in results if ok)
print(f"{passed}/{total} checks passed")
if passed != total:
    sys.exit(1)
