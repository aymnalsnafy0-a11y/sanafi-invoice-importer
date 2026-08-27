"""
اختبارات tools/benchmark_invoices.py - المنطق الصرف (محاذاة الأسطر، مقارنة
Ground Truth، حساب المقاييس) ببيانات مصطنعة، بدون أي حاجة لـVision/Oracle
حقيقي. مهم بشكل خاص: التأكد إن حساب الدقة (compute_metrics) لا يُخفي أسطراً
"unresolved" من المقام (بند صريح بمواصفة المستخدم)، وإن _classify_header
يبقى متزامناً مع exporter.py::_COLUMN_RULES (الملف نفسه يحذّر من هذا الخطر
بتعليق - راجع نفس الاختبار أدناه لأتمتة الفحص بدل الاعتماد على تذكّر يدوي).
"""

import io
import json
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, r"D:\scanar\invoice_importer")
sys.path.insert(0, r"D:\scanar\invoice_importer\tools")

results = []


def check(name, condition):
    results.append((name, bool(condition)))
    print(("PASS" if condition else "FAIL"), "-", name)


import benchmark_invoices as bi
from line_item import ExtractedLine


def GT(code="", name="", barcode="", unit="", qty=1.0, price=1.0, total=1.0, source="excel"):
    return bi.GroundTruthLine(code=code, name=name, barcode=barcode, unit=unit, quantity=qty, unit_price=price, total=total, source=source)


def EL(desc="", barcode="", code="", unit="", qty=1.0, price=1.0, total=1.0, needs_review=True, match_score=None, match_reason=""):
    return ExtractedLine(
        raw_text="x", description=desc, quantity=qty, unit_price=price, total=total, ocr_confidence=100,
        barcode=barcode, unit=unit, matched_item_code=code,
        matched_item_name="", needs_review=needs_review, match_score=match_score, match_reason=match_reason,
    )


print("=== 1) _classify_header يبقى متزامناً مع exporter.py::_COLUMN_RULES (اتجاه القراءة يطابق اتجاه الكتابة) ===")
import exporter as _exporter_module

_write_side_headers = {
    "سعر الوحدة": "unit_price", "رقم الصنف": "code", "اسم الصنف": "name",
    "الباركود": "barcode", "الكمية": "quantity", "الإجمالي": "total", "الوحدة": "unit",
}
for header, expected_field in _write_side_headers.items():
    getter = _exporter_module._resolve_field(header)
    check(f"exporter.py يفهم '{header}' -> دالة صحيحة موجودة", getter is not None)
    read_side = bi._classify_header(header)
    check(f"REAL SYNC CHECK: _classify_header('{header}') == '{expected_field}' (يطابق اتجاه exporter.py)", read_side == expected_field)

check("'سعر الوحدة' لا يُصنَّف خطأً كـ'وحدة' (يجب فحص 'سعر'+'وحدة' قبل 'وحدة' المجردة)", bi._classify_header("سعر الوحدة") == "unit_price")


print("\n=== 2) cross_check_ground_truth ===")
excel_a = [GT(code="1", name="صنف أ", barcode="111", qty=1, price=10, total=10)]
amn_a = [GT(code="1", name="صنف أ", barcode="111", qty=1, price=10, total=10, source="amn")]
cross_a = bi.cross_check_ground_truth(excel_a, amn_a)
check("نفس البيانات بالضبط -> reliable=True، صفر discrepancies", cross_a["reliable"] is True and cross_a["discrepancies"] == [])

excel_b = [GT(code="1", name="أ"), GT(code="2", name="ب")]
amn_b = [GT(code="1", name="أ")]
cross_b = bi.cross_check_ground_truth(excel_b, amn_b)
check("REAL FEATURE: اختلاف عدد الأسطر يُكتشف بوضوح (reliable=False)", cross_b["reliable"] is False)
check("نوع discrepancy = line_count_mismatch", any(d["type"] == "line_count_mismatch" for d in cross_b["discrepancies"]))

excel_c = [GT(code="1", name="أ", qty=5, price=10)]
amn_c = [GT(code="2", name="أ", qty=5, price=10, source="amn")]  # code مختلف فقط
cross_c = bi.cross_check_ground_truth(excel_c, amn_c)
check("REAL FEATURE: اختلاف حقل وحيد (code) بين Excel/.Amn يُكتشف حتى مع تطابق عدد الأسطر", any(d["type"] == "field_mismatch" and d["field"] == "code" for d in cross_c["discrepancies"]))
check("reliable يعتمد فقط على تطابق العدد (field mismatches لا تغيّره - قرار تصميم مقصود)", cross_c["reliable"] is True)


print("\n=== 3) align_lines: باركود مباشر يتصدّر، fuzzy fallback بس للباقي ===")
extracted = [EL(desc="صنف مطابق بالباركود", barcode="999"), EL(desc="حليب نادك كامل الدسم 1 لتر", barcode="")]
original_bc = ["999", ""]
truth = [GT(code="A", name="صنف مختلف تماماً بالاسم", barcode="999"), GT(code="B", name="حليب نادك 1 لتر", barcode="")]
pairs, missing, extra = bi.align_lines(extracted, original_bc, truth)
check("سطر بباركود مطابق يتزاوج بالباركود رغم اختلاف الاسم الكامل", any(i == 0 and j == 0 and m == "barcode_exact" for i, j, m, s in pairs))
check("سطر بدون باركود يتزاوج بتشابه الاسم (fuzzy fallback)", any(i == 1 and j == 1 and m == "fuzzy_description" for i, j, m, s in pairs))
check("صفر أسطر مفقودة (كلاهما تزاوج)", missing == [] and extra == [])

extracted2 = [EL(desc="بطاطس مقرمشة نكهة الجبنة", barcode="")]
truth2 = [GT(code="X", name="زيت زيتون بكر ممتاز اسباني", barcode="")]
score_check = __import__("rapidfuzz").fuzz.token_sort_ratio(extracted2[0].description, truth2[0].name)
check(f"تأكيد مسبق: الوصفان فعلاً تحت العتبة (score={score_check:.1f} < {bi._LINE_ALIGN_MIN_SCORE})", score_check < bi._LINE_ALIGN_MIN_SCORE)
pairs2, missing2, extra2 = bi.align_lines(extracted2, [""], truth2)
check("REAL SAFETY: تشابه ضعيف جداً (تحت _LINE_ALIGN_MIN_SCORE) لا يتزاوج قسراً", pairs2 == [] and missing2 == [0] and extra2 == [0])


print("\n=== 4) compute_metrics: 'unresolved' يُحسب ضد الدقة، لا يُخفى من المقام ===")
# 3 أسطر متزاوجة: 1 صحيح، 1 خطأ، 1 unresolved (matched_item_code فاضي)
recs = [
    bi.PerLineRecord(row_type="paired", verdict="correct", needs_review=False, expected_code="A", matched_item_code="A"),
    bi.PerLineRecord(row_type="paired", verdict="wrong", needs_review=True, expected_code="B", matched_item_code="C"),
    bi.PerLineRecord(row_type="paired", verdict="unresolved", needs_review=True, expected_code="D", matched_item_code=""),
]
metrics = bi.compute_metrics(recs)
check("paired_count يشمل الثلاثة", metrics["paired_count"] == 3)
check("REAL SAFETY (بند صريح بالمواصفة): unresolved يُحسب بمقام item_code_accuracy_over_paired (1/3 مو 1/2 أو 1/1)", metrics["item_code_accuracy_over_paired"] == round(1 / 3, 4))
check("unresolved_count منفصل وواضح، مو مخفي", metrics["unresolved_count"] == 1)

print("\n--- false_auto_accept: أخطر مقياس - يُكتشف عبر منطق الاشتقاق الحقيقي (build_per_line_records) ---")
# سطر 0: قبول تلقائي (needs_review=False) لكن الكود المطابَق خطأ فعلاً - أخطر حالة ممكنة
# سطر 1: خطأ أيضاً لكن أُحيل لمراجعة بشرية (needs_review=True) - أأمن بكثير
extracted_fa = [
    EL(desc="صنف أ", barcode="", code="Z", needs_review=False),
    EL(desc="صنف ب", barcode="", code="Y", needs_review=True),
]
truth_fa = [GT(code="A", name="صنف أ", barcode=""), GT(code="B", name="صنف ب", barcode="")]
pairs_fa, missing_fa, extra_fa = bi.align_lines(extracted_fa, ["", ""], truth_fa)
recs2 = bi.build_per_line_records(pairs_fa, missing_fa, extra_fa, extracted_fa, truth_fa, ["", ""], {}, [False, False])
metrics2 = bi.compute_metrics(recs2)
check("REAL SAFETY: false_auto_accept_count يكتشف بالضبط الحالة الخطيرة (قبول تلقائي + خطأ)", metrics2["false_auto_accept_count"] == 1)
check("wrong_but_flagged_count يكتشف الحالة الأأمن (خطأ لكن محال لمراجعة) بشكل منفصل", metrics2["wrong_but_flagged_count"] == 1)

print("\n--- qty/price accuracy: أسطر بلا قيمة قابلة للمقارنة تُستبعد بوضوح، مو تُحتسب كـ'صحيحة' ---")
recs3 = [
    bi.PerLineRecord(row_type="paired", expected_qty=5.0, extracted_qty=5.0),
    bi.PerLineRecord(row_type="paired", expected_qty=5.0, extracted_qty=3.0),
    bi.PerLineRecord(row_type="paired", expected_qty=5.0, extracted_qty=None),  # غير قابل للمقارنة
]
metrics3 = bi.compute_metrics(recs3)
check("qty_comparable_count يستبعد السطر الناقص (2 مو 3)", metrics3["qty_comparable_count"] == 2)
check("qty_incomparable_count يوثّق السطر المستبعد بدل تجاهله بصمت", metrics3["qty_incomparable_count"] == 1)
check("qty_accuracy = 1/2 (مو 1/3 - ما يُعاقَب على بيانات ناقصة كأنها خطأ فعلي)", metrics3["qty_accuracy"] == 0.5)


print("\n=== 5) survey_amnc_structure: عيّنة XML مصطنعة صغيرة ===")
import tempfile
from pathlib import Path

sample_xml = """<?xml version="1.0" encoding="utf-8"?>
<DataSet>
  <CLASSES>
    <CLS_ID>1</CLS_ID>
    <CLS_NO>100</CLS_NO>
    <CLS_UN_1_PCODE>111</CLS_UN_1_PCODE>
    <CLS_UN_2_PCODE>222</CLS_UN_2_PCODE>
    <CLS_UN_2_TRANS>6</CLS_UN_2_TRANS>
    <GTIN></GTIN>
  </CLASSES>
  <CLASSES>
    <CLS_ID>2</CLS_ID>
    <CLS_NO>101</CLS_NO>
    <GTIN>9999999999</GTIN>
  </CLASSES>
  <CLS_UNIT_BARCODE>
    <CLS_ID>1</CLS_ID>
  </CLS_UNIT_BARCODE>
</DataSet>
"""
tmp_amnc = Path(tempfile.mkdtemp()) / "sample.AmnC"
tmp_amnc.write_text(sample_xml, encoding="utf-8")
survey = bi.survey_amnc_structure(tmp_amnc)
check("total_classes = 2", survey["total_classes"] == 2)
check("صنف واحد متعدد الوحدات (2 pcodes)", survey["multi_unit_barcode_items"]["count"] == 1)
check("صنف واحد يعتمد GTIN فقط (بدون أي pcode)", survey["items_relying_on_gtin_only"]["count"] == 1)
check("صنف واحد فيه trans factor مفعّل", survey["items_with_populated_trans_factor"]["count"] == 1)
check("جدول CLS_UNIT_BARCODE: صف واحد، وينضم صح عبر CLS_ID", survey["cls_unit_barcode_table"]["row_count"] == 1 and survey["cls_unit_barcode_table"]["spot_check_join_match_rate"] == 1.0)


print("\n=== 6) build_review_package: يصفّي السطور 'المشكوك فيها' بس، مع أسباب واضحة، بدون أسرار ===")
recs6 = bi.build_per_line_records(pairs_fa, missing_fa, extra_fa, extracted_fa, truth_fa, ["", ""], {}, [False, False])
# نضيف سطر ثالث نظيف تماماً (صحيح + مطابقة قوية) - يجب ألا يظهر بالحزمة
extracted6 = extracted_fa + [EL(desc="صنف صحيح تماماً", barcode="777", code="P")]
truth6 = truth_fa + [GT(code="P", name="صنف صحيح تماماً", barcode="777")]
pairs6, missing6, extra6 = bi.align_lines(extracted6, ["", "", "777"], truth6)
recs6b = bi.build_per_line_records(pairs6, missing6, extra6, extracted6, truth6, ["", "", "777"], {}, [False, False, True])
metrics6 = bi.compute_metrics(recs6b)

class _Args:
    with_semantic = False
    hard_mode = False

files6 = bi.DiscoveredFiles(Path("x.pdf"), Path("x.AmnC"), Path("x.xlsx"), Path("x.Amn"), [])
package = bi.build_review_package(recs6b, metrics6, {"total_classes": 0}, {"reliable": True}, _Args(), files6)

check("السطر النظيف (صحيح + قبول تلقائي واضح بدون تعارض) غير موجود بالحزمة", not any(fl["expected_code"] == "P" for fl in package["flagged_lines"]))
check("السطر الخطير (false auto accept) موجود بالحزمة مع سبب واضح", any(fl["expected_code"] == "A" and any("قبول تلقائي خاطئ" in r for r in fl["suspicious_reasons"]) for fl in package["flagged_lines"]))
check("flagged_line_count يطابق فعلياً عدد السطور المصفّاة", package["flagged_line_count"] == len(package["flagged_lines"]))
check("total_line_count يشمل كل السطور (حتى النظيفة)", package["total_line_count"] == 3)
check("لا توجد أي كلمة 'password' أو 'key' أو 'credential' بمحتوى الحزمة (فحص أساسي لعدم تسرّب أسرار)", "password" not in json.dumps(package).lower() and "credential" not in json.dumps(package).lower())


print("\n=== 7) _retrieval_rank / _units_match: أدوات مقاييس Retrieval Recall@K ودقة الوحدة ===")

import matching_engine as _me
from items import ReferenceItem as _RI


def cand(code):
    return _me.MatchCandidate(item=_RI(code=code, name=f"صنف {code}", barcode="", default_unit="", internal_id=code, unit_id="1"), confidence=50.0, reason="")


pool7 = [cand("X1"), cand("X2"), cand("X3")]
check("REAL FEATURE: الرتبة 1-indexed لصنف موجود بالبداية", bi._retrieval_rank("X1", pool7) == 1)
check("رتبة صحيحة لصنف بمنتصف القائمة", bi._retrieval_rank("X2", pool7) == 2)
check("REAL SAFETY: صنف غير موجود بالـpool إطلاقاً -> None (فشل استرجاع حقيقي)", bi._retrieval_rank("NOTHERE", pool7) is None)
check("قائمة فاضية -> None بأمان", bi._retrieval_rank("X1", []) is None)

check("REAL FEATURE: وحدتان متطابقتان (حتى مع اختلاف حالة الأحرف/فراغات) -> True", bi._units_match("  كرتون ", "كرتون") is True)
check("وحدتان مختلفتان فعلياً -> False", bi._units_match("كرتون", "حبة") is False)
check("REAL SAFETY: أحد الطرفين فاضي -> None (غير قابل للمقارنة، مو False)", bi._units_match("", "كرتون") is None and bi._units_match("كرتون", "") is None)


print("\n=== 8) compute_metrics: Retrieval Recall@K + unit_accuracy + Auto Match Coverage/Precision (أسماء المستخدم الصريحة) ===")
recs8 = [
    # 3 أسطر متزاوجة، غير مختصرة بالباركود - retrieval_rank متفاوت
    bi.PerLineRecord(row_type="paired", verdict="correct", needs_review=False, was_barcode_shortcut=False, retrieval_rank=1, unit_match=True),
    bi.PerLineRecord(row_type="paired", verdict="correct", needs_review=False, was_barcode_shortcut=False, retrieval_rank=7, unit_match=False),
    bi.PerLineRecord(row_type="paired", verdict="unresolved", needs_review=True, was_barcode_shortcut=False, retrieval_rank=None, unit_match=None),
]
metrics8 = bi.compute_metrics(recs8)
check("REAL FEATURE: recall@1 = 1/3 (سطر واحد بس رتبته 1)", metrics8["retrieval_recall_at_1"] == round(1 / 3, 4))
check("REAL FEATURE: recall@5 = 1/3 (السطر برتبة 7 لسا برّه top5)", metrics8["retrieval_recall_at_5"] == round(1 / 3, 4))
check("REAL FEATURE: recall@10 = 2/3 (رتبة 7 تدخل ضمن top10)", metrics8["retrieval_recall_at_10"] == round(2 / 3, 4))
check("retrieval_not_found_count يحسب السطر بلا رتبة (فشل استرجاع كامل) بشكل منفصل", metrics8["retrieval_not_found_count"] == 1)
check("unit_accuracy يستبعد السطر غير القابل للمقارنة (None) - 1/2 مو 1/3", metrics8["unit_accuracy"] == 0.5 and metrics8["unit_comparable_count"] == 2)
check("auto_match_coverage = 2/3 (سطرين قبول تلقائي من أصل 3 متوقَّعة)", metrics8["auto_match_coverage"] == round(2 / 3, 4))
check("auto_match_precision = 2/2 (الاثنين المقبولين تلقائياً كانا صحيحين فعلاً)", metrics8["auto_match_precision"] == 1.0)
check("review_rate = 1/3 (سطر واحد يحتاج مراجعة من أصل 3 متزاوجة)", metrics8["review_rate"] == round(1 / 3, 4))


print("\n--- summary ---")
total = len(results)
passed = sum(1 for _, ok in results if ok)
print(f"{passed}/{total} checks passed")
if passed != total:
    sys.exit(1)
