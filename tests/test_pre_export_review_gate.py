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

print("--- no needs_review lines: gate passes silently, no dialog shown ---")
asked = []
app.messagebox.askyesno = lambda title, msg: asked.append((title, msg)) or True
clean_line = ExtractedLine(raw_text="x", description="صنف واضح", quantity=1, unit_price=1, total=1, ocr_confidence=100, matched_item_code="500", needs_review=False)
result = a._warn_if_needs_review_pending([clean_line])
check("gate returns True (proceed) with nothing pending", result is True)
check("no dialog was shown - nothing needed review", len(asked) == 0)

print("\n--- a pending needs_review line triggers the warning dialog ---")
asked.clear()
pending_line = ExtractedLine(raw_text="x", description="صنف مقترح بس ما تأكد", quantity=1, unit_price=1, total=1, ocr_confidence=100, needs_review=True)
result2 = a._warn_if_needs_review_pending([clean_line, pending_line])
check("dialog was shown for the pending line", len(asked) == 1)
check("dialog mentions the count (1)", "1" in asked[0][1])
check("user answering yes lets export proceed", result2 is True)

print("\n--- user declining the warning blocks export ---")
app.messagebox.askyesno = lambda title, msg: False
result3 = a._warn_if_needs_review_pending([pending_line])
check("gate returns False when the user says no", result3 is False)

print("\n--- a line explicitly confirmed 'not in catalog' does NOT trigger the gate ---")
asked.clear()
app.messagebox.askyesno = lambda title, msg: asked.append((title, msg)) or True
confirmed_missing_line = ExtractedLine(
    raw_text="x", description="صنف اتأكد إنه غير موجود", quantity=1, unit_price=1, total=1, ocr_confidence=100,
    needs_review=False, confirmed_not_in_catalog=True,
)
result4 = a._warn_if_needs_review_pending([confirmed_missing_line])
check("explicitly-confirmed-missing line does not count as pending review", len(asked) == 0 and result4 is True)

print("\n--- REAL WIRING: _export itself calls the gate and stops on decline ---")
export_calls = []
app.export_to_excel = lambda lines, path: export_calls.append((lines, path))
app.filedialog.asksaveasfilename = lambda **kw: r"C:\fake\out.xlsx"
app.messagebox.showwarning = lambda *a, **k: None
app.messagebox.askyesno = lambda title, msg: False  # يرفض المراجعة أول تنبيه يجيه
a.lines = [pending_line]
a._export()
check("_export aborted before even reaching the save dialog (gate blocked it)", len(export_calls) == 0)

print("\n--- REAL WIRING: _export_amn also calls the gate and stops on decline ---")
amn_calls = []
app.export_to_amn = lambda lines, path: amn_calls.append((lines, path))
matched_but_pending = ExtractedLine(
    raw_text="x", description="صنف", quantity=1, unit_price=1, total=1, ocr_confidence=100,
    matched_item_code="500", matched_internal_id="9", needs_review=True,
)
a.lines = [matched_but_pending]
a._export_amn()
check("_export_amn aborted before reaching the save dialog (gate blocked it)", len(amn_calls) == 0)

a.destroy()

print("\n--- summary ---")
total = len(results)
passed = sum(1 for _, ok in results if ok)
print(f"{passed}/{total} checks passed")
if passed != total:
    sys.exit(1)
