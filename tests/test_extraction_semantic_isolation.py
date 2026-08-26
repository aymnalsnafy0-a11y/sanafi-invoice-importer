"""
اختبار Safety حقيقي: يثبت أن مسار الاستخراج (allow_semantic=False) لا يستدعي
طبقة الذكاء الاصطناعي الدلالي إطلاقاً - ليس فقط أن النتيجة النهائية تبدو
صحيحة، بل أن الدالة المسؤولة عن نداء AI (semantic_enhance_candidates) لا
تُستدعى من الأساس. راجع matching_engine.enhance_one(allow_semantic=...)
و_extract_one_invoice بـapp.py (يستدعيها بـallow_semantic=False دائماً).
"""

import io
import sys
import tempfile
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

results = []


def check(name, condition):
    results.append((name, bool(condition)))
    print(("PASS" if condition else "FAIL"), "-", name)


import learned_matches
import settings as settings_module

tmp_dir = tempfile.mkdtemp()
learned_matches._MATCHES_FILE = Path(tmp_dir) / "learned_matches_test.json"
settings_module._SETTINGS_FILE = Path(tmp_dir) / "matching_settings_test.json"

import matching_engine
from items import ReferenceItem
from line_item import ExtractedLine


def L(description, **kw):
    return ExtractedLine(raw_text="x", description=description, quantity=1, unit_price=kw.pop("unit_price", None), total=1, ocr_confidence=100, **kw)


reference = [
    ReferenceItem(code="500", name="حليب نادك كامل الدسم 1 لتر", barcode="1111", default_unit="كرتون", internal_id="9", unit_id="2"),
    ReferenceItem(code="600", name="بيبسي 330 مل كرتون 24 حبة", barcode="", default_unit="كرتون", internal_id="10", unit_id="3"),
]
idx = matching_engine.build_reference_attrs_index(reference)


class _MustNotCallSemantic(Exception):
    pass


def _boom(*args, **kwargs):
    raise _MustNotCallSemantic("semantic_enhance_candidates استُدعيت رغم allow_semantic=False")


print("--- SAFETY: allow_semantic=False يجب ألا يستدعي semantic_enhance_candidates إطلاقاً ---")
_real_semantic_enhance_candidates = matching_engine.semantic_enhance_candidates
matching_engine.semantic_enhance_candidates = _boom
try:
    vague_line = L("منتج غير محدد بلا اسم أو حجم واضح")
    matching_engine.enhance_one(vague_line, reference, supplier_name=None, reference_attrs_index=idx, allow_semantic=False)
    never_called = True
except _MustNotCallSemantic:
    never_called = False
finally:
    matching_engine.semantic_enhance_candidates = _real_semantic_enhance_candidates

check("REAL SAFETY: enhance_one(allow_semantic=False) ينجح بدون استدعاء semantic_enhance_candidates إطلاقاً (حتى لو رفعت Exception لو استُدعيت)", never_called)
check("النتيجة المحلية البحتة لسا منطقية - وصف غامض بلا مطابقة قوية -> needs_review=True", vague_line.needs_review is True)
check("لا يخمّن كود صنف - matched_item_code يبقى فاضي", vague_line.matched_item_code == "")

print("\n--- REGRESSION GUARD: allow_semantic=True/الافتراضي لسا يحافظ على السلوك القديم (الطبقة نفسها لسا نشطة) ---")
import semantic_matcher

# ما نعتمد على اتصال إنترنت حقيقي هنا - rerank نفسها معطّلة (ترجع None)،
# لكن هذا مختلف عن الاختبار الأول: هنا نتحقق إن enhance_one لسا *تصل*
# لـsemantic_enhance_candidates بالسلوك الافتراضي (allow_semantic=True)،
# عكس القسم الأول اللي أثبت إنها ما توصلها إطلاقاً لما allow_semantic=False.
semantic_matcher.rerank = lambda *a, **k: None

call_tracker = {"called": False}
_orig = matching_engine.semantic_enhance_candidates


def _tracking_wrapper(*args, **kwargs):
    call_tracker["called"] = True
    return _orig(*args, **kwargs)


matching_engine.semantic_enhance_candidates = _tracking_wrapper
try:
    vague_line2 = L("منتج غير محدد بلا اسم أو حجم واضح")
    matching_engine.enhance_one(vague_line2, reference, supplier_name=None, reference_attrs_index=idx)  # allow_semantic افتراضي = True
finally:
    matching_engine.semantic_enhance_candidates = _orig

check("REAL FEATURE: النداء الافتراضي (allow_semantic=True) لسا يصل فعلياً لـsemantic_enhance_candidates (الطبقة موجودة، مو محذوفة)", call_tracker["called"] is True)
check("والنتيجة النهائية لسا صحيحة (تنحدر محلياً بهدوء لما rerank ترجع None) - needs_review=True", vague_line2.needs_review is True)

print("\n--- summary ---")
passed = sum(1 for _, ok in results if ok)
print(f"{passed}/{len(results)} checks passed")
if passed != len(results):
    sys.exit(1)
