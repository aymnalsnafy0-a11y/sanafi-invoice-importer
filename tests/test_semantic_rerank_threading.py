"""
اختبار: نداء إعادة الترتيب الدلالي (matching_engine.semantic_enhance_candidates)
من نافذة المراجعة المنبثقة (_open_review_dialog - تُستخدم الحين لكل من وضع
"أثناء القراءة" ووضع "بعد الاستخراج" بعد استبدال اللوحة الصغيرة القديمة
بنافذة منبثقة موحّدة، راجع test_after_extraction_review_panel.py للتفصيل)
يشتغل فعلياً بخيط خلفية منفصل - مو الخيط الرئيسي/Tk - وما يجمّد الواجهة
أثناء انتظاره. نموّه semantic_enhance_candidates بتأخير مصطنع (يحاكي بطء
شبكة حقيقي) ونتأكد mainloop يقدر يكمل يعالج أحداث أثناء هذا التأخير - صفر
اتصال إنترنت حقيقي أو تكلفة فعلية بهذا الاختبار.
"""

import io
import sys
import tempfile
import threading
import time
import tkinter as tk
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.stdout.reconfigure(line_buffering=True)
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

semantic_matcher.rerank = lambda *a, **k: None  # احتياط إضافي - غير مستخدم مباشرة هنا لكن نفس عادة بقية الاختبارات
app.InvoiceImporterApp._refresh_items_from_db = lambda self: None  # راجع الملاحظة بـtest_after_extraction_review_panel.py
from items import ReferenceItem
from line_item import ExtractedLine

a = app.InvoiceImporterApp()
a.withdraw()

reference = [
    ReferenceItem(code="500", name="حليب نادك كامل الدسم 1 لتر", barcode="1111", default_unit="كرتون", internal_id="9", unit_id="2"),
]
a.last_reference = reference
a.reference_attrs_index = matching_engine.build_reference_attrs_index(reference)
a.invoice_supplier_name = "مورد الاختبار"

_AI_DELAY = 0.3  # تأخير مصطنع يحاكي استدعاء شبكي بطيء - يكفي لملاحظة تجميد لو صار فعلاً


def make_recording_enhancer(call_info, local_candidates):
    def fake_enhance(line, reference_arg, supplier_name=None, reference_attrs_index=None, top_n=5):
        call_info["thread"] = threading.current_thread()
        call_info["is_main_thread"] = threading.current_thread() is threading.main_thread()
        call_info["called"] = True
        time.sleep(_AI_DELAY)
        return local_candidates[:top_n], False

    return fake_enhance


def find_all(widget):
    out = [widget]
    for child in widget.winfo_children():
        out.extend(find_all(child))
    return out


def close_via_skip():
    toplevels = [w for w in a.winfo_children() if isinstance(w, tk.Toplevel)]
    if not toplevels:
        a.after(30, close_via_skip)
        return
    buttons = [w for w in find_all(toplevels[-1]) if isinstance(w, tk.Button)]
    skip_btn = next((b for b in buttons if b["text"] == "تخطي"), None)
    if skip_btn is None:
        a.after(30, close_via_skip)
        return
    skip_btn.invoke()


print("--- 1) الضغط على صف بجدول 'أصناف غير موجودة' (بعد الاستخراج): يفتح النافذة المنبثقة، AI بخيط خلفية بدون تجميد mainloop ---")
# نستدعي _on_row_selected مباشرة بحدث مموّه (widget=tree_unmatched) بدل
# event_generate - راجع الملاحظة التقنية أعلى test_after_extraction_review_panel.py:
# event_generate("<<TreeviewSelect>>") يسبب تجمّد حقيقي هنا (خاصية داخلية
# Tcl/Tk)، بينما النداء المباشر لنفس معالج الحدث يشتغل تمام دائماً - نفس
# الكود الحقيقي يُستدعى فعلياً، الفرق فقط بآلية التشغيل، مو بالسلوك المُختبَر.
line1 = ExtractedLine(raw_text="x", description="حليب سائل كامل الدسم عبوة كبيرة", quantity=1, unit_price=8, total=8, ocr_confidence=100, needs_review=True)
a.lines = [line1]
a._populate_table()

call_info1 = {"called": False}
local_candidates1 = matching_engine.suggest_candidates(line1, reference, reference_attrs_index=a.reference_attrs_index, top_n=3)
matching_engine.needs_semantic_rerank = lambda candidates: True  # نجبر مسار AI يشتغل بغض النظر عن ثقة المرشّحين الفعلية
matching_engine.semantic_enhance_candidates = make_recording_enhancer(call_info1, local_candidates1)

tick_count1 = {"n": 0}


def tick1():
    tick_count1["n"] += 1
    a.after(20, tick1)


class _FakeEvent:
    def __init__(self, widget):
        self.widget = widget


# selection_set() نفسها تُطلق <<TreeviewSelect>> تلقائياً (نفس خاصية
# event_generate الحقيقية بالضبط) - نفكّ الربط مؤقتاً عشان نتحكم بنداء واحد
# فقط (المباشر أدناه)، بدل نداءين متزامنين لنفس الحدث
a.tree_unmatched.unbind("<<TreeviewSelect>>")
a.tree_unmatched.selection_set("0")
a.after(20, tick1)
a.after(int((_AI_DELAY + 0.3) * 1000), close_via_skip)
a._on_row_selected(_FakeEvent(a.tree_unmatched))

check("طبقة AI استُدعيت فعلاً", call_info1.get("called") is True)
check("REAL SAFETY: الاستدعاء صار من خيط خلفية منفصل، مو الخيط الرئيسي/Tk", call_info1.get("is_main_thread") is False)
check("REAL SAFETY: mainloop استمر بمعالجة الأحداث أثناء تأخير AI (ما تجمّد) - tick تحرّك عدة مرات", tick_count1["n"] >= 3)


print("\n--- 2) _open_review_dialog (أثناء القراءة - نداء مباشر): نفس الشيء - AI بخيط خلفية بدون تجميد النافذة ---")
# نداء مباشر (نفس نمط test_during_reading_review.py المُثبَت) - wait_window()
# تشتغل تلقائياً فور استدعائها مباشرة من الخيط الرئيسي، بدون حاجة لـmainloop()
# منفصل هنا. ملاحظة تقنية مهمة (راجع test_after_extraction_review_panel.py):
# هذا القسم يجي *بعد* القسم الأول عمداً - خلط event_generate بنداء مباشر
# لاحق بنفس نسخة التطبيق يسبب تجمّد حقيقي؛ الترتيب هنا (mainloop حقيقي أولاً
# بالقسم 1، ثم نداء مباشر بالقسم 2) مُثبَت أنه آمن.
line2 = ExtractedLine(raw_text="x", description="حليب سائل كامل الدسم عبوة كبيرة", quantity=1, unit_price=8, total=8, ocr_confidence=100, needs_review=True)

call_info2 = {"called": False}
local_candidates2 = matching_engine.suggest_candidates(line2, reference, reference_attrs_index=a.reference_attrs_index, top_n=3)
matching_engine.semantic_enhance_candidates = make_recording_enhancer(call_info2, local_candidates2)

tick_count2 = {"n": 0}


def tick2():
    tick_count2["n"] += 1
    a.after(20, tick2)


a.after(20, tick2)
a.after(int((_AI_DELAY + 0.3) * 1000), close_via_skip)
action2 = a._open_review_dialog(0, line2)

check("النافذة (نداء مباشر) طبقة AI استُدعيت فعلاً", call_info2.get("called") is True)
check("REAL SAFETY: نفس الشيء بالنداء المباشر - الاستدعاء من خيط خلفية، مو الرئيسي", call_info2.get("is_main_thread") is False)
check("REAL SAFETY: mainloop استمر يعالج أحداث (tick تحرّك) أثناء AI بالنافذة", tick_count2["n"] >= 3)
check("النافذة أُغلقت بنجاح (لم تتجمّد للأبد)", action2 == "skip")

a.destroy()

print("\n--- summary ---")
total = len(results)
passed = sum(1 for _, ok in results if ok)
print(f"{passed}/{total} checks passed")
if passed != total:
    sys.exit(1)
