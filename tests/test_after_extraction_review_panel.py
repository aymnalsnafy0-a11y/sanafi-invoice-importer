"""
وضع المراجعة "بعد الاستخراج": الضغط على صف "يحتاج مراجعة" بجدول "أصناف غير
موجودة" يفتح نافذة منبثقة واضحة (نفس نافذة وضع "أثناء القراءة" -
_open_review_dialog - بدون زر "إلغاء المراجعة المتبقية") بدل اللوحة الصغيرة
القديمة تحت الجدول - قرار صريح من المستخدم (2026-08-26): اللوحة القديمة
كانت "مو مفهومة تمام" وتحتاج تمرير/بحث لتلاحظها.

ملاحظة تقنية مهمة (تأكدنا منها فعلياً بعزل المشكلة خطوة بخطوة): استدعاء
event_generate("<<TreeviewSelect>>") على نفس نسخة التطبيق، وبعده لاحقاً نداء
مباشر آخر لـ_open_review_dialog (اللي يفتح Toplevel.wait_window() - حلقة
أحداث متداخلة)، يسبب تجمّد حقيقي - خاصية داخلية بـTcl/Tk عند خلط الحدثين،
مو خطأ بمنطقنا (النداء المباشر وحده يشتغل تمام دائماً، بالضبط نمط
test_during_reading_review.py المُثبَت). بالاستخدام الحقيقي هذا غير مشكلة
إطلاقاً (نقرة مستخدم حقيقية تولّد الحدث وwait_window() بنفس المكدس مباشرة -
تماماً كيف صُمم الكود، صفر نداءات متتابعة). لذلك هنا: كل نداءات
_open_review_dialog المباشرة (وحدة تعتمد على wait_window()) تجي أولاً على
نفس النسخة، واختبار "تحديد الصف يستدعي الدالة الصحيحة" (عبر event_generate)
يجي أخيراً بلا أي شي بعده.
"""

import io
import sys
import tempfile
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
# نافذة المراجعة (بخيط خلفية). نعطّل الاستدعاء الحقيقي بغض النظر عن حالة
# الجهاز الفعلية - ممنوع الاختبارات تحتاج إنترنت حقيقي أو تدفع تكلفة فعلية.
# نعطّل هنا كمان مسار خيط الذكاء الاصطناعي بالكامل (needs_semantic_rerank
# دايماً False) - هذا الملف يختبر سلوك نافذة المراجعة وأزرارها، مو خيوط
# الذكاء الاصطناعي (مُختبرة بتفصيل كافٍ بملف test_semantic_rerank_threading.py
# المنفصل).
semantic_matcher.rerank = lambda *a, **k: None
matching_engine.needs_semantic_rerank = lambda candidates: False
app.InvoiceImporterApp._refresh_items_from_db = lambda self: None
from items import ReferenceItem
from line_item import ExtractedLine

a = app.InvoiceImporterApp()
a.withdraw()

reference = [
    ReferenceItem(code="500", name="حليب نادك كامل الدسم 1 لتر", barcode="1111", default_unit="كرتون", internal_id="9", unit_id="2"),
    ReferenceItem(code="600", name="بيبسي 330 مل كرتون 24 حبة", barcode="", default_unit="كرتون", internal_id="10", unit_id="3"),
]
a.last_reference = reference
a.reference_attrs_index = matching_engine.build_reference_attrs_index(reference)
a.invoice_supplier_name = "مورد الاختبار"


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


def click_top(text):
    click_dialog_button(open_dialogs()[-1], text)


print("--- popup dialog behavior (نداء مباشر - نفس نمط test_during_reading_review.py المُثبَت) ---")

print("'تخطي' يترك السطر بدون أي تغيير")
line1 = ExtractedLine(raw_text="x", description="حليب نادك 1 لتر", quantity=1, unit_price=8, total=8, ocr_confidence=100, needs_review=True)
a.lines = [line1]
a._populate_table()
a.after(150, lambda: click_top("تخطي"))
action1 = a._open_review_dialog(0, line1, show_cancel_button=False)
check("action reported as 'skip'", action1 == "skip")
check("line still needs review (untouched)", line1.needs_review is True and line1.matched_item_code == "")
check("dialog closed itself", open_dialogs() == [])

print("\nزر 'إلغاء المراجعة المتبقية' غائب لما show_cancel_button=False (ما فيه طابور يُلغى بهذا الوضع)")
line2 = ExtractedLine(raw_text="x", description="حليب نادك 1 لتر", quantity=1, unit_price=8, total=8, ocr_confidence=100, needs_review=True)
a.lines = [line2]
a._populate_table()


def inspect_buttons_then_skip():
    dialog = open_dialogs()[-1]
    button_texts = {b["text"] for b in find_all(dialog) if isinstance(b, tk.Button)}
    check("'إلغاء المراجعة المتبقية' غائب", "إلغاء المراجعة المتبقية" not in button_texts)
    check("'تخطي' موجود", "تخطي" in button_texts)
    check("'اعتباره غير موجود بالقاعدة' موجود", "اعتباره غير موجود بالقاعدة" in button_texts)
    click_dialog_button(dialog, "تخطي")


a.after(150, inspect_buttons_then_skip)
a._open_review_dialog(0, line2, show_cancel_button=False)

print("\nاختيار مرشّح مقترح من النافذة يطبّقه عبر نفس مسار التعديل الآمن للتعديل اليدوي")
line3 = ExtractedLine(raw_text="x", description="حليب نادك 1 لتر", quantity=1, unit_price=8, total=8, ocr_confidence=100, needs_review=True)
a.lines = [line3]
a._populate_table()

candidate = matching_engine.suggest_candidates(
    line3, reference, supplier_name="مورد الاختبار", reference_attrs_index=a.reference_attrs_index, top_n=1
)[0]
check("تأكيد مسبق: أفضل مرشّح فعلاً هو صنف حليب نادك", candidate.item.code == "500")

a.after(150, lambda: click_top("اختيار"))
action3 = a._open_review_dialog(0, line3, show_cancel_button=False)
check("action reported as 'picked'", action3 == "picked")
check("matched_item_code applied to the line", line3.matched_item_code == "500")
check("matched_internal_id backfilled (needed for .Amn export)", line3.matched_internal_id == "9")
check("needs_review cleared after explicit human pick", line3.needs_review is False)

learned = learned_matches.lookup("مورد الاختبار", "حليب نادك 1 لتر")
check("picking a suggestion feeds the learning table (same as manual edit)", learned is not None and learned[0]["matched_item_code"] == "500")

print("\nتعليم السطر كـ'غير موجود بالقاعدة' من النافذة يضبط العلَم بدون تخمين كود")
line4 = ExtractedLine(raw_text="x", description="صنف غريب جداً", quantity=1, unit_price=1, total=1, ocr_confidence=100, needs_review=True)
a.lines = [line4]
a._populate_table()
a.after(150, lambda: click_top("اعتباره غير موجود بالقاعدة"))
action4 = a._open_review_dialog(0, line4, show_cancel_button=False)
check("action reported as 'not_in_catalog'", action4 == "not_in_catalog")
check("confirmed_not_in_catalog flag set", line4.confirmed_not_in_catalog is True)
check("needs_review cleared", line4.needs_review is False)
check("matched_item_code stays blank - no guessing", line4.matched_item_code == "")


print("\n--- REAL FEATURE (آخر قسم عمداً - راجع الملاحظة التقنية بالأعلى): تحديد صف 'يحتاج مراجعة' يستدعي نافذة الاختيار الصحيحة ---")
line_needs_review = ExtractedLine(
    raw_text="x", description="حليب نادك 1 لتر", quantity=1, unit_price=8, total=8, ocr_confidence=100, needs_review=True
)
line_resolved = ExtractedLine(
    raw_text="x", description="بيبسي 330 مل كرتون", quantity=1, unit_price=30, total=30, ocr_confidence=100,
    matched_item_code="600", matched_item_name="بيبسي 330 مل كرتون 24 حبة", needs_review=False,
)
a.lines = [line_needs_review, line_resolved]
a._populate_table()

calls = []
app.InvoiceImporterApp._open_review_dialog = lambda self, idx, line, show_cancel_button=True: calls.append((idx, line, show_cancel_button)) or "skip"

a.tree_unmatched.selection_set("0")
a.tree_unmatched.event_generate("<<TreeviewSelect>>")
check("selecting the needs-review row called _open_review_dialog exactly once", len(calls) == 1)
check("called with the right line index", calls and calls[0][0] == 0)
check("called with the right line object", calls and calls[0][1] is line_needs_review)
check("REAL FEATURE: called with show_cancel_button=False (no 'queue' to cancel for a single ad-hoc row)", calls and calls[0][2] is False)

calls.clear()
a.tree_matched.selection_set("1")
a.tree_matched.event_generate("<<TreeviewSelect>>")
check("selecting an already-resolved row (needs_review=False) does NOT open the dialog", len(calls) == 0)

a.destroy()

print("\n--- summary ---")
total = len(results)
passed = sum(1 for _, ok in results if ok)
print(f"{passed}/{total} checks passed")
if passed != total:
    sys.exit(1)
