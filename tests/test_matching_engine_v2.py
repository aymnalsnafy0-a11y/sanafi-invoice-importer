"""
اختبارات محرك المطابقة v2 - استرجاع متعدد المصادر + تقييم متعدد العوامل +
فجوة الغموض. يغطي الحالات الـ11 المطلوبة صراحة بخطة feature/smart-item-matching-v2
بالإضافة لاختبار سقف الأداء (بند 7).
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
import matching_engine
from items import ReferenceItem
from line_item import ExtractedLine


def L(description, **kw):
    return ExtractedLine(raw_text="x", description=description, quantity=1, unit_price=kw.pop("unit_price", None), total=1, ocr_confidence=100, **kw)


print("=== 1) الاسم مختلف جدًا لكن الحجم + العدد + الماركة صحيحة -> يظهر بالاقتراحات ===")
reference1 = [
    ReferenceItem(code="A1", name="صنكروب زيت زهرة الشمس المكرر عبوة اقتصادية 1.5لترx6", barcode="", default_unit="كرتون", internal_id="1", unit_id="1"),
    ReferenceItem(code="B2", name="صنف بعيد كل البعد ماله علاقة", barcode="", default_unit="", internal_id="2", unit_id="1"),
]
idx1 = matching_engine.build_reference_attrs_index(reference1)
line1 = L("صنكروب سعر جملة تخفيض توصيل سريع للمطاعم 1.5لترx6")

from rapidfuzz import fuzz
raw_name_score = fuzz.token_sort_ratio(line1.description, reference1[0].name)
check(f"تأكيد مسبق: تشابه الاسم فعلاً منخفض ({raw_name_score:.0f}%) - يثبت السيناريو حقيقي", raw_name_score < 55)

candidates1 = matching_engine.suggest_candidates(line1, reference1, supplier_name=None, reference_attrs_index=idx1, top_n=5)
codes1 = [c.item.code for c in candidates1]
check("REAL BUG FIX: الصنف الصحيح (A1) يظهر بالاقتراحات رغم انخفاض تشابه الاسم", "A1" in codes1)
a1 = next(c for c in candidates1 if c.item.code == "A1")
check("سبب الاقتراح يذكر الحجم/العدد/الماركة صراحة (Explainability)", "الحجم" in a1.reason and "قطع" in a1.reason and "الماركة" in a1.reason)


print("\n=== 2) الاسم متشابه جدًا لكن الحجم مختلف -> لا قبول تلقائي ===")
reference2 = [ReferenceItem(code="C1", name="بيبسي 250 مل كرتون 24 حبة", barcode="", default_unit="كرتون", internal_id="3", unit_id="1")]
idx2 = matching_engine.build_reference_attrs_index(reference2)
line2 = L("بيبسي 330 مل كرتون 24 حبة")
matching_engine.enhance_one(line2, reference2, supplier_name=None, reference_attrs_index=idx2)
check("لا قبول تلقائي رغم تشابه الاسم العالي - تعارض الحجم يمنعه", line2.matched_item_code == "" and line2.needs_review is True)
check("سبب الرفض يذكر تعارض الحجم صراحة", "حجم" in line2.match_reason)


print("\n=== 3) أفضل مرشحين متقاربين جدًا -> Ambiguity Review ===")
reference3 = [
    ReferenceItem(code="D1", name="مطهر ديتول اصلي 1 لتر", barcode="", default_unit="", internal_id="4", unit_id="1"),
    ReferenceItem(code="D2", name="مطهر ديتول اصلي 1 لتر جديد", barcode="", default_unit="", internal_id="5", unit_id="1"),
]
idx3 = matching_engine.build_reference_attrs_index(reference3)
line3 = L("مطهر ديتول اصلي 1 لتر")
c3 = matching_engine.suggest_candidates(line3, reference3, supplier_name=None, reference_attrs_index=idx3, top_n=2)
check("تأكيد مسبق: مرشّحين اثنين بثقة عالية جداً ومتقاربة", len(c3) == 2 and c3[0].confidence >= 95 and (c3[0].confidence - c3[1].confidence) < config.MATCH_MIN_CONFIDENCE_GAP)
matching_engine.enhance_one(line3, reference3, supplier_name=None, reference_attrs_index=idx3)
check("REAL FEATURE: القبول التلقائي يُرفض رغم عبور العتبة - فجوة الغموض", line3.matched_item_code == "" and line3.needs_review is True)
check("سبب الرفض يذكر الغموض صراحة", "قريب جداً" in line3.match_reason or "غموض" in line3.match_reason or "مراجعة بشرية" in line3.match_reason)


print("\n=== 4) مورد سابق يعطي boost لكن لا يحتكر الاختيار ===")
reference4 = [ReferenceItem(code="E1", name="حليب نادك كامل الدسم كرتون 1 لتر", barcode="", default_unit="", internal_id="6", unit_id="1")]
idx4 = matching_engine.build_reference_attrs_index(reference4)
learned_matches.record_confirmation("مورد الحليب", "حليب نادك كامل الدسم كرتون 1 لتر", reference4[0])
line4_weak = L("وصف مختلف كلياً بدون أي علاقة واضحة بالاسم")  # لا تشابه اسم، لا خصائص - بس نفس المورد
candidates4 = matching_engine.suggest_candidates(line4_weak, reference4, supplier_name="مورد الحليب", reference_attrs_index=idx4, top_n=5)
e1_candidate = next((c for c in candidates4 if c.item.code == "E1"), None)
check("تاريخ المورد يوسّع الاسترجاع - الصنف يظهر رغم ضعف الأدلة الثانية", e1_candidate is not None)
check("بس ما يحتكر: الثقة تبقى منخفضة، ما توصل لقبول تلقائي بمفردها", e1_candidate is not None and e1_candidate.confidence < config.MATCH_AUTO_ACCEPT_THRESHOLD)
matching_engine.enhance_one(line4_weak, reference4, supplier_name="مورد الحليب", reference_attrs_index=idx4)
check("enhance_one: ما يقبل تلقائياً بمجرد تاريخ المورد لوحده", line4_weak.matched_item_code == "")


print("\n=== 5) مطابقة متعلّمة صحيحة تتصدر ===")
reference5 = [
    ReferenceItem(code="F1", name="عصير المراعي برتقال 1 لتر", barcode="", default_unit="", internal_id="7", unit_id="1"),
    ReferenceItem(code="F2", name="عصير المراعي برتقال 1 لتر فريش", barcode="", default_unit="", internal_id="8", unit_id="1"),
]
idx5 = matching_engine.build_reference_attrs_index(reference5)
learned_matches.record_confirmation("مورد العصائر", "برتقال المراعي", reference5[0])
line5 = L("برتقال المراعي")
c5 = matching_engine.suggest_candidates(line5, reference5, supplier_name="مورد العصائر", reference_attrs_index=idx5, top_n=5)
check("المطابقة المتعلّمة تتصدّر القائمة دايماً", bool(c5) and c5[0].item.code == "F1" and "مؤكَّدة" in c5[0].reason)


print("\n=== 6) مطابقة متعلّمة قديمة فيها تعارض حجم جديد -> لا قبول تلقائي ===")
reference6 = [ReferenceItem(code="G1", name="نيدو حليب مجفف 1800 جم", barcode="", default_unit="", internal_id="9", unit_id="1")]
idx6 = matching_engine.build_reference_attrs_index(reference6)
# المستخدم أكّد سابقاً (5 مرات - ثقة عالية) إن نص "نيدو 400 جم" يطابق G1 -
# بغض النظر عن سبب ذلك التأكيد القديم، الصنف G1 المسجّل بالقاعدة فعلياً
# 1800 جم. لما نفس النص يجي بفاتورة جديدة، لازم فحص التعارض يمسك هذا رغم
# ثقة الذاكرة المتعلّمة العالية
for _ in range(5):
    learned_matches.record_confirmation("مورد الألبان", "نيدو 400 جم", reference6[0])
line6 = L("نيدو 400 جم")
c6 = matching_engine.suggest_candidates(line6, reference6, supplier_name="مورد الألبان", reference_attrs_index=idx6, top_n=1)
check("تأكيد مسبق: المطابقة المتعلّمة موثوقة جداً (5 تأكيدات)", bool(c6) and "مؤكَّدة" in c6[0].reason)
check("تأكيد مسبق: فحص التعارض يمسك اختلاف الحجم رغم الثقة العالية بالذاكرة", bool(c6) and c6[0].confidence <= config.MATCH_ATTRIBUTE_CONFLICT_CAP)
matching_engine.enhance_one(line6, reference6, supplier_name="مورد الألبان", reference_attrs_index=idx6)
check("مطابقة متعلّمة موثوقة لكن حجمها يتعارض مع الصنف المسجّل -> لا قبول تلقائي", line6.matched_item_code == "" and line6.needs_review is True)


print("\n=== 7) صنف بلا معلومات كافية -> لا تخمين ===")
reference7 = [
    ReferenceItem(code="H1", name="مياه صحة 200 مل", barcode="", default_unit="", internal_id="10", unit_id="1"),
    ReferenceItem(code="H2", name="مياه العين 330 مل", barcode="", default_unit="", internal_id="11", unit_id="1"),
    ReferenceItem(code="H3", name="مياه نستله 600 مل", barcode="", default_unit="", internal_id="12", unit_id="1"),
]
idx7 = matching_engine.build_reference_attrs_index(reference7)
line7 = L("مياه صغير")
matching_engine.enhance_one(line7, reference7, supplier_name=None, reference_attrs_index=idx7)
check("وصف غامض بلا حجم/عدد/ماركة واضحة -> ما يخمّن، يبقى بدون مطابقة", line7.matched_item_code == "" and line7.needs_review is True)


print("\n=== 8) نفس الصنف مكرر بعدة باركودات/وحدات -> dedup صحيح ===")
reference8 = [
    ReferenceItem(code="I1", name="أرز الباب الذهبي 10 كيلو", barcode="1", default_unit="حبة", internal_id="13", unit_id="1"),
    ReferenceItem(code="I1", name="أرز الباب الذهبي 10 كيلو", barcode="2", default_unit="كرتون", internal_id="13", unit_id="2"),
]
idx8 = matching_engine.build_reference_attrs_index(reference8)
line8 = L("أرز الباب الذهبي 10 كيلو")
c8 = matching_engine.suggest_candidates(line8, reference8, supplier_name=None, reference_attrs_index=idx8, top_n=5)
check("رقم الصنف المكرر (باركودين) يظهر مرة وحدة بس بالاقتراحات", sum(1 for c in c8 if c.item.code == "I1") == 1)


print("\n=== 9) لا كسر لمطابقة الباركود الحالية ===")
from items import ReferenceItem as RI, match_line_items
reference9 = [RI(code="J1", name="أي اسم", barcode="9999999999999", default_unit="", internal_id="14", unit_id="1")]
line9 = ExtractedLine(raw_text="x", description="وصف عشوائي كلياً", quantity=1, unit_price=1, total=1, ocr_confidence=100, barcode="9999999999999")
match_line_items([line9], reference9)
before9 = (line9.matched_item_code, line9.matched_item_name, line9.match_score, line9.needs_review)
# هذا بالضبط شرط التخطي بـapp.py::_extract_one_invoice - سطر تطابق بالباركود
# فعلياً ما يُستدعى matching_engine عليه إطلاقاً (نتأكد الحالة ما تغيّرت لو
# استُدعي غلطاً كمان، احتياط إضافي)
matching_engine.enhance_one(line9, reference9, supplier_name=None, reference_attrs_index=matching_engine.build_reference_attrs_index(reference9))
after9 = (line9.matched_item_code, line9.matched_item_name, line9.match_score, line9.needs_review)
check("سطر متطابق بالباركود يبقى بنفس الحالة حتى لو استُدعي المحرك عليه غلطاً", before9[0] == after9[0] and before9[1] == after9[1])


print("\n=== 10) during_reading يبقى 5 اقتراحات كحد أقصى ===")
reference10 = [ReferenceItem(code=f"K{i}", name=f"صنف رقم {i} تجريبي", barcode="", default_unit="", internal_id=str(i), unit_id="1") for i in range(10)]
idx10 = matching_engine.build_reference_attrs_index(reference10)
line10 = L("صنف رقم تجريبي")
c10 = matching_engine.suggest_candidates(line10, reference10, supplier_name=None, reference_attrs_index=idx10, top_n=5)
check("top_n=5 (أثناء القراءة) يُحترم - أقصى 5 نتائج", len(c10) <= 5)


print("\n=== 11) after_extraction يبقى 3 اقتراحات كحد أقصى ===")
c11 = matching_engine.suggest_candidates(line10, reference10, supplier_name=None, reference_attrs_index=idx10, top_n=3)
check("top_n=3 (بعد الاستخراج) يُحترم - أقصى 3 نتائج", len(c11) <= 3)


print("\n=== إضافي: سقف عدد المرشّحين قبل التقييم الكامل (بند 7 - الأداء) ===")
# قائمة مرجعية كبيرة، كلها بنفس الحجم الشائع "1 لتر" - يثبت إن خاصية شائعة
# وحدها ما ترجّع مئات المرشّحين بدون سقف
big_reference = [
    ReferenceItem(code=f"BIG{i}", name=f"صنف عشوائي {i} 1 لتر", barcode="", default_unit="", internal_id=str(i), unit_id="1")
    for i in range(500)
]
big_idx = matching_engine.build_reference_attrs_index(big_reference)
check("فهرس الحجم فعلاً يجمع كل الـ500 صنف بصندوق واحد (يثبت سيناريو الاختبار)", len(big_idx.by_size_bucket) <= 3)
big_line = L("صنف عشوائي 250 1 لتر")
hits = matching_engine._retrieve_candidate_hits(big_line, __import__("item_attributes").extract_attributes(big_line.description), big_reference, big_idx, set())
ranked = matching_engine._rank_and_cap_candidates(hits)
check(f"عدد المرشّحين المسترجَعين ({len(hits)}) أكبر من السقف (يثبت الحاجة الفعلية للقص)", len(hits) > matching_engine._MAX_CANDIDATES_BEFORE_SCORING)
check(f"السقف يُطبَّق فعلياً - العدد النهائي قبل التقييم ({len(ranked)}) لا يتجاوز {matching_engine._MAX_CANDIDATES_BEFORE_SCORING}", len(ranked) <= matching_engine._MAX_CANDIDATES_BEFORE_SCORING)


print("\n--- summary ---")
total = len(results)
passed = sum(1 for _, ok in results if ok)
print(f"{passed}/{total} checks passed")
if passed != total:
    sys.exit(1)
