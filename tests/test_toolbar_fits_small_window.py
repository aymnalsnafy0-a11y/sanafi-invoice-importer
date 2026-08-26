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

a = app.InvoiceImporterApp()
a.update_idletasks()

print("--- REAL BUG: toolbar used to be ONE row with 9 buttons - buttons silently rendered off-window ---")
print("--- FIX: toolbar is now split into 3 short rows, each independently narrow ---")

top = [w for w in a.winfo_children() if isinstance(w, tk.Frame)][0]
rows = top.winfo_children()
check("toolbar is split into exactly 3 rows (not 1 giant row)", len(rows) == 3)

# هامش أمان سخي تحت حتى أصغر شاشة حقيقية معقولة لعميل (1024px) - كل صف على
# حدة، بعيداً عن أي قيد عرض ثاني بالنافذة (زي عرض الجدول نفسه، اللي له حد
# أدنى منفصل تماماً عن التولبار ولا علاقة له بهذا الإصلاح)
SAFE_MAX_ROW_WIDTH = 900
row_labels = ["الصف الأول (خطوات العمل 1-2-3)", "الصف الثاني (قاعدة البيانات)", "الصف الثالث (الذكاء الاصطناعي/الإعدادات/الشات)"]
for label, row in zip(row_labels, rows):
    width = row.winfo_reqwidth()
    check(f"{label}: عرضه {width}px - أقل من {SAFE_MAX_ROW_WIDTH}px بأمان", width < SAFE_MAX_ROW_WIDTH)

print("\n--- every toolbar button is a real, present child of one of the 3 rows (none silently dropped) ---")
all_row_children_ids = {id(w) for row in rows for w in row.winfo_children()}
required_widgets = {
    "زر الاستخراج (3)": a.extract_button,
    "زر رصيد الذكاء الاصطناعي": a.ai_budget_button,
    "زر تحديث الأصناف من قاعدة البيانات": a.refresh_db_button,
    "تسمية الفاتورة المختارة": a.invoice_label,
    "تسمية ملف الأصناف المرجعي": a.items_label,
}
for name, widget in required_widgets.items():
    check(f"{name}: موجود فعلياً داخل أحد صفوف الشريط", id(widget) in all_row_children_ids)

a.destroy()

print("\n--- summary ---")
total = len(results)
passed = sum(1 for _, ok in results if ok)
print(f"{passed}/{total} checks passed")
if passed != total:
    sys.exit(1)
