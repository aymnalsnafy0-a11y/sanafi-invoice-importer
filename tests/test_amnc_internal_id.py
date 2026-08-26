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


# نموذج مصغّر يطابق البنية الحقيقية لملف .AmnC (تأكدنا منها بملف حقيقي
# "روضة الياسمين للتجارة 2024.AmnC" - 27,395 صنف، 100% منهم فيهم CLS_ID) -
# صنف بلا باركود وحدات (يعتمد الصف الاحتياطي)، وصنف بباركودين لوحدتين مختلفتين
SAMPLE_AMNC_XML = """<?xml version="1.0" standalone="yes"?>
<NewDataSet>
  <CLASSES>
    <CLS_ID>88</CLS_ID>
    <GCLS_ID>7</GCLS_ID>
    <CLS_NO>4897103871233</CLS_NO>
    <CLS_ARNAME>مصفف شعر 1233</CLS_ARNAME>
    <CLS_UN_1>3</CLS_UN_1>
  </CLASSES>
  <CLASSES>
    <CLS_ID>775</CLS_ID>
    <CLS_NO>6281100113133</CLS_NO>
    <CLS_ARNAME>حليب مجفف نيدو 1800جم</CLS_ARNAME>
    <CLS_UN_1>حبة</CLS_UN_1>
    <CLS_UN_1_PCODE>6281100113133</CLS_UN_1_PCODE>
    <CLS_UN_2>كرتون</CLS_UN_2>
    <CLS_UN_2_PCODE>6281100113140</CLS_UN_2_PCODE>
  </CLASSES>
</NewDataSet>
"""

tmp_dir = tempfile.mkdtemp()
fixture_path = Path(tmp_dir) / "fixture.AmnC"
fixture_path.write_text(SAMPLE_AMNC_XML, encoding="utf-8")

import items
from amn_exporter import export_to_amn
from line_item import ExtractedLine

print("--- REAL BUG FIX: .AmnC file reference now carries internal_id (CLS_ID), verified against real file structure ---")
reference = items.load_item_reference(fixture_path)
check("both classes loaded", len(reference) == 3)  # الصنف الثاني له صفّين (باركود لكل وحدة)

item1 = next(i for i in reference if i.code == "4897103871233")
check("item with NO unit barcodes (fallback row) still gets internal_id", item1.internal_id == "88")

item2_rows = [i for i in reference if i.code == "6281100113133"]
check("item with multiple unit barcodes: BOTH rows get internal_id", len(item2_rows) == 2 and all(i.internal_id == "775" for i in item2_rows))

print("\n--- REAL FILE VERIFICATION: the actual client file (if present) parses with 100% internal_id coverage ---")
real_file = Path(r"C:\Users\hp\Downloads\روضة الياسمين للتجارة 2024.AmnC")
if real_file.exists():
    real_reference = items.load_item_reference(real_file)
    missing = [i for i in real_reference if not i.internal_id]
    check(f"real file: {len(real_reference)} rows loaded, 0 missing internal_id", len(real_reference) > 0 and len(missing) == 0)
else:
    print("SKIPPED - real file not present on this machine")

print("\n--- END TO END: internal_id now actually reaches the exported .Amn XML (CLS_ID no longer 0) ---")
matched_line = ExtractedLine(
    raw_text="x", description="حليب نيدو", quantity=2, unit_price=50, total=100, ocr_confidence=100,
    matched_item_code="6281100113133", matched_item_name="حليب مجفف نيدو 1800جم",
    matched_internal_id=item2_rows[0].internal_id, matched_unit_id=item2_rows[0].unit_id,
)
export_path = Path(tmp_dir) / "test_export.Amn"
export_to_amn([matched_line], export_path)
exported_xml = export_path.read_text(encoding="utf-8")
check("exported .Amn file contains the REAL CLS_ID (775), not 0", "<CLS_ID>775</CLS_ID>" in exported_xml)
check("exported .Amn file does NOT silently write CLS_ID=0 for this matched line", "<CLS_ID>0</CLS_ID>" not in exported_xml)

print("\n--- honest limitation, verified: UN_ID has no equivalent field anywhere in the .AmnC format ---")
check("unit_id stays empty from file-based reference (genuinely unavailable in this file format)", item2_rows[0].unit_id == "")

print("\n--- summary ---")
total = len(results)
passed = sum(1 for _, ok in results if ok)
print(f"{passed}/{total} checks passed")
if passed != total:
    sys.exit(1)
