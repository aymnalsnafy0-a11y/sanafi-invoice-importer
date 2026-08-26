import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, r"D:\scanar\invoice_importer")

results = []


def check(name, condition):
    results.append((name, bool(condition)))
    print(("PASS" if condition else "FAIL"), "-", name)


import app
import config
from line_item import ExtractedLine

a = app.InvoiceImporterApp()
a.withdraw()

print("--- totals are computed from the table itself, not the printed invoice values ---")
a.lines = [
    ExtractedLine(raw_text="x", description="صنف 1", quantity=1, unit_price=100, total=100, ocr_confidence=100, matched_item_code="500"),
    ExtractedLine(raw_text="x", description="صنف 2", quantity=1, unit_price=50, total=50, ocr_confidence=100, matched_item_code=""),
]
# قيم مطبوعة بالفاتورة (لو وُجدت) تُهمَل عمداً بحساب الرقم الأساسي المعروض
a.invoice_subtotal_before_tax = 999.0
a.invoice_tax_amount = 1.0
a.invoice_grand_total_with_tax = None
a._populate_table()
label_text = a.grand_total_label["text"]
check("subtotal computed from line totals (150.00), not the printed 999", "150.00" in label_text and "999" not in label_text)
check(f"tax computed at config VAT_RATE ({config.VAT_RATE*100:.0f}%)", f"{150 * config.VAT_RATE:,.2f}" in label_text)
check("grand total = subtotal + computed tax", f"{150 * (1 + config.VAT_RATE):,.2f}" in label_text)

print("\n--- a real mismatch against the invoice-printed grand total surfaces a warning ---")
a.invoice_grand_total_with_tax = 999.0  # يختلف كثير عن المحسوب (172.5 تقريباً)
a._populate_table()
check("mismatch warning shown when computed vs printed diverge significantly", "⚠" in a.grand_total_label["text"])

print("\n--- no warning when computed and printed totals agree ---")
a.invoice_grand_total_with_tax = 150 * (1 + config.VAT_RATE)
a._populate_table()
check("no mismatch warning when they match", "⚠" not in a.grand_total_label["text"])

print("\n--- alternating row shading: consecutive visible rows in the SAME panel get different tags ---")
a.lines = [
    ExtractedLine(raw_text="x", description=f"صنف {i}", quantity=1, unit_price=1, total=1, ocr_confidence=100, matched_item_code="")
    for i in range(4)
]
a._populate_table()
tags_seen = [a.tree_unmatched.item(str(i), "tags")[0] for i in range(4)]
check("4 consecutive unmatched rows alternate between the two shades", tags_seen == ["not_in_catalog", "not_in_catalog_alt", "not_in_catalog", "not_in_catalog_alt"])

a.destroy()

print("\n--- summary ---")
total = len(results)
passed = sum(1 for _, ok in results if ok)
print(f"{passed}/{total} checks passed")
if passed != total:
    sys.exit(1)
