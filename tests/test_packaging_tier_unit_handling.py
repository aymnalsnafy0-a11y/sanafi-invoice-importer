"""
اختبارات معالجة "وحدة التعبئة" الواعية بالسياق (Packaging-aware unit handling) -
قرار صريح من المستخدم بعد فحص فاتورة عميل حقيقية (2026-08-26): اختلاف
"كرتون/صندوق/كيس/دستة/بالة" بالفاتورة مقابل "حبة" بقاعدة الأصناف (نمط شائع
جداً - فواتير الموردين تفوتر بالجملة، القاعدة مسجّلة بسعر أصغر وحدة بيع) لا
يجب يمنع القبول التلقائي بمفرده، **بس فقط** لو بقية الأدلة قوية وواضحة (اسم
قوي، حجم/وزن متطابق، عدد قطع غير متعارض). أي اختلاف آخر غير مفسَّر يبقى سبب
مراجعة كالمعتاد - هذا الملف يتحقق من الشرط الصارم بدقة، مو إلغاء الفحص عموماً.
"""

import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, r"D:\scanar\invoice_importer")

results = []


def check(name, condition):
    results.append((name, bool(condition)))
    print(("PASS" if condition else "FAIL"), "-", name)


import config
import item_attributes as ia
import matching_engine
from items import ReferenceItem
from line_item import ExtractedLine


def L(description, unit, **kw):
    return ExtractedLine(
        raw_text="x", description=description, quantity=1, unit_price=kw.pop("unit_price", None), total=1,
        ocr_confidence=100, unit=unit, **kw,
    )


def score(line, ref_item, sources=frozenset()):
    line_attrs = ia.extract_attributes(line.description)
    ref_attrs = ia.extract_attributes(ref_item.name)
    return matching_engine._score_one_candidate(line, line_attrs, ref_item, ref_attrs, set(sources))


print("=== 1) الحالة الإيجابية: كرتون بالفاتورة مقابل حبة بالقاعدة + اسم قوي + حجم متطابق + عدد قطع غير متعارض -> لا يُقصّ ===")
line1 = L("بيبسي دايت 250 مل", unit="CTN(24)")
ref1 = ReferenceItem(code="P1", name="بيبسي دايت 250 مل", barcode="", default_unit="حبة", internal_id="1", unit_id="1")
c1 = score(line1, ref1)
check("REAL FIX: ما انقصّ لسقف 40 - اختلاف كرتون/حبة وحده مو تعارض هنا", c1.confidence > config.MATCH_ATTRIBUTE_CONFLICT_CAP)
check("has_structural_conflict=False (ما اعتُبر تعارض بنيوي)", c1.has_structural_conflict is False)
check("السبب يوضّح إنها ملاحظة مو تعارض", "ملاحظة" in c1.reason and "تعبئة مختلفة" in c1.reason)
check("لسا ما فيه علامة ⚠ بخصوص الوحدة الصريحة تحديداً", "⚠ اختلاف الوحدة الصريحة" not in c1.reason)


print("\n=== 2) نفس السيناريو بس بالاتجاه المعاكس (حبة بالفاتورة، كرتون بالقاعدة) -> نفس التساهل ===")
line2 = L("بيبسي دايت 250 مل", unit="حبة")
ref2 = ReferenceItem(code="P2", name="بيبسي دايت 250 مل", barcode="", default_unit="كرتون", internal_id="2", unit_id="1")
c2 = score(line2, ref2)
check("نفس التساهل يشتغل بالاتجاه المعاكس (حبة/كرتون)", c2.confidence > config.MATCH_ATTRIBUTE_CONFLICT_CAP)


print("\n=== 3) اسم ضعيف (تحت MIN_SUGGEST_THRESHOLD) + كرتون/حبة + حجم متطابق -> يبقى سبب مراجعة (السقف يُطبَّق) ===")
line3 = L("مشروب غازي متنوع كبير جدا", unit="CTN(24)")  # اسم بعيد جداً عن اسم القاعدة
ref3 = ReferenceItem(code="P3", name="بيبسي دايت 250 مل", barcode="", default_unit="حبة", internal_id="3", unit_id="1")
c3 = score(line3, ref3)
check("تأكيد مسبق: الاسم فعلاً تحت العتبة", (c3.confidence <= config.MATCH_ATTRIBUTE_CONFLICT_CAP))
check("REAL SAFETY: اسم ضعيف -> التساهل ما ينطبق، السقف يبقى فعّال", c3.confidence <= config.MATCH_ATTRIBUTE_CONFLICT_CAP and c3.has_structural_conflict is True)


print("\n=== 4) اسم قوي + كرتون/حبة، لكن الحجم/الوزن فعلاً مختلف -> يبقى سبب مراجعة ===")
line4 = L("بيبسي دايت 330 مل", unit="CTN(24)")  # نفس الاسم تقريباً، حجم مختلف فعلياً
ref4 = ReferenceItem(code="P4", name="بيبسي دايت 250 مل", barcode="", default_unit="حبة", internal_id="4", unit_id="1")
c4 = score(line4, ref4)
check("REAL SAFETY: حجم مختلف فعلياً -> التساهل ما ينطبق رغم كرتون/حبة، السقف يبقى فعّال", c4.confidence <= config.MATCH_ATTRIBUTE_CONFLICT_CAP and c4.has_structural_conflict is True)


print("\n=== 5) اسم قوي + حجم متطابق + كرتون/حبة، لكن عدد القطع مختلف فعلياً بالاسمين -> يبقى سبب مراجعة ===")
line5 = L("بيبسي دايت 250 مل × 12", unit="CTN(24)")
ref5 = ReferenceItem(code="P5", name="بيبسي دايت 250 مل × 24", barcode="", default_unit="حبة", internal_id="5", unit_id="1")
c5 = score(line5, ref5)
check("REAL SAFETY: عدد قطع متعارض بالاسم -> التساهل ما ينطبق، السقف يبقى فعّال", c5.confidence <= config.MATCH_ATTRIBUTE_CONFLICT_CAP and c5.has_structural_conflict is True)


print("\n=== 6) اختلاف بين وحدتي تعبئة 'خارجيتين' مختلفتين (كرتون مقابل صندوق) - مو 'حبة' - يبقى تعارض كامل كالمعتاد ===")
line6 = L("بيبسي دايت 250 مل", unit="CTN(24)")
ref6 = ReferenceItem(code="P6", name="بيبسي دايت 250 مل", barcode="", default_unit="صندوق", internal_id="6", unit_id="1")
c6 = score(line6, ref6)
check("REAL SAFETY (نطاق الإصلاح محدود عمداً): كرتون مقابل صندوق (بدون 'حبة') يبقى تعارض كامل، مو مشمول بالتساهل", c6.confidence <= config.MATCH_ATTRIBUTE_CONFLICT_CAP and c6.has_structural_conflict is True)
check("السبب يذكر ⚠ اختلاف الوحدة الصريحة (السلوك القديم، مو ملاحظة)", "⚠ اختلاف الوحدة الصريحة" in c6.reason)


print("\n=== 7) الحالة الإيجابية تنطبق أيضاً على مرشّح الذاكرة المتعلّمة (_score_learned_candidate) ===")
learned_item7 = ReferenceItem(code="L7", name="بيبسي دايت 250 مل", barcode="", default_unit="حبة", internal_id="7", unit_id="1")
line7 = L("بيبسي دايت 250 مل", unit="CTN(24)")
line7_attrs = ia.extract_attributes(line7.description)
c7 = matching_engine._score_learned_candidate(line7, line7_attrs, learned_item7, confirm_count=3)
check("REAL FIX (مسار الذاكرة المتعلّمة أيضاً): كرتون/حبة وحده ما يقصّ ثقة مطابقة متعلّمة", c7.confidence > config.MATCH_ATTRIBUTE_CONFLICT_CAP)
check("has_structural_conflict=False لمرشّح الذاكرة", c7.has_structural_conflict is False)

print("\n--- 7ب) لكن لو الحجم فعلاً متعارض، مرشّح الذاكرة المتعلّمة يبقى محدود بالسقف كالمعتاد (الاستثناء ما 'يفكّه') ---")
learned_item7b = ReferenceItem(code="L7B", name="بيبسي دايت 500 مل", barcode="", default_unit="حبة", internal_id="7b", unit_id="1")
line7b = L("بيبسي دايت 250 مل", unit="CTN(24)")
line7b_attrs = ia.extract_attributes(line7b.description)
c7b = matching_engine._score_learned_candidate(line7b, line7b_attrs, learned_item7b, confirm_count=5)
check("REAL SAFETY: تعارض حجم حقيقي حتى بمرشّح ذاكرة -> السقف يبقى فعّال", c7b.confidence <= config.MATCH_ATTRIBUTE_CONFLICT_CAP)


print("\n--- summary ---")
total = len(results)
passed = sum(1 for _, ok in results if ok)
print(f"{passed}/{total} checks passed")
if passed != total:
    sys.exit(1)
