import io
import sys
import tkinter as tk

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, r"D:\scanar\invoice_importer")

results = []


def check(name, condition):
    results.append((name, bool(condition)))
    print(("PASS" if condition else "FAIL"), "-", name)


import app
import matching_engine
from items import ReferenceItem
from line_item import ExtractedLine

a = app.InvoiceImporterApp()
a.withdraw()

reference = [
    ReferenceItem(code="500", name="حليب نادك كامل الدسم 1 لتر", barcode="6281100084013", default_unit="كرتون", internal_id="9", unit_id="2"),
]
a.last_reference = reference
a.reference_attrs_index = matching_engine.build_reference_attrs_index(reference)


def find_all(widget):
    out = [widget]
    for child in widget.winfo_children():
        out.extend(find_all(child))
    return out


print("--- REAL FEATURE ADDED: the after-extraction review panel now has its own manual search box ---")
line = ExtractedLine(raw_text="x", description="صنف غير معروف تماماً", quantity=1, unit_price=8, total=8, ocr_confidence=100, needs_review=True)
a.lines = [line]
a._populate_table()
a.tree_unmatched.selection_set("0")
a.tree_unmatched.event_generate("<<TreeviewSelect>>")
check("review panel has a search entry", isinstance(a.review_search_entry, tk.Entry))

print("\n--- searching by BARCODE inside the review panel finds the item and picking it applies + moves the row ---")
a.review_search_entry.delete(0, "end")
a.review_search_entry.insert(0, "6281100084013")
a._on_review_search_typed()
buttons = [w for w in find_all(a.review_search_results_frame) if isinstance(w, tk.Button)]
check("a result button appeared for the barcode search", len(buttons) >= 1)
labels = [w for w in find_all(a.review_search_results_frame) if isinstance(w, tk.Label)]
check("no confusing % shown in the review panel's search results", all("%" not in lbl["text"] for lbl in labels))

buttons[0].invoke()
check("picking the search result applied the match", line.matched_item_code == "500")
check("matched_internal_id backfilled", line.matched_internal_id == "9")
check("REAL BUG FIX: status bar now confirms the pick (used to look like nothing happened)", "500" in a.status_label["text"] and "✓" in a.status_label["text"])
check("panel closes after picking", a.review_panel.winfo_manager() != "pack")

print("\n--- reopening the panel for a different line clears the previous search box/results ---")
line2 = ExtractedLine(raw_text="x", description="سطر ثاني", quantity=1, unit_price=1, total=1, ocr_confidence=100, needs_review=True)
a.lines = [line2]
a._populate_table()
a.tree_unmatched.selection_set("0")
a.tree_unmatched.event_generate("<<TreeviewSelect>>")
check("search entry reset to empty for the new line", a.review_search_entry.get() == "")
check("stale search results from the previous line are gone", len(a.review_search_results_frame.winfo_children()) == 0)

a.destroy()

print("\n--- summary ---")
total = len(results)
passed = sum(1 for _, ok in results if ok)
print(f"{passed}/{total} checks passed")
if passed != total:
    sys.exit(1)
