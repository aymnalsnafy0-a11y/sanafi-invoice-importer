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

# عزل الاختبار تماماً عن الملف الحقيقي
tmp_dir = tempfile.mkdtemp()
learned_matches._MATCHES_FILE = Path(tmp_dir) / "learned_matches_test.json"


class FakeRefItem:
    def __init__(self, code, name, internal_id="1", unit_id="1", barcode="", default_unit="حبة"):
        self.code = code
        self.name = name
        self.internal_id = internal_id
        self.unit_id = unit_id
        self.barcode = barcode
        self.default_unit = default_unit


print("--- fresh state ---")
check("no match for anything on a fresh file", learned_matches.lookup("شركة بن خميس", "حليب نيدو") is None)

print("\n--- first confirmation ---")
item = FakeRefItem(code="6281100113133", name="حليب مجفف وادي فاطمه 1800ج", internal_id="775", unit_id="2", barcode="6281100113133")
learned_matches.record_confirmation("شركة بن خميس", "نيدو علب 1800كغ", item)

hit = learned_matches.lookup("شركة بن خميس", "نيدو علب 1800كغ")
check("exact same supplier+text now resolves", hit is not None)
if hit:
    entry, count = hit
    check("resolves to the confirmed item code", entry["matched_item_code"] == "6281100113133")
    check("confirm_count starts at 1", count == 1)
    check("initial confidence = 80%", learned_matches.confidence_for_confirm_count(count) == 80.0)

print("\n--- fuzzy lookup within same supplier (slightly different wording) ---")
hit2 = learned_matches.lookup("شركة بن خميس", "نيدو 1800كغ علب")  # نفس الكلمات، ترتيب مختلف قليلاً
check("near-identical wording (word order) still resolves via fuzzy lookup", hit2 is not None)

print("\n--- different supplier, same text: must NOT match ---")
hit3 = learned_matches.lookup("مورد ثاني تماماً", "نيدو علب 1800كغ")
check("different supplier does not reuse another supplier's mapping", hit3 is None)

print("\n--- repeated confirmation of the SAME item raises confidence ---")
for _ in range(4):
    learned_matches.record_confirmation("شركة بن خميس", "نيدو علب 1800كغ", item)
hit4 = learned_matches.lookup("شركة بن خميس", "نيدو علب 1800كغ")
entry4, count4 = hit4
check("confirm_count accumulated to 5 total", count4 == 5)
check("confidence climbed to 99% (80 + 4*5 = 100, capped at 99)", learned_matches.confidence_for_confirm_count(count4) == 99.0)

print("\n--- user later corrects to a DIFFERENT item: correction wins, count resets ---")
different_item = FakeRefItem(code="9999999", name="صنف مختلف تماماً", internal_id="2", unit_id="1")
learned_matches.record_confirmation("شركة بن خميس", "نيدو علب 1800كغ", different_item)
hit5 = learned_matches.lookup("شركة بن خميس", "نيدو علب 1800كغ")
entry5, count5 = hit5
check("correction overwrites to the new item code", entry5["matched_item_code"] == "9999999")
check("confirm_count resets to 1 after a correction (new trust starts fresh)", count5 == 1)

print("\n--- summary ---")
total = len(results)
passed = sum(1 for _, ok in results if ok)
print(f"{passed}/{total} checks passed")
if passed != total:
    sys.exit(1)
