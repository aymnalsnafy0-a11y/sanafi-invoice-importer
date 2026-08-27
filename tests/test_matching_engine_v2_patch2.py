"""
اختبارات patch v2 (جولة ثانية): (1) دمج دليل عادي قوي مع دليل ذاكرة أضعف
بدل استبداله، (2) حصص محجوزة لكل رتبة أولوية بالسقف - رتبة عالية الأولوية
فياضة العدد (تاريخ مورد) ما تُسقط مرشّح اسم قوي من رتبة أضعف.
"""

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

import config
import item_attributes
import matching_engine
import semantic_matcher

# اختبارات محرك محلي بحت - ممنوع تعتمد على اتصال إنترنت حقيقي أو تدفع
# تكلفة فعلية. نعطّل استدعاء AI الحقيقي بغض النظر عن حالة الجهاز الفعلية.
semantic_matcher.rerank = lambda *a, **k: None
from items import ReferenceItem
from line_item import ExtractedLine


def L(description, **kw):
    return ExtractedLine(
        raw_text="x", description=description, quantity=1, unit_price=kw.pop("unit_price", None), total=1,
        ocr_confidence=100, **kw,
    )


print("=== 1) دليل عادي قوي (>=95) + ذاكرة أضعف (تأكيد واحد=80) لنفس الصنف -> لا ينخفض لـ80 ===")
reference1 = [ReferenceItem(code="STRONG1", name="بيبسي 330 مل كرتون 24 حبة", barcode="", default_unit="كرتون", internal_id="1", unit_id="1")]
idx1 = matching_engine.build_reference_attrs_index(reference1)
learned_matches.record_confirmation("مورد المشروبات", "بيبسي 330 مل كرتون 24 حبة", reference1[0])  # تأكيد واحد بس = 80%
line1 = L("بيبسي 330 مل كرتون 24 حبة")  # تطابق شبه تام بالاسم + كل الخصائص - الدليل العادي لازم يكون >=95

# تأكيد مسبق: الدليل العادي فعلاً >=95 لوحده (بدون أي دور للذاكرة)
ordinary_only = matching_engine._score_all_candidates(line1, reference1, idx1, set())
check("تأكيد مسبق: الدليل العادي وحده (بدون ذاكرة) فعلاً >=95", bool(ordinary_only) and ordinary_only[0].confidence >= 95)

c1 = matching_engine.suggest_candidates(line1, reference1, supplier_name="مورد المشروبات", reference_attrs_index=idx1, top_n=1)
check("REAL BUG FIX: الثقة النهائية ما انخفضت لـ80% بسبب دمج تأكيد ذاكرة أضعف - بقيت >=95", bool(c1) and c1[0].confidence >= 95)
check("السبب يذكر الدليل العادي وكمان إشارة للتأكيد السابق", "مؤكَّدة" in c1[0].reason)

matching_engine.enhance_one(line1, reference1, supplier_name="مورد المشروبات", reference_attrs_index=idx1)
check("القبول التلقائي لسا ممكن (لا غموض ولا تعارض)", line1.matched_item_code == "STRONG1")

print("\n--- 1ب) تأكيد إضافي: لو الذاكرة أقوى (تعارض بالدليل العادي)، الذاكرة نفسها لسا محدودة بنفس سقف التعارض ---")
reference1b = [ReferenceItem(code="CONFLICT1", name="نيدو حليب مجفف 1800 جم", barcode="", default_unit="", internal_id="2", unit_id="1")]
idx1b = matching_engine.build_reference_attrs_index(reference1b)
for _ in range(5):
    learned_matches.record_confirmation("مورد الألبان", "نيدو 400 جم", reference1b[0])
line1b = L("نيدو 400 جم")  # تعارض حجم حقيقي (400 مقابل 1800) - الدليلين لازم ينزلوا لنفس السقف
c1b = matching_engine.suggest_candidates(line1b, reference1b, supplier_name="مورد الألبان", reference_attrs_index=idx1b, top_n=1)
check("حتى بعد الدمج، تعارض بنيوي حقيقي يبقى سقف فعلي - الدمج ما 'يفكّه'", bool(c1b) and c1b[0].confidence <= config.MATCH_ATTRIBUTE_CONFLICT_CAP)


print("\n=== 2) أكثر من 100 مرشّح تاريخ مورد + مرشّح اسم قوي واحد غير موجود بالتاريخ -> ما يسقط بسبب السقف ===")
supplier_history_refs = [
    ReferenceItem(code=f"HIST{i}", name=f"صنف تاريخي رقم {i}", barcode="", default_unit="", internal_id=str(i), unit_id="1")
    for i in range(110)
]
strong_name_ref = ReferenceItem(code="STRONGNAME", name="سنافي شاي احمر فاخر توينينغز نادر", barcode="", default_unit="", internal_id="999", unit_id="1")
reference2 = supplier_history_refs + [strong_name_ref]
idx2 = matching_engine.build_reference_attrs_index(reference2)

for ref in supplier_history_refs:
    learned_matches.record_confirmation("مورد كبير", f"نص قديم غير ذي صلة {ref.code}", ref)
supplier_codes2 = learned_matches.codes_confirmed_for_supplier("مورد كبير")
check(f"تأكيد مسبق: فعلاً أكثر من 100 صنف مؤكَّد سابقاً لنفس المورد ({len(supplier_codes2)})", len(supplier_codes2) > 100)

# مرشّح الاسم القوي غير موجود إطلاقاً بتاريخ المورد (صنف جديد لأول مرة)
line2 = L("سنافي شاي احمر فاخر توينينغز نادر")
line2_attrs = item_attributes.extract_attributes(line2.description)
hits2 = matching_engine._retrieve_candidate_hits(line2, line2_attrs, reference2, idx2, supplier_codes2)
size_of_supplier_hits = sum(1 for srcs in hits2.values() if "supplier_history" in srcs)
check(f"تأكيد مسبق: فعلاً أكثر من 100 مرشّح من مصدر تاريخ المورد ({size_of_supplier_hits})", size_of_supplier_hits > matching_engine._MAX_CANDIDATES_BEFORE_SCORING)

ranked2 = matching_engine._rank_and_cap_candidates(hits2, line2, reference2, line2_attrs, idx2)
ranked_codes2 = {reference2[i].code for i in ranked2}
check("REAL BUG FIX: مرشّح الاسم القوي ينجو من القصّ رغم فيضان تاريخ المورد فوق السقف", "STRONGNAME" in ranked_codes2)
check("السقف الأقصى العام (80) لسا محترم", len(ranked2) <= matching_engine._MAX_CANDIDATES_BEFORE_SCORING)

full2 = matching_engine.suggest_candidates(line2, reference2, supplier_name="مورد كبير", reference_attrs_index=idx2, top_n=5)
strongname_result = next((c for c in full2 if c.item.code == "STRONGNAME"), None)
check("يوصل فعلياً لنتيجة التقييم النهائي (مو بس الفهرسة الداخلية)", strongname_result is not None)
check("ثقته النهائية عالية (مطابقة اسم شبه تامة)", strongname_result is not None and strongname_result.confidence >= 90)


print("\n--- summary ---")
total = len(results)
passed = sum(1 for _, ok in results if ok)
print(f"{passed}/{total} checks passed")
if passed != total:
    sys.exit(1)
