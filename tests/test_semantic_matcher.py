"""
اختبارات semantic_matcher.py - عميل Anthropic مموّه بالكامل، صفر اتصال
إنترنت حقيقي وصفر تكلفة فعلية. يغطي: اختيار مرشّح صالح/خارج القائمة،
معالجة الفشل (timeout/invalid JSON/لا مفتاح)، الكاش، ومنع نداءات متزامنة
مكرّرة لنفس المفتاح.
"""

import io
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, r"D:\scanar\invoice_importer")

results = []


def check(name, condition):
    results.append((name, bool(condition)))
    print(("PASS" if condition else "FAIL"), "-", name)


import anthropic

import ai_client
import semantic_matcher
import usage_tracker

tmp_dir = tempfile.mkdtemp()
semantic_matcher._CACHE_FILE = Path(tmp_dir) / "semantic_rerank_cache_test.json"
# _call_api يسجّل usage عبر usage_tracker.record_usage() قبل حتى التحقق من
# صحة الرد - لازم نحوّل ملف التتبّع الحقيقي بعيداً هنا، وإلا كل رد مموّه
# (حتى المرفوض لاحقاً) يكتب تكلفة وهمية بملف usage_state.json الحقيقي.
usage_tracker._USAGE_FILE = Path(tmp_dir) / "usage_state_test.json"


# ---------- عميل Anthropic مموّه ----------

class _FakeUsage:
    def __init__(self, input_tokens=100, output_tokens=50):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeResponse:
    def __init__(self, parsed_output, usage=None):
        self.parsed_output = parsed_output
        self.usage = usage if usage is not None else _FakeUsage()


class _FakeMessages:
    def __init__(self, behavior, call_counter=None, delay=0.0):
        self._behavior = behavior  # دالة() -> _FakeResponse أو ترفع استثناء
        self._call_counter = call_counter
        self._delay = delay

    def parse(self, **kwargs):
        if self._call_counter is not None:
            self._call_counter[0] += 1
        if self._delay:
            time.sleep(self._delay)
        return self._behavior()


class _FakeClient:
    def __init__(self, behavior, call_counter=None, delay=0.0):
        self.messages = _FakeMessages(behavior, call_counter, delay)


def _candidates():
    return [
        semantic_matcher.SemanticCandidateInput(code="A1", name="صنف أ", local_confidence=40, local_reason="الاسم 40%"),
        semantic_matcher.SemanticCandidateInput(code="B2", name="صنف ب", local_confidence=35, local_reason="الاسم 35%"),
    ]


def _patch_client(monkeypatch_target, behavior, call_counter=None, delay=0.0):
    ai_client.get_client = lambda timeout=None: _FakeClient(behavior, call_counter, delay)


_original_get_client = ai_client.get_client


def reset_client():
    ai_client.get_client = _original_get_client


print("--- 1) AI يختار مرشّحاً صالحاً موجود فعلاً بالقائمة ---")
_patch_client(None, lambda: _FakeResponse(
    semantic_matcher._RerankResponseSchema(selected_code="A1", confidence=90, reason="نفس المعنى", ambiguous=False)
))
result1 = semantic_matcher.rerank("وصف تجريبي", "مورد", 1, "", None, None, None, None, None, _candidates())
check("النتيجة غير فارغة", result1 is not None)
check("الكود المختار صحيح", result1 is not None and result1.selected_code == "A1")
check("الثقة بالحدود الصحيحة (0-100)", result1 is not None and 0 <= result1.confidence <= 100)
reset_client()


print("\n--- 2) AI يختار كوداً خارج القائمة المرسلة -> يُرفض بالكامل ---")
_patch_client(None, lambda: _FakeResponse(
    semantic_matcher._RerankResponseSchema(selected_code="OUTSIDE999", confidence=95, reason="", ambiguous=False)
))
result2 = semantic_matcher.rerank("وصف", None, None, "", None, None, None, None, None, _candidates())
check("REAL SAFETY: رد يختار كوداً برّه القائمة يُرفض بالكامل (None)", result2 is None)
reset_client()


print("\n--- 3) alternative_codes تُفلتَر (تكرار + أكواد خارج القائمة) ---")
_patch_client(None, lambda: _FakeResponse(
    semantic_matcher._RerankResponseSchema(
        selected_code="A1", confidence=60, reason="", ambiguous=True,
        alternative_codes=["B2", "B2", "OUTSIDE999", "A1"],
    )
))
result3 = semantic_matcher.rerank("وصف", None, None, "", None, None, None, None, None, _candidates())
check("البدائل تحتوي B2 مرة وحدة بس", result3 is not None and result3.alternative_codes == ["B2"])
reset_client()


print("\n--- 4) timeout/فشل شبكة -> fallback بهدوء (None)، صفر crash ---")
_patch_client(None, lambda: (_ for _ in ()).throw(anthropic.APIConnectionError(message="timeout", request=None)))
semantic_matcher._CACHE_FILE = Path(tmp_dir) / "cache_timeout.json"
result4 = semantic_matcher.rerank("وصف جديد كلياً", None, None, "", None, None, None, None, None, _candidates())
check("فشل شبكي يرجع None بدون استثناء", result4 is None)
reset_client()


print("\n--- 5) رد غير صالح (JSON/تحليل فشل) -> fallback بهدوء ---")
class _BrokenMessages:
    def parse(self, **kwargs):
        raise ValueError("malformed response")


class _BrokenClient:
    def __init__(self):
        self.messages = _BrokenMessages()


ai_client.get_client = lambda timeout=None: _BrokenClient()
semantic_matcher._CACHE_FILE = Path(tmp_dir) / "cache_broken.json"
result5 = semantic_matcher.rerank("وصف آخر", None, None, "", None, None, None, None, None, _candidates())
check("رد غير صالح يرجع None بدون crash", result5 is None)
reset_client()


print("\n--- 6) بدون مفتاح API (get_client يرفع RuntimeError) -> النظام المحلي يكمل ---")
def _raise_no_key(timeout=None):
    raise RuntimeError("لم يتم العثور على مفتاح Anthropic API")


ai_client.get_client = _raise_no_key
semantic_matcher._CACHE_FILE = Path(tmp_dir) / "cache_no_key.json"
result6 = semantic_matcher.rerank("وصف ثالث", None, None, "", None, None, None, None, None, _candidates())
check("عدم وجود مفتاح API يرجع None بدون استثناء", result6 is None)
reset_client()


print("\n--- 7) قائمة مرشّحين فارغة -> ما يستدعي API إطلاقاً ---")
call_counter7 = [0]
_patch_client(None, lambda: _FakeResponse(semantic_matcher._RerankResponseSchema()), call_counter=call_counter7)
result7 = semantic_matcher.rerank("وصف", None, None, "", None, None, None, None, None, [])
check("قائمة فارغة ترجع None فوراً", result7 is None)
check("صفر نداء API لقائمة فارغة", call_counter7[0] == 0)
reset_client()


print("\n--- 8) الكاش يمنع نداء API مكرر لنفس الحالة بالضبط ---")
call_counter8 = [0]
semantic_matcher._CACHE_FILE = Path(tmp_dir) / "cache_dedup.json"
_patch_client(None, lambda: _FakeResponse(
    semantic_matcher._RerankResponseSchema(selected_code="A1", confidence=80, reason="", ambiguous=False)
), call_counter=call_counter8)
r_first = semantic_matcher.rerank("نفس الوصف بالضبط", "نفس المورد", None, "", None, None, None, None, None, _candidates())
r_second = semantic_matcher.rerank("نفس الوصف بالضبط", "نفس المورد", None, "", None, None, None, None, None, _candidates())
check("النداء الأول نجح", r_first is not None and r_first.selected_code == "A1")
check("REAL FEATURE: النداء الثاني (نفس المدخلات بالضبط) استفاد من الكاش - صفر نداء API إضافي", call_counter8[0] == 1)
check("النتيجتان متطابقتان", r_first == r_second)
reset_client()


print("\n--- 9) مفتاح الكاش يتغيّر لو المرشّحون اختلفوا (حتى لو نفس الوصف/المورد) ---")
call_counter9 = [0]
semantic_matcher._CACHE_FILE = Path(tmp_dir) / "cache_candidates_sensitivity.json"
_patch_client(None, lambda: _FakeResponse(
    semantic_matcher._RerankResponseSchema(selected_code="A1", confidence=70, reason="", ambiguous=False)
), call_counter=call_counter9)
different_candidates = [
    semantic_matcher.SemanticCandidateInput(code="A1", name="صنف أ", local_confidence=40),
    semantic_matcher.SemanticCandidateInput(code="C3", name="صنف ج جديد", local_confidence=30),  # مرشّح ثالث جديد - القائمة تغيّرت
]
semantic_matcher.rerank("نفس الوصف", "نفس المورد", None, "", None, None, None, None, None, _candidates())
semantic_matcher.rerank("نفس الوصف", "نفس المورد", None, "", None, None, None, None, None, different_candidates)
check("REAL SAFETY: تغيّر المرشّحين (حتى بنفس الوصف/المورد) يُسبّب نداء API جديد - الكاش ما يخلط الحالتين", call_counter9[0] == 2)
reset_client()


print("\n--- 10) نداءان متزامنان لنفس المفتاح بالضبط -> نداء API واحد فقط ---")
call_counter10 = [0]
semantic_matcher._CACHE_FILE = Path(tmp_dir) / "cache_concurrent.json"
_patch_client(None, lambda: _FakeResponse(
    semantic_matcher._RerankResponseSchema(selected_code="A1", confidence=75, reason="", ambiguous=False)
), call_counter=call_counter10, delay=0.15)  # تأخير مصطنع يضمن تداخل حقيقي بين الخيطين

concurrent_results = [None, None]


def _call_concurrent(slot):
    concurrent_results[slot] = semantic_matcher.rerank(
        "وصف متزامن", "مورد متزامن", None, "", None, None, None, None, None, _candidates()
    )


t1 = threading.Thread(target=_call_concurrent, args=(0,))
t2 = threading.Thread(target=_call_concurrent, args=(1,))
t1.start()
time.sleep(0.03)  # نضمن t1 دخل القفل قبل ما t2 يبدأ، بس لسا داخل التأخير المصطنع
t2.start()
t1.join(timeout=5)
t2.join(timeout=5)

check("الخيطان رجّعوا نتيجة صحيحة", concurrent_results[0] is not None and concurrent_results[1] is not None)
check("REAL FEATURE: نداءان متزامنان لنفس المفتاح بالضبط -> نداء API واحد فقط (القفل يمنع التكرار)", call_counter10[0] == 1)
reset_client()


print("\n--- summary ---")
total = len(results)
passed = sum(1 for _, ok in results if ok)
print(f"{passed}/{total} checks passed")
if passed != total:
    sys.exit(1)
