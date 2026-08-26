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

a = app.InvoiceImporterApp()
a.withdraw()

info_messages = []
app.messagebox.showinfo = lambda title, msg: info_messages.append(msg)
app.messagebox.showwarning = lambda title, msg: info_messages.append(msg)

print("--- picking a first file starts the batch ---")
app.filedialog.askopenfilenames = lambda **kw: (r"C:\fake\invoice1.pdf",)
a._pick_invoice()
check("batch has 1 invoice", len(a.batch) == 1)
check("batch_index points at the new invoice", a.batch_index == 0)

print("\n--- REAL BUG FIX: picking a second file APPENDS instead of replacing ---")
app.filedialog.askopenfilenames = lambda **kw: (r"C:\fake\invoice2.pdf",)
a._pick_invoice()
check("first invoice is still in the batch (not wiped out)", len(a.batch) == 2 and a.batch[0].path == Path(r"C:\fake\invoice1.pdf"))
check("second invoice was appended", a.batch[1].path == Path(r"C:\fake\invoice2.pdf"))
check("view jumped to the newly-added (2nd) invoice", a.batch_index == 1)

print("\n--- re-picking the SAME path again is silently ignored as a duplicate ---")
app.filedialog.askopenfilenames = lambda **kw: (r"c:\FAKE\Invoice1.PDF",)  # نفس المسار الأول، حالة أحرف مختلفة (ويندوز)
info_messages.clear()
a._pick_invoice()
check("duplicate path does not grow the batch", len(a.batch) == 2)
check("user is informed the file is already in the batch", len(info_messages) == 1)

print("\n--- mixed pick: one duplicate + one genuinely new file only adds the new one ---")
app.filedialog.askopenfilenames = lambda **kw: (r"C:\fake\invoice2.pdf", r"C:\fake\invoice3.pdf")
a._pick_invoice()
check("only the genuinely new file was added", len(a.batch) == 3)
check("third invoice is the new one", a.batch[2].path == Path(r"C:\fake\invoice3.pdf"))
check("view jumped to it", a.batch_index == 2)

print("\n--- REAL BUG FIX: clicking extract again with an already-extracted batch does not silently re-run/re-bill ---")
for inv in a.batch:
    inv.extracted = True
info_messages.clear()
a._run_extraction()
check("user is told everything is already extracted", len(info_messages) == 1)
check("extract button was never disabled (no background extraction started)", str(a.extract_button["state"]) == "normal")

a.destroy()

print("\n--- summary ---")
total = len(results)
passed = sum(1 for _, ok in results if ok)
print(f"{passed}/{total} checks passed")
if passed != total:
    sys.exit(1)
