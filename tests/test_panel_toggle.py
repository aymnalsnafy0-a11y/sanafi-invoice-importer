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

a = app.InvoiceImporterApp()
a.withdraw()

def is_shown(widget):
    # winfo_ismapped() يعتمد على ظهور النافذة فعلياً على الشاشة، وهذا الاختبار
    # يشغّل نافذة withdraw() (بدون واجهة) - نتحقق بدل كذا هل الودجت مُدار
    # فعلياً بـpack() أو لا (winfo_manager يرجّع "" لو pack_forget اتنادى عليه)
    return widget.winfo_manager() == "pack"


print("--- default panel on startup is 'unmatched' ---")
check("unmatched panel is mapped (visible) by default", is_shown(a._unmatched_section))
check("matched panel is NOT mapped by default", not is_shown(a._matched_section))

print("\n--- populating the table updates BOTH toggle-button counts, even the hidden panel's ---")
a.lines = [
    ExtractedLine(raw_text="x", description="صنف 1", quantity=1, unit_price=1, total=1, ocr_confidence=100, matched_item_code="500"),
    ExtractedLine(raw_text="x", description="صنف 2", quantity=1, unit_price=1, total=1, ocr_confidence=100, matched_item_code="501"),
    ExtractedLine(raw_text="x", description="صنف 3", quantity=1, unit_price=1, total=1, ocr_confidence=100, matched_item_code=""),
]
a._populate_table()
check("unmatched button shows count 1 (even while unmatched panel is the visible one)", "1" in a.show_unmatched_button["text"])
check("matched button shows count 2 while its panel is still hidden", "2" in a.show_matched_button["text"])

print("\n--- clicking the 'matched' toggle switches the visible panel ---")
a._show_panel("matched")
check("matched panel now visible", is_shown(a._matched_section))
check("unmatched panel now hidden", not is_shown(a._unmatched_section))
check("only one panel is ever visible at once", is_shown(a._matched_section) != is_shown(a._unmatched_section))

print("\n--- switching back to 'unmatched' works ---")
a._show_panel("unmatched")
check("unmatched panel visible again", is_shown(a._unmatched_section))
check("matched panel hidden again", not is_shown(a._matched_section))

a.destroy()

print("\n--- summary ---")
total = len(results)
passed = sum(1 for _, ok in results if ok)
print(f"{passed}/{total} checks passed")
if passed != total:
    sys.exit(1)
