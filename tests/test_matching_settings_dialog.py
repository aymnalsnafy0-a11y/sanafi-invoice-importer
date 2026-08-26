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


import settings as settings_module

tmp_dir = tempfile.mkdtemp()
settings_module._SETTINGS_FILE = Path(tmp_dir) / "matching_settings_test.json"

import app

a = app.InvoiceImporterApp()
a.withdraw()


def find_all(widget):
    out = [widget]
    for child in widget.winfo_children():
        out.extend(find_all(child))
    return out


def find_by_class(widget, cls):
    return [w for w in find_all(widget) if isinstance(w, cls)]


print("--- dialog opens pre-filled with current (default) settings ---")
a._open_matching_settings_window()
dialog = [w for w in a.winfo_children() if isinstance(w, tk.Toplevel)][-1]
entries = find_by_class(dialog, tk.Entry)
check("dialog has 2 threshold entries", len(entries) == 2)
check("auto-accept entry pre-filled with default (95)", entries[0].get() == "95")
check("needs-review entry pre-filled with default (70)", entries[1].get() == "70")
dialog.destroy()

print("\n--- changing values and clicking 'حفظ' actually persists via settings.py ---")
a._open_matching_settings_window()
dialog = [w for w in a.winfo_children() if isinstance(w, tk.Toplevel)][-1]
radios = find_by_class(dialog, tk.Radiobutton)
entries = find_by_class(dialog, tk.Entry)
checkbuttons = find_by_class(dialog, tk.Checkbutton)
buttons = find_by_class(dialog, tk.Button)

# اختر "بعد الاستخراج" (الراديو الثاني)، غيّر العتبتين، بدّل خانة التعلّم التلقائي
radios[1].invoke()
entries[0].delete(0, "end")
entries[0].insert(0, "90")
entries[1].delete(0, "end")
entries[1].insert(0, "60")
checkbuttons[0].invoke()  # كانت مفعّلة افتراضياً -> تصير معطّلة

save_button = next(b for b in buttons if b["text"] == "حفظ")
save_button.invoke()

saved = settings_module.get_settings()
check("review_mode persisted as after_extraction", saved["review_mode"] == "after_extraction")
check("auto_accept_threshold persisted as 90", saved["auto_accept_threshold"] == 90.0)
check("needs_review_threshold persisted as 60", saved["needs_review_threshold"] == 60.0)
check("auto_learn_from_manual_edits toggled off and persisted", saved["auto_learn_from_manual_edits"] is False)
check("dialog closed after successful save", not dialog.winfo_exists())

print("\n--- invalid input (needs_review > auto_accept) is rejected, nothing gets persisted ---")
a._open_matching_settings_window()
dialog = [w for w in a.winfo_children() if isinstance(w, tk.Toplevel)][-1]
entries = find_by_class(dialog, tk.Entry)
buttons = find_by_class(dialog, tk.Button)
entries[0].delete(0, "end")
entries[0].insert(0, "50")   # عتبة قبول تلقائي أقل من عتبة المراجعة - غير منطقي
entries[1].delete(0, "end")
entries[1].insert(0, "80")
save_button = next(b for b in buttons if b["text"] == "حفظ")
save_button.invoke()

saved2 = settings_module.get_settings()
check("invalid save was rejected - previous valid values still stand", saved2["auto_accept_threshold"] == 90.0)
check("dialog stays open on validation failure", dialog.winfo_exists())
dialog.destroy()

a.destroy()

print("\n--- summary ---")
total = len(results)
passed = sum(1 for _, ok in results if ok)
print(f"{passed}/{total} checks passed")
if passed != total:
    sys.exit(1)
