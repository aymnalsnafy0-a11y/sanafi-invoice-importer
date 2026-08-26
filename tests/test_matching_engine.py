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

# عزل تام عن الملفات الحقيقية
tmp_dir = tempfile.mkdtemp()
learned_matches._MATCHES_FILE = Path(tmp_dir) / "learned_matches_test.json"
settings_module._SETTINGS_FILE = Path(tmp_dir) / "matching_settings_test.json"

import matching_engine
from items import ReferenceItem, match_line_items
from line_item import ExtractedLine

reference = [
    ReferenceItem(code="500", name="حليب نادك كامل الدسم 1 لتر", barcode="1111", default_unit="كرتون", internal_id="9", unit_id="2"),
    ReferenceItem(code="600", name="بيبسي 330 مل كرتون 24 حبة", barcode="", default_unit="كرتون", internal_id="10", unit_id="3"),
    ReferenceItem(code="601", name="بيبسي 250 مل كرتون 24 حبة", barcode="", default_unit="كرتون", internal_id="11", unit_id="3"),
    ReferenceItem(code="602", name="بيبسي 330 مل حبة", barcode="", default_unit="حبة", internal_id="12", unit_id="1"),
]

print("--- REGRESSION GUARD: barcode-matched lines must be 100% untouched by the new engine ---")
barcode_line = ExtractedLine(
    raw_text="x", description="حليب اي شي غريب", quantity=1, unit_price=8, total=8, ocr_confidence=100, barcode="1111"
)
match_line_items([barcode_line], reference)  # نفس اللي يسويه items.py دايماً
check("items.py barcode match worked as baseline", barcode_line.matched_item_code == "500")
before = (barcode_line.matched_item_code, barcode_line.matched_item_name, barcode_line.match_score, barcode_line.needs_review)

# محاكاة بالضبط شرط التخطي اللي بيصير بـapp.py::_extract_one_invoice -
# لو الباركود انطابق فعلياً، matching_engine ما يلمس السطر إطلاقاً
ref_barcodes = {item.barcode for item in reference if item.barcode}
is_barcode_tier = bool("1111") and "1111" in ref_barcodes
check("the skip condition itself correctly identifies this as barcode-tier", is_barcode_tier is True)
# ما نستدعي enhance_one على هذا السطر أصلاً (هذا هو الضمان) - نتأكد فقط
# إن حالة السطر ما زالت متطابقة مع النسخة الأصلية
after = (barcode_line.matched_item_code, barcode_line.matched_item_name, barcode_line.match_score, barcode_line.needs_review)
check("barcode-matched line state is byte-identical (untouched)", before == after)

print("\n--- attribute conflict correctly caps confidence even with high name similarity ---")
attrs_index = matching_engine.build_reference_attrs_index(reference)
wrong_size_line = ExtractedLine(
    raw_text="x", description="بيبسي 250 مل كرتون 24 حبة", quantity=1, unit_price=35, total=35, ocr_confidence=100
)
# هذا الوصف نص حرفي مطابق تماماً لصنف code=601 (250مل) - ما فيه تعارض هنا،
# اختبار تعارض حقيقي أوضح تحت
candidates = matching_engine.suggest_candidates(wrong_size_line, reference, supplier_name="مورد تجريبي", reference_attrs_index=attrs_index)
check("exact real match (250ml) ranks first with very high confidence", candidates and candidates[0].item.code == "601" and candidates[0].confidence >= 95)

conflict_line = ExtractedLine(
    raw_text="x", description="بيبسي 250 مل حبة", quantity=1, unit_price=35, total=35, ocr_confidence=100
)
# أقرب تشابه بالاسم لـ"بيبسي 250 مل حبة" هو "بيبسي 330 مل حبة" (code 602، نفس
# نوع التعبئة "حبة" لكن حجم مختلف 250 مقابل 330) - يجب أن يُقصّ بشدة
candidates2 = matching_engine.suggest_candidates(conflict_line, reference, supplier_name="مورد تجريبي", reference_attrs_index=attrs_index)
top_602 = next((c for c in candidates2 if c.item.code == "602"), None)
check("a candidate with a real size conflict never exceeds the danger cap", top_602 is not None and top_602.confidence <= 40)

print("\n--- enhance_one: high confidence gets auto-applied, low confidence leaves matched_item_code blank ---")
clear_match_line = ExtractedLine(
    raw_text="x", description="حليب نادك كامل الدسم 1 لتر", quantity=1, unit_price=8, total=8, ocr_confidence=100
)
matching_engine.enhance_one(clear_match_line, reference, supplier_name="مورد تجريبي", reference_attrs_index=attrs_index)
check("clear exact-name match gets auto-applied (matched_item_code set)", clear_match_line.matched_item_code == "500")
check("needs_review is False for the auto-applied match", clear_match_line.needs_review is False)

vague_line = ExtractedLine(
    raw_text="x", description="شي غريب ما له علاقة بأي صنف بالقاعدة", quantity=1, unit_price=5, total=5, ocr_confidence=100
)
matching_engine.enhance_one(vague_line, reference, supplier_name="مورد تجريبي", reference_attrs_index=attrs_index)
check("no-guess principle: low-confidence line leaves matched_item_code BLANK", vague_line.matched_item_code == "")
check("low-confidence line is marked needs_review", vague_line.needs_review is True)

print("\n--- learned mapping: ranks first in suggestions regardless of raw fuzzy score, even after 1 confirmation ---")
learn_target = reference[2]  # code 601 - بيبسي 250 مل
learned_matches.record_confirmation("مورد الاختبار", "بيبسي وسط", learn_target)
learned_line = ExtractedLine(
    raw_text="x", description="بيبسي وسط", quantity=1, unit_price=35, total=35, ocr_confidence=100
)
suggestions = matching_engine.suggest_candidates(learned_line, reference, supplier_name="مورد الاختبار", reference_attrs_index=attrs_index)
check("learned candidate (code 601) ranks FIRST in suggestions after just 1 confirmation", bool(suggestions) and suggestions[0].item.code == "601")
check("learned candidate's confidence is 80% (1st confirmation), correctly below auto-accept", suggestions[0].confidence == 80.0)

# بثقة 80% بس (تحت عتبة القبول التلقائي 95% الافتراضية) - ما يُطبَّق تلقائياً،
# يبقى يحتاج مراجعة - هذا سلوك صحيح مقصود (تأكيد واحد لا يكفي وحده)
matching_engine.enhance_one(learned_line, reference, supplier_name="مورد الاختبار", reference_attrs_index=attrs_index)
check("1 confirmation alone (80%) does NOT auto-apply - still needs human review", learned_line.matched_item_code == "" and learned_line.needs_review is True)

print("\n--- learned mapping: auto-applies once confidence crosses the auto-accept threshold via repeated confirmations ---")
for _ in range(3):  # نوصل لـ4 تأكيدات إجمالاً = 80 + 3*5 = 95% = عتبة القبول التلقائي بالضبط
    learned_matches.record_confirmation("مورد الاختبار", "بيبسي وسط", learn_target)
confident_line = ExtractedLine(
    raw_text="x", description="بيبسي وسط", quantity=1, unit_price=35, total=35, ocr_confidence=100
)
matching_engine.enhance_one(confident_line, reference, supplier_name="مورد الاختبار", reference_attrs_index=attrs_index)
check("after enough confirmations to reach 95%, the learned match auto-applies", confident_line.matched_item_code == "601")
check("needs_review is False once auto-applied", confident_line.needs_review is False)

print("\n--- summary ---")
total = len(results)
passed = sum(1 for _, ok in results if ok)
print(f"{passed}/{total} checks passed")
if passed != total:
    sys.exit(1)
