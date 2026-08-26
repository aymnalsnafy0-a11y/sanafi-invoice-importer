import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, r"D:\scanar\invoice_importer")

from item_attributes import extract_attributes, check_attribute_conflict

results = []


def check(name, condition):
    results.append((name, bool(condition)))
    print(("PASS" if condition else "FAIL"), "-", name)


print("--- extract_attributes: pack + size combined pattern ---")
a = extract_attributes("بيبسي 330 مل × 24 كرتون")
check("12x330ml-style: pack_count extracted", a.pack_count == 24)
check("12x330ml-style: size_value extracted", a.size_value == 330)
check("12x330ml-style: size_value_base in ml", a.size_value_base == 330)
check("12x330ml-style: unit_word = carton", a.unit_word == "carton")

b = extract_attributes("PEPSI 330ML X24")
check("english variant: pack_count extracted", b.pack_count == 24)
check("english variant: size_value extracted", b.size_value == 330)

print("\n--- extract_attributes: 'كرتون 24 حبة' phrasing (pack count separate from combined pattern) ---")
c = extract_attributes("مياه صحة كرتون 24 حبة 330 مل")
check("carton-then-count phrasing: pack_count = 24", c.pack_count == 24)
check("carton-then-count phrasing: unit_word = carton (outer, not piece)", c.unit_word == "carton")

print("\n--- weight unit equivalence: 1kg == 1000g ---")
kg_item = extract_attributes("حليب مجفف وادي فاطمه 1كغ")
g_item = extract_attributes("حليب مجفف وادي فاطمه 1000غ")
check("1kg extracted as weight family with base=1000", kg_item.size_value_base == 1000)
check("1000g extracted as weight family with base=1000", g_item.size_value_base == 1000)
conflict, reason = check_attribute_conflict(kg_item, g_item)
check("1kg vs 1000g: NOT flagged as a conflict (same real value)", conflict is False)

print("\n--- real-world case from this project: NIDO, invoice says كغ but catalog says جم ---")
# ملاحظة: النص الحقيقي المستخرج من الفاتورة كان "1800كغ" (كيلوغرام) بينما
# القاعدة "1800جم" (غرام) - فرق 1000 ضعف حرفياً بالنص. حتى لو الاحتمال إنها
# نفس المنتج فعلياً (خطأ قراءة/كتابة بالوحدة)، ما نقدر نفترض هذا برمجياً -
# الأصح إنها تُعلَّم كـ"تعارض" وتحتاج مراجعة بشرية، مو تُطابَق تلقائياً
# بثقة رغم اختلاف الوحدة المكتوبة فعلياً. هذا سلوك آمن مقصود، مو خطأ.
invoice_desc = extract_attributes("نيدو علب 1800كغ")
catalog_desc = extract_attributes("حليب مجفف نيدو 1800جم")
conflict, reason = check_attribute_conflict(invoice_desc, catalog_desc)
check("NIDO 1800كغ vs 1800جم: correctly FLAGGED (real unit mismatch as written)", conflict is True)
print(f"  invoice size_value_base={invoice_desc.size_value_base}, catalog size_value_base={catalog_desc.size_value_base}")

print("\n--- real danger case: 250ml vs 330ml must be flagged ---")
small = extract_attributes("بيبسي 250 مل")
big = extract_attributes("بيبسي 330 مل")
conflict, reason = check_attribute_conflict(small, big)
check("250ml vs 330ml: FLAGGED as a conflict", conflict is True)
print(f"  reason: {reason}")

print("\n--- real danger case: 12 pieces vs 24 pieces must be flagged ---")
p12 = extract_attributes("مناديل كلينكس 12 قطعة")
p24 = extract_attributes("مناديل كلينكس 24 قطعة")
conflict, reason = check_attribute_conflict(p12, p24)
check("12pcs vs 24pcs: FLAGGED as a conflict", conflict is True)

print("\n--- real danger case: carton vs piece must be flagged ---")
carton_item = extract_attributes("عصير برتقال كرتون 330 مل")
piece_item = extract_attributes("عصير برتقال حبة 330 مل")
conflict, reason = check_attribute_conflict(carton_item, piece_item)
check("carton vs piece: FLAGGED as a conflict", conflict is True)

print("\n--- missing attribute on one side must NOT be flagged ---")
has_size = extract_attributes("بيبسي 330 مل")
no_size = extract_attributes("بيبسي")
conflict, reason = check_attribute_conflict(has_size, no_size)
check("one side has no detected size at all: NOT flagged (missing != conflicting)", conflict is False)

print("\n--- brand guess (weak, best-effort) ---")
brand_test = extract_attributes("نيدو علب 1800كغ")
check("brand guess skips generic leading word 'علب', picks 'نيدو'", brand_test.brand == "نيدو")

print("\n--- summary ---")
total = len(results)
passed = sum(1 for _, ok in results if ok)
print(f"{passed}/{total} checks passed")
if passed != total:
    sys.exit(1)
