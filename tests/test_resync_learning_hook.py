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

# عزل تام عن الملفات الحقيقية - قبل استيراد app (يستورد learned_matches/settings)
tmp_dir = tempfile.mkdtemp()
learned_matches._MATCHES_FILE = Path(tmp_dir) / "learned_matches_test.json"
settings_module._SETTINGS_FILE = Path(tmp_dir) / "matching_settings_test.json"

import app
from items import ReferenceItem
from line_item import ExtractedLine

a = app.InvoiceImporterApp()
a.withdraw()

reference = [
    ReferenceItem(code="500", name="حليب نادك كامل الدسم 1 لتر", barcode="1111", default_unit="كرتون", internal_id="9", unit_id="2"),
]
a.last_reference = reference
a.invoice_supplier_name = "مورد الاختبار"

line = ExtractedLine(raw_text="x", description="حليب نادك 1 لتر بالضبط كذا", quantity=1, unit_price=8, total=8, ocr_confidence=100)

print("--- manual code edit resolves against reference AND feeds the learning table ---")
a._resync_from_reference(line, key="code", value="500")
check("manual edit resolved matched_internal_id (needed for .Amn export)", line.matched_internal_id == "9")
check("manual edit resolved matched_unit_id", line.matched_unit_id == "2")

learned = learned_matches.lookup("مورد الاختبار", "حليب نادك 1 لتر بالضبط كذا")
check("the manual confirmation was recorded into learned_matches", learned is not None)
if learned:
    entry, confirm_count = learned
    check("learned entry points to the correct item code", entry["matched_item_code"] == "500")
    check("first confirmation has confirm_count == 1", confirm_count == 1)

print("\n--- a second manual confirmation of the SAME mapping increments confidence ---")
line2 = ExtractedLine(raw_text="x", description="حليب نادك 1 لتر بالضبط كذا", quantity=1, unit_price=8, total=8, ocr_confidence=100)
a._resync_from_reference(line2, key="code", value="500")
learned2 = learned_matches.lookup("مورد الاختبار", "حليب نادك 1 لتر بالضبط كذا")
check("confirm_count climbed to 2 after a second manual confirmation", learned2 is not None and learned2[1] == 2)

print("\n--- an unresolvable manual code does NOT get recorded as a false confirmation ---")
line3 = ExtractedLine(raw_text="x", description="صنف غير معروف إطلاقاً", quantity=1, unit_price=8, total=8, ocr_confidence=100)
a._resync_from_reference(line3, key="code", value="999999-not-in-reference")
check("unresolved code leaves matched_internal_id blank", line3.matched_internal_id == "")
learned3 = learned_matches.lookup("مورد الاختبار", "صنف غير معروف إطلاقاً")
check("no bogus learned entry created for an unresolved manual code", learned3 is None)

a.destroy()

print("\n--- summary ---")
total = len(results)
passed = sum(1 for _, ok in results if ok)
print(f"{passed}/{total} checks passed")
if passed != total:
    sys.exit(1)
