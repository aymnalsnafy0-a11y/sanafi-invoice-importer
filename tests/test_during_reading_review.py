import io
import sys
import tempfile
import threading
import time
import tkinter as tk
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, r"D:\scanar\invoice_importer")

results = []


def check(name, condition):
    results.append((name, bool(condition)))
    print(("PASS" if condition else "FAIL"), "-", name)


import learned_matches
import settings as settings_module

tmp_dir = tempfile.mkdtemp()
learned_matches._MATCHES_FILE = Path(tmp_dir) / "learned_matches_test.json"
settings_module._SETTINGS_FILE = Path(tmp_dir) / "matching_settings_test.json"

import app
import matching_engine
import semantic_matcher

# اختبارات محلية بحتة - app.py الحين يستدعي طبقة إعادة الترتيب الدلالي من
# نوافذ المراجعة (بخيط خلفية). نعطّل الاستدعاء الحقيقي بغض النظر عن حالة
# الجهاز الفعلية (مفتاح API/ai_enabled) - ممنوع الاختبارات تحتاج إنترنت
# حقيقي أو تدفع تكلفة فعلية.
semantic_matcher.rerank = lambda *a, **k: None
from app import _BatchInvoice
from items import ReferenceItem
from line_item import ExtractedLine

a = app.InvoiceImporterApp()
a.withdraw()

reference = [
    ReferenceItem(code="500", name="حليب نادك كامل الدسم 1 لتر", barcode="1111", default_unit="كرتون", internal_id="9", unit_id="2"),
]
a.last_reference = reference
a.reference_attrs_index = matching_engine.build_reference_attrs_index(reference)


def find_all(widget):
    out = [widget]
    for child in widget.winfo_children():
        out.extend(find_all(child))
    return out


def click_top_dialog_button(text):
    dialog = [w for w in a.winfo_children() if isinstance(w, tk.Toplevel)][-1]
    buttons = [w for w in find_all(dialog) if isinstance(w, tk.Button)]
    btn = next(b for b in buttons if b["text"] == text)
    btn.invoke()


print("--- _open_review_dialog: real modal, 'تخطي' leaves the line unresolved ---")
line1 = ExtractedLine(raw_text="x", description="حليب نادك 1 لتر", quantity=1, unit_price=8, total=8, ocr_confidence=100, needs_review=True)
a.lines = [line1]
a.after(150, lambda: click_top_dialog_button("تخطي"))
action1 = a._open_review_dialog(0, line1)
check("action reported as 'skip'", action1 == "skip")
check("line still needs review (untouched)", line1.needs_review is True and line1.matched_item_code == "")

print("\n--- _open_review_dialog: picking the top suggestion applies it via _apply_edit_to_model ---")
line2 = ExtractedLine(raw_text="x", description="حليب نادك 1 لتر", quantity=1, unit_price=8, total=8, ocr_confidence=100, needs_review=True)
a.lines = [line2]
a.after(150, lambda: click_top_dialog_button("اختيار"))
action2 = a._open_review_dialog(0, line2)
check("action reported as 'picked'", action2 == "picked")
check("matched_item_code applied", line2.matched_item_code == "500")
check("matched_internal_id backfilled", line2.matched_internal_id == "9")
check("needs_review cleared", line2.needs_review is False)

print("\n--- _open_review_dialog: 'اعتباره غير موجود بالقاعدة' sets the flag without guessing ---")
line3 = ExtractedLine(raw_text="x", description="صنف غريب جداً", quantity=1, unit_price=1, total=1, ocr_confidence=100, needs_review=True)
a.lines = [line3]
a.after(150, lambda: click_top_dialog_button("اعتباره غير موجود بالقاعدة"))
action3 = a._open_review_dialog(0, line3)
check("action reported as 'not_in_catalog'", action3 == "not_in_catalog")
check("confirmed_not_in_catalog set", line3.confirmed_not_in_catalog is True)
check("matched_item_code stays blank", line3.matched_item_code == "")

print("\n--- ORCHESTRATION: _run_during_reading_review walks every needs_review line across the WHOLE batch ---")
# نحاكي _open_review_dialog (بدون نوافذ حقيقية - أسرع وحتمي) عشان نتأكد
# من منطق التنقّل/التخطي نفسه، مو من تصرف النافذة (مُختبر أعلاه فعلاً)
visited = []


def fake_open_review_dialog(idx, line):
    visited.append((a.batch_index, idx, line.description))
    return "skip"


a._open_review_dialog = fake_open_review_dialog

inv_ok1 = _BatchInvoice(path=Path("inv1.pdf"), lines=[
    ExtractedLine(raw_text="x", description="سطر أ", quantity=1, unit_price=1, total=1, ocr_confidence=100, needs_review=True),
    ExtractedLine(raw_text="x", description="سطر ب - مؤكد فعلاً", quantity=1, unit_price=1, total=1, ocr_confidence=100, needs_review=False, matched_item_code="500"),
])
inv_failed = _BatchInvoice(path=Path("inv2.pdf"), error="فشل قراءة الملف", lines=[
    ExtractedLine(raw_text="x", description="سطر بفاتورة فاشلة - يُتجاهل", quantity=1, unit_price=1, total=1, ocr_confidence=100, needs_review=True),
])
inv_ok2 = _BatchInvoice(path=Path("inv3.pdf"), lines=[
    ExtractedLine(raw_text="x", description="سطر ج بفاتورة ثانية", quantity=1, unit_price=1, total=1, ocr_confidence=100, needs_review=True),
])
a.batch = [inv_ok1, inv_failed, inv_ok2]
a.batch_index = 0


def run_review_under_real_mainloop(target_fn):
    """tkinter على بايثون 3.14 يرفض .after() من خيط ثاني إلا لو mainloop()
    شغّال فعلاً على الخيط الرئيسي (RuntimeError: main thread is not in main
    loop) - بعكس مجرد استدعاء update() بحلقة يدوية. نجدول بدء خيط الخلفية
    عبر after() (يضمن mainloop بدأ فعلاً قبل أول استدعاء .after() من الخيط
    الثاني)، والخيط نفسه يقفل mainloop لما يخلص أو بعد مهلة أمان."""
    worker_holder = {}

    def start_worker():
        def worker_body():
            target_fn()
            a.after(0, a.quit)

        t = threading.Thread(target=worker_body, daemon=True)
        worker_holder["thread"] = t
        t.start()

    a.after(50, start_worker)
    safety_id = a.after(10000, a.quit)  # احتياط لو صار تجمّد حقيقي - الاختبار يفشل بدل ما يعلّق للأبد
    a.mainloop()
    a.after_cancel(safety_id)
    return worker_holder.get("thread")


worker = run_review_under_real_mainloop(a._run_during_reading_review)
if worker is not None:
    worker.join(timeout=5)

check("worker thread finished (no deadlock/hang)", worker is not None and not worker.is_alive())
check("visited exactly the 2 needs_review lines from non-failed invoices", [v[2] for v in visited] == ["سطر أ", "سطر ج بفاتورة ثانية"])
check("view auto-switched to the invoice containing the 2nd needs_review line", visited[1][0] == 2)

print("\n--- ORCHESTRATION: returning 'cancel_batch' stops the loop immediately ---")
visited2 = []


def fake_open_review_dialog_cancel(idx, line):
    visited2.append(line.description)
    return "cancel_batch" if line.description == "سطر أ" else "skip"


a._open_review_dialog = fake_open_review_dialog_cancel
a.batch = [
    _BatchInvoice(path=Path("inv1.pdf"), lines=[
        ExtractedLine(raw_text="x", description="سطر أ", quantity=1, unit_price=1, total=1, ocr_confidence=100, needs_review=True),
        ExtractedLine(raw_text="x", description="سطر لازم ما يوصله - الإلغاء أوقف الحلقة قبله", quantity=1, unit_price=1, total=1, ocr_confidence=100, needs_review=True),
    ]),
]
a.batch_index = 0

worker2 = run_review_under_real_mainloop(a._run_during_reading_review)
if worker2 is not None:
    worker2.join(timeout=5)

check("worker thread finished after cancel", worker2 is not None and not worker2.is_alive())
check("loop stopped right after cancel_batch - never visited the next line", visited2 == ["سطر أ"])

a.destroy()

print("\n--- summary ---")
total = len(results)
passed = sum(1 for _, ok in results if ok)
print(f"{passed}/{total} checks passed")
if passed != total:
    sys.exit(1)
