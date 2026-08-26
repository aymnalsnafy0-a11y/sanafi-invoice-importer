"""
إعادة ترتيب دلالي بالذكاء الاصطناعي - طبقة إضافية اختيارية فوق محرك
المطابقة المحلي (matching_engine.py)، للحالات الصعبة فقط (اختلاف صياغة/لغة
كبير بين الفاتورة والقاعدة). لا يُستدعى مباشرة من دوال matching_engine.py
الأساسية - عبر طبقة orchestration منفصلة هناك (semantic_enhance_candidates)
عشان المحرك المحلي (suggest_candidates) يبقى دالة سريعة deterministic بدون
أي استدعاء شبكي، قابلة للاختبار بدون إنترنت.

قواعد صارمة يفرضها هذا الملف بنفسه (دفاع مضاعف، بالإضافة لأي فحص لاحق
بـmatching_engine.py):
- ممنوع اختيار كود غير موجود بقائمة المرشّحين المرسلة - أي رد يخالف هذا يُرفض بالكامل.
- أي فشل (شبكة/timeout/rate limit/JSON غير صالح/لا مفتاح/الذكاء الاصطناعي
  متوقف يدوياً) يرجّع None بهدوء - صفر استثناء يوصل للمتصل، صفر crash.
- كاش محلي (JSON) لتفادي تكرار نفس الاستدعاء - **مو تعلّم دائم**، له صلاحية
  منتهية (راجع config.SEMANTIC_RERANK_CACHE_TTL_SECONDS)، ومنفصل تماماً عن
  learned_matches.json (ذاك تأكيد بشري صريح، هذا مجرد تخزين مؤقت لأداء API).
"""

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field

import anthropic
from pydantic import BaseModel

import ai_client
import config
import usage_tracker

_CACHE_FILE = config.BASE_DIR / "semantic_rerank_cache.json"

_SYSTEM_PROMPT = """أنت مساعد مطابقة أصناف لبرنامج محاسبي. عندك سطر من فاتورة
مشتريات وقائمة أصناف مرشّحة من قاعدة بيانات حقيقية. مهمتك: تحديد أي مرشّح من
القائمة (لو وُجد) يطابق صنف الفاتورة فعلياً بالمعنى - حتى لو الأسماء بلغتين
مختلفتين (عربي/إنجليزي) أو صياغة مختلفة تماماً - مو بس تشابه الحروف.

قواعد صارمة يجب الالتزام بها:
- اختر فقط من الأكواد الموجودة صراحة بالقائمة المرسلة لك أدناه. ممنوع تماماً
  اختراع كود أو اقتراح صنف غير موجود بالقائمة.
- لو ولا مرشّح يطابق فعلياً (أو الوصف غامض جداً)، اجعل selected_code فارغاً (null).
- لو فيه أكثر من مرشّح محتمل بدون طريقة أكيدة للترجيح بينهم، اجعل
  ambiguous=true واذكر الأكواد المحتملة الثانية بـalternative_codes.
- ركّز على المعنى والخصائص (نوع المنتج، الحجم/الوزن، عدد القطع، نوع
  التعبئة) وليس فقط تشابه الحروف السطحي.
- رقم confidence من 0 إلى 100 يعكس مدى ثقتك بالاختيار."""


class _RerankResponseSchema(BaseModel):
    selected_code: str | None = None
    confidence: float = 0.0
    reason: str = ""
    ambiguous: bool = False
    alternative_codes: list[str] = []


@dataclass
class SemanticCandidateInput:
    code: str
    name: str
    barcode: str = ""
    unit: str = ""
    size_value: float | None = None
    size_unit: str | None = None
    pack_count: int | None = None
    unit_word: str | None = None
    local_confidence: float = 0.0
    local_reason: str = ""


@dataclass
class SemanticRerankResult:
    selected_code: str
    confidence: float
    reason: str
    ambiguous: bool
    alternative_codes: list[str] = field(default_factory=list)


def _cache_key(
    supplier_name: str | None, description: str, candidates: list[SemanticCandidateInput]
) -> str:
    """المفتاح يعتمد كمان على المرشّحين أنفسهم (كود+اسم)، مو بس المورد
    والوصف - عشان لو قائمة الأصناف تغيّرت (صنف جديد انضاف/انحذف)، نفس
    المورد ونفس الوصف ما يرجّعون كاش قديم غير دقيق بعد التغيير."""
    supplier_norm = (supplier_name or "").strip().casefold()
    desc_norm = " ".join((description or "").strip().casefold().split())
    codes_and_names = sorted(f"{c.code}:{c.name}" for c in candidates)
    raw = supplier_norm + "||" + desc_norm + "||" + "||".join(codes_and_names)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _load_cache() -> dict:
    if _CACHE_FILE.exists():
        try:
            return json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_cache(state: dict) -> None:
    try:
        _CACHE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass  # الكاش تحسين أداء بحت - فشل الكتابة ما يوقف أي شي


def _get_cached(key: str) -> SemanticRerankResult | None:
    entry = _load_cache().get(key)
    if entry is None:
        return None
    if time.time() - entry.get("cached_at", 0) > config.SEMANTIC_RERANK_CACHE_TTL_SECONDS:
        return None
    return SemanticRerankResult(
        selected_code=entry["selected_code"],
        confidence=entry["confidence"],
        reason=entry["reason"],
        ambiguous=entry["ambiguous"],
        alternative_codes=entry.get("alternative_codes", []),
    )


def _set_cached(key: str, result: SemanticRerankResult) -> None:
    state = _load_cache()
    state[key] = {
        "selected_code": result.selected_code,
        "confidence": result.confidence,
        "reason": result.reason,
        "ambiguous": result.ambiguous,
        "alternative_codes": result.alternative_codes,
        "cached_at": time.time(),
    }
    _save_cache(state)


def _build_user_message(
    description: str,
    supplier_name: str | None,
    quantity: float | None,
    unit: str,
    unit_price: float | None,
    size_value: float | None,
    size_unit: str | None,
    pack_count: int | None,
    unit_word: str | None,
    candidates: list[SemanticCandidateInput],
) -> str:
    """يبني رسالة تحتوي فقط بيانات الصنف الضرورية للمطابقة وأفضل
    المرشّحين - بدون أي معلومة عميل/فاتورة غير ضرورية، وبدون إرسال صورة
    الفاتورة مرة ثانية (تُقرأ مرة وحدة بـvision_extract.py فقط)."""
    lines = ["بيانات صنف الفاتورة:", f"- الوصف: {description}"]
    if supplier_name:
        lines.append(f"- المورد: {supplier_name}")
    if quantity is not None:
        lines.append(f"- الكمية: {quantity}")
    if unit:
        lines.append(f"- الوحدة كما بالفاتورة: {unit}")
    if unit_price is not None:
        lines.append(f"- سعر الوحدة: {unit_price}")
    if size_value is not None:
        lines.append(f"- الحجم/الوزن المستخرج محلياً: {size_value}{size_unit or ''}")
    if pack_count is not None:
        lines.append(f"- عدد القطع المستخرج محلياً: {pack_count}")
    if unit_word:
        lines.append(f"- نوع التعبئة المستخرج محلياً: {unit_word}")

    lines.append("\nالمرشّحون (اختر الكود الصحيح منهم فقط، أو null لو ولا وحد يطابق):")
    for c in candidates:
        parts = [f"code={c.code}", f"name={c.name}"]
        if c.barcode:
            parts.append(f"barcode={c.barcode}")
        if c.unit:
            parts.append(f"unit={c.unit}")
        if c.size_value is not None:
            parts.append(f"size={c.size_value}{c.size_unit or ''}")
        if c.pack_count is not None:
            parts.append(f"pack={c.pack_count}")
        if c.unit_word:
            parts.append(f"package_type={c.unit_word}")
        parts.append(f"local_confidence={c.local_confidence:.0f}%")
        if c.local_reason:
            parts.append(f"local_reason={c.local_reason}")
        lines.append("- " + " | ".join(parts))

    return "\n".join(lines)


def _validate_and_build_result(
    parsed: _RerankResponseSchema, candidates: list[SemanticCandidateInput]
) -> SemanticRerankResult | None:
    """تحقق صارم - أي مخالفة تعني رفض الرد بالكامل (fallback للمحرك
    المحلي، مو محاولة "إصلاح" رد جزئي الصحة)."""
    valid_codes = {c.code for c in candidates}
    if not parsed.selected_code:
        return None
    if parsed.selected_code not in valid_codes:
        return None
    confidence = max(0.0, min(100.0, parsed.confidence))
    # إزالة تكرار + استبعاد أي كود خارج القائمة المرسلة + استبعاد الكود
    # المختار نفسه لو تكرر غلطاً بقائمة البدائل
    alt_codes = []
    seen = {parsed.selected_code}
    for code in parsed.alternative_codes:
        if code in valid_codes and code not in seen:
            alt_codes.append(code)
            seen.add(code)
    return SemanticRerankResult(
        selected_code=parsed.selected_code,
        confidence=confidence,
        reason=(parsed.reason or "").strip(),
        ambiguous=bool(parsed.ambiguous),
        alternative_codes=alt_codes,
    )


def _call_api(
    description: str,
    supplier_name: str | None,
    quantity: float | None,
    unit: str,
    unit_price: float | None,
    size_value: float | None,
    size_unit: str | None,
    pack_count: int | None,
    unit_word: str | None,
    candidates: list[SemanticCandidateInput],
) -> SemanticRerankResult | None:
    try:
        client = ai_client.get_client(timeout=config.SEMANTIC_RERANK_TIMEOUT_SECONDS)
    except RuntimeError:
        # الذكاء الاصطناعي متوقف يدوياً أو المفتاح ناقص - فشل ناعم، المحرك
        # المحلي يكمل بدون أي تأثير
        return None

    user_message = _build_user_message(
        description, supplier_name, quantity, unit, unit_price,
        size_value, size_unit, pack_count, unit_word, candidates,
    )

    try:
        response = client.messages.parse(
            model=config.ANTHROPIC_MODEL,
            max_tokens=1000,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
            output_format=_RerankResponseSchema,
        )
    except (anthropic.APIConnectionError, anthropic.RateLimitError, anthropic.APIError, anthropic.AuthenticationError):
        return None
    except Exception:  # noqa: BLE001 - احتياط أخير: أي خطأ غير متوقع يبقى فشل ناعم، أبداً ما يوصل crash للمتصل
        return None

    if response.usage is not None:
        usage_tracker.record_usage(response.usage.input_tokens, response.usage.output_tokens)

    try:
        parsed = response.parsed_output
    except Exception:  # noqa: BLE001
        return None

    return _validate_and_build_result(parsed, candidates)


# قفل عام واحد لكل نداءات إعادة الترتيب الدلالي - يمنع نداءين متزامنين
# (من خيطي مراجعة مختلفين، أو نفس الصف يُفتح مرتين بسرعة) من ضرب API
# مكرَّر لنفس المفتاح بالضبط. بسيط ومتحفّظ (يسلسل كل نداءات هذا الملف)،
# مقبول لأن هذا مسار "حالات صعبة فقط" أصلاً، مو مسار ساخن متكرر.
_call_lock = threading.Lock()


def rerank(
    description: str,
    supplier_name: str | None,
    quantity: float | None,
    unit: str,
    unit_price: float | None,
    size_value: float | None,
    size_unit: str | None,
    pack_count: int | None,
    unit_word: str | None,
    candidates: list[SemanticCandidateInput],
) -> SemanticRerankResult | None:
    """يحاول إعادة ترتيب دلالي بالذكاء الاصطناعي. يرجّع None لأي سبب فشل -
    بدون استثناء يوصل للمتصل أبداً. آمن للاستدعاء من أي خيط."""
    if not candidates:
        return None

    key = _cache_key(supplier_name, description, candidates)
    cached = _get_cached(key)
    if cached is not None:
        return cached

    with _call_lock:
        # إعادة فحص الكاش بعد الحصول على القفل - لو خيط ثاني سبقنا وحل نفس
        # الطلب أثناء انتظارنا، نستفيد من نتيجته بدل استدعاء API مكرر
        cached = _get_cached(key)
        if cached is not None:
            return cached

        result = _call_api(
            description, supplier_name, quantity, unit, unit_price,
            size_value, size_unit, pack_count, unit_word, candidates,
        )
        if result is not None:
            _set_cached(key, result)
        return result
