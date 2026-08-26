import io
import sys
import tkinter as tk
from tkinter import ttk

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, r"D:\scanar\invoice_importer")

results = []


def check(name, condition):
    results.append((name, bool(condition)))
    print(("PASS" if condition else "FAIL"), "-", name)


import app
from items import ReferenceItem
from line_item import ExtractedLine


class FakeEvent:
    def __init__(self, widget, x, y):
        self.widget = widget
        self.x = x
        self.y = y


def double_click_cell(tree, row_id, col_name):
    tree.update_idletasks()
    bbox = tree.bbox(row_id, col_name)
    cx = bbox[0] + bbox[2] // 2
    cy = bbox[1] + bbox[3] // 2
    a._on_cell_double_click(FakeEvent(tree, cx, cy))


a = app.InvoiceImporterApp()
a.withdraw()

reference = [
    ReferenceItem(code="500", name="حليب نادك كامل الدسم 1 لتر", barcode="1111", default_unit="كرتون", internal_id="9", unit_id="2"),
]
a.last_reference = reference
a.lines = [ExtractedLine(raw_text="x", description="صنف", quantity=1, unit_price=1, total=1, ocr_confidence=100, matched_item_code="")]
a._populate_table()


def find_all(widget):
    out = [widget]
    for child in widget.winfo_children():
        out.extend(find_all(child))
    return out


print("--- REAL BEHAVIOR CHANGE: double-clicking 'اسم الصنف' now opens the full search window directly ---")
before_toplevels = {id(w) for w in a.winfo_children() if isinstance(w, tk.Toplevel)}
double_click_cell(a.tree_unmatched, "0", "matched_name")
after_toplevels = [w for w in a.winfo_children() if isinstance(w, tk.Toplevel) and id(w) not in before_toplevels]
check("a search dialog (Toplevel) opened", len(after_toplevels) == 1)
dialog = after_toplevels[0]
check("the dialog has a real results grid with proper columns (matches AccSystem's look)", any(
    isinstance(w, ttk.Treeview) and tuple(w["columns"]) == ("code", "name", "barcode", "unit")
    for w in find_all(dialog)
))
check("no leftover inline edit Entry was created on the tree itself", not any(isinstance(w, tk.Entry) for w in a.tree_unmatched.winfo_children()))
dialog.destroy()

print("\n--- double-clicking 'رقم الصنف' also opens the search window (not a raw text edit) ---")
before_toplevels2 = {id(w) for w in a.winfo_children() if isinstance(w, tk.Toplevel)}
double_click_cell(a.tree_unmatched, "0", "code")
after_toplevels2 = [w for w in a.winfo_children() if isinstance(w, tk.Toplevel) and id(w) not in before_toplevels2]
check("a search dialog opened for the code column too", len(after_toplevels2) == 1)
after_toplevels2[0].destroy()

print("\n--- double-clicking a plain data column (qty) still opens a simple inline text editor, not the search window ---")
before_toplevels3 = {id(w) for w in a.winfo_children() if isinstance(w, tk.Toplevel)}
double_click_cell(a.tree_unmatched, "0", "qty")
after_toplevels3 = [w for w in a.winfo_children() if isinstance(w, tk.Toplevel) and id(w) not in before_toplevels3]
check("no search dialog for a plain numeric column", len(after_toplevels3) == 0)
entries = [w for w in a.tree_unmatched.winfo_children() if isinstance(w, tk.Entry)]
check("a plain inline entry was created instead for the numeric column", len(entries) == 1)
entries[0].destroy()

a.destroy()

print("\n--- summary ---")
total = len(results)
passed = sum(1 for _, ok in results if ok)
print(f"{passed}/{total} checks passed")
if passed != total:
    sys.exit(1)
