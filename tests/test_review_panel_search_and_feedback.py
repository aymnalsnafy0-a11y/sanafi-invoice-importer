"""
البحث اليدوي داخل نافذة المراجعة المنبثقة (_open_review_dialog) - نفس آلية
البحث الموجودة أصلاً بوضع "أثناء القراءة"، تُستخدم الحين أيضاً من وضع "بعد
الاستخراج" بعد استبدال اللوحة الصغيرة القديمة تحت الجدول بنافذة منبثقة
(قرار صريح من المستخدم 2026-08-26 - راجع test_after_extraction_review_panel.py
للتفصيل والملاحظة التقنية عن ترتيب استدعاءات event_generate/wait_window).
"""

import io
import sys
import tkinter as tk

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, r"D:\scanar\invoice_importer")

results = []


def check(name, condition):
    results.append((name, bool(condition)))
    print(("PASS" if condition else "FAIL"), "-", name)


import learned_matches
import settings as settings_module

# REAL BUG FIX (اكتُشف 2026-08-26): هذا الملف كان يستخدم learned_matches.json
# الحقيقي بدون أي عزل - كل تشغيلة لهذا الاختبار كانت تكتب فعلياً بملف تعلّم
# المستخدم الحقيقي ("صنف غير معروف تماماً" ← الصنف 500، وصل لـ19 تأكيد وهمي
# بملفه الحقيقي عبر تكرار تشغيل مجموعة الاختبارات). صُحِّح بعزل الملف هنا
# (نفس نمط كل ملفات الاختبار الأخرى)، والإدخال الوهمي المتراكم بالملف
# الحقيقي حُذف يدوياً بعد اكتشافه.
import tempfile
from pathlib import Path

tmp_dir_learned = tempfile.mkdtemp()
learned_matches._MATCHES_FILE = Path(tmp_dir_learned) / "learned_matches_test.json"
settings_module._SETTINGS_FILE = Path(tmp_dir_learned) / "matching_settings_test.json"

import app
import matching_engine
import semantic_matcher

semantic_matcher.rerank = lambda *a, **k: None
matching_engine.needs_semantic_rerank = lambda candidates: False
app.InvoiceImporterApp._refresh_items_from_db = lambda self: None
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


def open_dialogs():
    return [w for w in a.winfo_children() if isinstance(w, tk.Toplevel)]


def click_dialog_button(dialog, text):
    buttons = [w for w in find_all(dialog) if isinstance(w, tk.Button)]
    btn = next(b for b in buttons if b["text"] == text)
    btn.invoke()


print("--- searching by BARCODE inside the review dialog finds the item and picking it applies + moves the row ---")
line = ExtractedLine(raw_text="x", description="صنف غير معروف تماماً", quantity=1, unit_price=8, total=8, ocr_confidence=100, needs_review=True)
a.lines = [line]
a._populate_table()


def search_and_pick():
    dialog = open_dialogs()[-1]
    entries = [w for w in find_all(dialog) if isinstance(w, tk.Entry)]
    check("dialog has exactly one search entry", len(entries) == 1)
    search_entry = entries[0]
    search_entry.insert(0, "6281100084013")
    click_dialog_button(dialog, "بحث")

    buttons = [w for w in find_all(dialog) if isinstance(w, tk.Button) and w["text"] == "اختيار"]
    check("REAL FEATURE: exactly one search result button appeared for the barcode search (no local suggestions for an unknown item)", len(buttons) == 1)
    labels = [w for w in find_all(dialog) if isinstance(w, tk.Label)]
    check("no confusing % shown for a plain manual search result", all("%" not in lbl["text"] for lbl in labels if "500" in lbl["text"]))

    buttons[0].invoke()


a.after(150, search_and_pick)
action = a._open_review_dialog(0, line, show_cancel_button=False)
check("picking the search result applied the match", line.matched_item_code == "500")
check("matched_internal_id backfilled", line.matched_internal_id == "9")
check("action reported as 'picked'", action == "picked")
check("REAL BUG FIX: status bar confirms the pick", "500" in a.status_label["text"] and "✓" in a.status_label["text"])
check("dialog closed after picking", open_dialogs() == [])


print("\n--- reopening the dialog for a different line starts with a fresh, empty search box ---")
line2 = ExtractedLine(raw_text="x", description="سطر ثاني", quantity=1, unit_price=1, total=1, ocr_confidence=100, needs_review=True)
a.lines = [line2]
a._populate_table()


def inspect_fresh_dialog_then_skip():
    dialog = open_dialogs()[-1]
    entries = [w for w in find_all(dialog) if isinstance(w, tk.Entry)]
    check("search entry starts empty for the new line", entries and entries[0].get() == "")
    result_buttons = [w for w in find_all(dialog) if isinstance(w, tk.Button) and w["text"] == "اختيار"]
    check("no stale search results carried over from the previous line", len(result_buttons) == 0)
    click_dialog_button(dialog, "تخطي")


a.after(150, inspect_fresh_dialog_then_skip)
a._open_review_dialog(0, line2, show_cancel_button=False)

a.destroy()

print("\n--- summary ---")
total = len(results)
passed = sum(1 for _, ok in results if ok)
print(f"{passed}/{total} checks passed")
if passed != total:
    sys.exit(1)
