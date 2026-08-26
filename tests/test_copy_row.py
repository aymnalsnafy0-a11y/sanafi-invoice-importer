import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, r"D:\scanar\invoice_importer")

results = []


def check(name, condition):
    results.append((name, bool(condition)))
    print(("PASS" if condition else "FAIL"), "-", name)


import app
from line_item import ExtractedLine


class FakeEvent:
    def __init__(self, widget):
        self.widget = widget


a = app.InvoiceImporterApp()
a.withdraw()

a.lines = [
    ExtractedLine(raw_text="x", description="حليب نادك 1 لتر", quantity=2, unit_price=8.5, total=17.0, ocr_confidence=100, matched_item_code="500", matched_item_name="حليب نادك كامل الدسم 1 لتر", unit="كرتون", barcode="1111"),
]
a._populate_table()

print("--- REAL FEATURE ADDED: Ctrl+C on a selected row copies its data to the clipboard ---")
a.tree_matched.selection_set("0")
a._copy_selected_row(FakeEvent(a.tree_matched))
clipboard_text = a.clipboard_get()
check("clipboard contains the item code", "500" in clipboard_text)
check("clipboard contains the item name", "حليب نادك كامل الدسم 1 لتر" in clipboard_text)
check("clipboard contains the quantity", "2" in clipboard_text)
check("values are tab-separated (pastes cleanly into Excel as separate columns)", "\t" in clipboard_text)
check("status bar confirms the copy", "نسخ" in a.status_label["text"])

print("\n--- no selection: nothing happens, no crash ---")
a.tree_matched.selection_remove(*a.tree_matched.selection())
try:
    a._copy_selected_row(FakeEvent(a.tree_matched))
    check("no crash when nothing is selected", True)
except Exception as exc:
    check(f"no crash when nothing is selected (raised {exc})", False)

a.destroy()

print("\n--- summary ---")
total = len(results)
passed = sum(1 for _, ok in results if ok)
print(f"{passed}/{total} checks passed")
if passed != total:
    sys.exit(1)
