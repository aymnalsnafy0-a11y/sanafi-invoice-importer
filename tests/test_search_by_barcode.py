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
]

print("--- REAL BUG FIX: searching by a full barcode now finds the item (used to only search by name) ---")
results1 = _search_reference_items(reference, "6281100084013")
check("full barcode search returns exactly the matching item first", bool(results1) and results1[0][1].code == "500")
check("barcode match scores at full confidence (100)", results1[0][0] == 100.0)

print("\n--- searching by a PARTIAL barcode (substring) still finds it ---")
results2 = _search_reference_items(reference, "84013")
check("partial/trailing barcode digits still find the right item", bool(results2) and results2[0][1].code == "500")

print("\n--- searching by item code works too ---")
results3 = _search_reference_items(reference, "601")
check("searching by item code finds the right item", bool(results3) and results3[0][1].code == "601")

print("\n--- searching by name (the original behavior) still works exactly as before ---")
results4 = _search_reference_items(reference, "حليب نادك")
check("name-based fuzzy search still works", bool(results4) and results4[0][1].code == "500")

print("\n--- a barcode/code match ranks ABOVE a coincidentally-similar name match ---")
reference_tricky = reference + [
    ReferenceItem(code="999", name="6281100084013 شي غريب مو الصنف الصحيح", barcode="", default_unit="حبة", internal_id="11", unit_id="1"),
]
results5 = _search_reference_items(reference_tricky, "6281100084013")
check("the REAL barcode match (code 500) still wins even against a name containing the same digits", results5[0][1].code == "500")

print("\n--- empty query / empty reference return no results (no crash) ---")
check("empty query returns empty list", _search_reference_items(reference, "") == [])
check("empty reference returns empty list", _search_reference_items([], "test") == [])

print("\n--- summary ---")
total = len(results)
passed = sum(1 for _, ok in results if ok)
print(f"{passed}/{total} checks passed")
if passed != total:
    sys.exit(1)
