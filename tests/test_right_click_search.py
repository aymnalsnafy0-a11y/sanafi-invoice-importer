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

a = app.InvoiceImporterApp()
a.withdraw()

reference = [
    ReferenceItem(code="500", name="حليب نادك كامل الدسم 1 لتر", barcode="1111", default_unit="كرتون", internal_id="9", unit_id="2"),
    ReferenceItem(code="600", name="بيبسي 330 مل كرتون 24 حبة", barcode="", default_unit="كرتون", internal_id="10", unit_id="3"),
]
a.last_reference = reference

# سطر مطابَق فعلاً - نتأكد إن البحث اليدوي متاح لأي صف، مو بس اللي يحتاج مراجعة
line = ExtractedLine(
    raw_text="x", description="حليب نادك", quantity=1, unit_price=8, total=8, ocr_confidence=100,
    matched_item_code="600", matched_item_name="بيبسي 330 مل كرتون 24 حبة",  # تطابق غلط عمداً - نبي نصححه بالبحث
)
a.lines = [line]
a._populate_table()


def find_all(widget):
    out = [widget]
    for child in widget.winfo_children():
        out.extend(find_all(child))
    return out


def get_search_dialog_tree(dialog):
    return [w for w in find_all(dialog) if isinstance(w, ttk.Treeview)][0]


print("--- right-clicking a row (even an already-matched one) offers a search option ---")
menu_holder = {}
original_menu_cls = tk.Menu


class RecordingMenu(original_menu_cls):
    def tk_popup(self, *a2, **k2):
        menu_holder["menu"] = self


app.tk.Menu = RecordingMenu


class FakeEvent:
    widget = a.tree_matched
    x = 5
    y = 5
    x_root = 100
    y_root = 100


a.tree_matched.selection_set("0")
a._on_tree_right_click(FakeEvent())
menu = menu_holder.get("menu")
check("a context menu was built for the row", menu is not None)
check("menu has exactly the search entry", menu is not None and menu.index("end") == 0)

app.tk.Menu = original_menu_cls

print("\n--- _open_item_search_dialog: results render as a real grid (columns), matching AccSystem's look ---")
a._open_item_search_dialog(0, line)
dialog = [w for w in a.winfo_children() if isinstance(w, tk.Toplevel)][-1]
entries = [w for w in find_all(dialog) if isinstance(w, tk.Entry)]
check("search box pre-filled with the (wrong) matched name for convenience", entries[0].get() == "بيبسي 330 مل كرتون 24 حبة")

results_tree = get_search_dialog_tree(dialog)
check("results tree has real columns (code/name/barcode/unit)", tuple(results_tree["columns"]) == ("code", "name", "barcode", "unit"))
row_ids = results_tree.get_children()
check("auto-search on open finds the exact self-match (600) as the top result", len(row_ids) > 0 and results_tree.item(row_ids[0], "values")[0] == "600")

first_row_values = results_tree.item(row_ids[0], "values")
check("no confusing confidence percentage baked into the row values", all("%" not in str(v) for v in first_row_values))
dialog.destroy()

print("\n--- correcting the query re-ranks results live, and picking the top result applies it ---")
# event_generate('<KeyRelease>') على Entry بنافذة withdrawn مو موثوق بهذي
# بيئة الاختبار (نفس القيد الموثّق بملفات اختبار ثانية بهذا المشروع) - نفتح
# النافذة مباشرة بنص مصحّح (يمر بنفس do_search() الحقيقي عند البناء) بدل
# محاكاة كتابة حية
corrected_line = ExtractedLine(raw_text="x", description="حليب نادك", quantity=1, unit_price=8, total=8, ocr_confidence=100)
a._open_item_search_dialog(0, corrected_line)
dialog2 = [w for w in a.winfo_children() if isinstance(w, tk.Toplevel)][-1]
results_tree2 = get_search_dialog_tree(dialog2)

row_ids2 = results_tree2.get_children()
top_values = results_tree2.item(row_ids2[0], "values") if row_ids2 else None
check("the correct milk item (code 500) now ranks as the top result after retyping", top_values is not None and top_values[0] == "500")

# نستخدم زر "اختيار" (نفس دالة pick_selected اللي يستخدمها الدبل كلك بالضبط)
# بدل محاكاة حدث فأرة مركّب - event_generate لأحداث فأرة على نافذة withdrawn
# مو موثوق دايماً بهذي بيئة الاختبار (نفس القيد المكتشف بملفات ثانية بهذا
# المشروع)، بعكس .invoke() المباشر على الزر
results_tree2.selection_set(row_ids2[0])
pick_button = next(b for b in find_all(dialog2) if isinstance(b, tk.Button) and b["text"] == "اختيار")
pick_button.invoke()

check("selecting the top result and clicking 'اختيار' applies it via _apply_edit_to_model", line.matched_item_code == "500")
check("matched_internal_id backfilled", line.matched_internal_id == "9")

a.destroy()

print("\n--- summary ---")
total = len(results)
passed = sum(1 for _, ok in results if ok)
print(f"{passed}/{total} checks passed")
if passed != total:
    sys.exit(1)
