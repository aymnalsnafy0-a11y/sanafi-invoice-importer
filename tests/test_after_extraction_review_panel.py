import io
import sys
import tempfile
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
# لوحة المراجعة (بخيط خلفية). نعطّل الاستدعاء الحقيقي بغض النظر عن حالة
# الجهاز الفعلية - ممنوع الاختبارات تحتاج إنترنت حقيقي أو تدفع تكلفة فعلية.
semantic_matcher.rerank = lambda *a, **k: None
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

line_needs_review = ExtractedLine(
    raw_text="x", description="حليب نادك 1 لتر", quantity=1, unit_price=8, total=8, ocr_confidence=100, needs_review=True
)
line_resolved = ExtractedLine(
    raw_text="x", description="بيبسي 330 مل كرتون", quantity=1, unit_price=30, total=30, ocr_confidence=100,
    matched_item_code="600", matched_item_name="بيبسي 330 مل كرتون 24 حبة", needs_review=False,
)
a.lines = [line_needs_review, line_resolved]
a._populate_table()

print("--- selecting a 'needs_review' row opens the suggestion panel ---")
a.tree_unmatched.selection_set("0")
a.tree_unmatched.event_generate("<<TreeviewSelect>>")
check("review panel is now visible", a.review_panel.winfo_manager() == "pack")
check("panel title mentions the line's description", "حليب نادك 1 لتر" in a.review_title_label["text"])
suggestion_buttons = [w for w in a.review_suggestions_frame.winfo_children()]
check("at least one suggestion row rendered", len(suggestion_buttons) >= 1)

print("\n--- selecting an already-resolved row (needs_review=False) hides the panel ---")
a.tree_matched.selection_set("1")
a.tree_matched.event_generate("<<TreeviewSelect>>")
check("panel hidden for a resolved row", a.review_panel.winfo_manager() != "pack")

print("\n--- picking a suggested candidate applies it via the SAME safe path as manual edit ---")
a.tree_unmatched.selection_set("0")
a.tree_unmatched.event_generate("<<TreeviewSelect>>")
candidate = matching_engine.suggest_candidates(
    line_needs_review, reference, supplier_name="مورد الاختبار", reference_attrs_index=a.reference_attrs_index, top_n=1
)[0]
check("sanity: top candidate is indeed the nadec milk item", candidate.item.code == "500")
a._pick_review_candidate(candidate)
check("matched_item_code applied to the line", line_needs_review.matched_item_code == "500")
check("matched_internal_id backfilled (needed for .Amn export)", line_needs_review.matched_internal_id == "9")
check("needs_review cleared after explicit human pick", line_needs_review.needs_review is False)
check("panel closed after picking", a.review_panel.winfo_manager() != "pack")

learned = learned_matches.lookup("مورد الاختبار", "حليب نادك 1 لتر")
check("picking a suggestion feeds the learning table (same as manual edit)", learned is not None and learned[0]["matched_item_code"] == "500")

print("\n--- marking a line as 'not in catalog' sets the flag without guessing a code ---")
line_unknown = ExtractedLine(
    raw_text="x", description="صنف غريب جداً", quantity=1, unit_price=1, total=1, ocr_confidence=100, needs_review=True
)
a.lines = [line_unknown]
a._populate_table()
a.tree_unmatched.selection_set("0")
a.tree_unmatched.event_generate("<<TreeviewSelect>>")
a._mark_selected_not_in_catalog()
check("confirmed_not_in_catalog flag set", line_unknown.confirmed_not_in_catalog is True)
check("needs_review cleared", line_unknown.needs_review is False)
check("matched_item_code stays blank - no guessing", line_unknown.matched_item_code == "")

a.destroy()

print("\n--- summary ---")
total = len(results)
passed = sum(1 for _, ok in results if ok)
print(f"{passed}/{total} checks passed")
if passed != total:
    sys.exit(1)
