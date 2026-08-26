import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, r"D:\scanar\invoice_importer")

results = []


def check(name, condition):
    results.append((name, bool(condition)))
    print(("PASS" if condition else "FAIL"), "-", name)


import app
from app import _BatchInvoice
from line_item import ExtractedLine

a = app.InvoiceImporterApp()
a.withdraw()
app.messagebox.askyesno = lambda title, msg: True  # يوافق دايماً بهذا الاختبار

print("--- removing the WRONG invoice out of a 3-file batch keeps the other two intact ---")
inv1 = _BatchInvoice(path=Path("inv1.pdf"), lines=[ExtractedLine(raw_text="x", description="أ", quantity=1, unit_price=1, total=1, ocr_confidence=100)])
inv2_wrong = _BatchInvoice(path=Path("inv2_غلط.pdf"), lines=[ExtractedLine(raw_text="x", description="ب", quantity=1, unit_price=1, total=1, ocr_confidence=100)])
inv3 = _BatchInvoice(path=Path("inv3.pdf"), lines=[ExtractedLine(raw_text="x", description="ج", quantity=1, unit_price=1, total=1, ocr_confidence=100)])
a.batch = [inv1, inv2_wrong, inv3]
a.batch_index = 1
a._show_batch_index_fresh(1)

a._remove_current_invoice_from_batch()
check("batch shrank from 3 to 2", len(a.batch) == 2)
check("the wrong invoice is gone", inv2_wrong not in a.batch)
check("the other two invoices survived, in order", [inv.path.name for inv in a.batch] == ["inv1.pdf", "inv3.pdf"])
check("view lands on the invoice that shifted into the removed slot", a.batch_index == 1 and a.lines[0].description == "ج")

print("\n--- removing the LAST invoice in a batch clamps the index instead of going out of range ---")
a.batch_index = 1
a._remove_current_invoice_from_batch()
check("batch has exactly 1 invoice left", len(a.batch) == 1)
check("index clamped back to 0 (no IndexError)", a.batch_index == 0)

print("\n--- removing the only remaining invoice resets the whole UI to empty state ---")
a._remove_current_invoice_from_batch()
check("batch is now empty", a.batch == [])
check("lines cleared", a.lines == [])
check("invoice label reset to 'no file chosen'", a.invoice_label["text"] == "لم يتم اختيار ملف")

print("\n--- declining the confirmation dialog leaves the batch untouched ---")
inv_keep = _BatchInvoice(path=Path("keep_me.pdf"), lines=[])
a.batch = [inv_keep]
a.batch_index = 0
app.messagebox.askyesno = lambda title, msg: False
a._remove_current_invoice_from_batch()
check("declining the confirmation keeps the invoice in the batch", a.batch == [inv_keep])

a.destroy()

print("\n--- summary ---")
total = len(results)
passed = sum(1 for _, ok in results if ok)
print(f"{passed}/{total} checks passed")
if passed != total:
    sys.exit(1)
