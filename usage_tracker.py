"""
تتبّع استهلاك الذكاء الاصطناعي (Claude) محلياً على هذا الجهاز.

ملاحظة مهمة: ما فيه طريقة موثّقة نسأل بيها Anthropic مباشرة "كم رصيدي
المتبقي؟" بمفتاح API عادي (تحققنا من صفحة الأسعار الرسمية - فيها أسعار
التوكن بس، لا يوجد endpoint لجلب الرصيد). لذلك الرقم هنا **تقدير محلي**:
نحسب تكلفة كل استدعاء فعلي من بيانات usage الحقيقية اللي يرجّعها كل رد من
Claude، ونجمعها، ونطرحها من رصيد بداية تكتبه أنت يدوياً. لو استُخدم نفس
المفتاح من جهاز ثاني أو مكان ثاني، هذا الرقم ما يعرف عنه شي.
"""

import json

import config

_USAGE_FILE = config.BASE_DIR / "usage_state.json"

# أسعار Claude Opus 5 الرسمية (دولار لكل مليون توكن) - تحققنا منها من صفحة
# أسعار Anthropic الرسمية مباشرة (platform.claude.com/docs)، وتتطابق تقريباً
# مع التكلفة الفعلية المقيسة سابقاً (فاتورة 11 صنف ≈ 0.0335$).
INPUT_PRICE_PER_MTOK = 5.0
OUTPUT_PRICE_PER_MTOK = 25.0

_DEFAULT_STATE = {"starting_balance": None, "total_spent": 0.0, "ai_enabled": True}


def _load_state() -> dict:
    if _USAGE_FILE.exists():
        try:
            state = json.loads(_USAGE_FILE.read_text(encoding="utf-8"))
            return {**_DEFAULT_STATE, **state}
        except (json.JSONDecodeError, OSError):
            pass
    return dict(_DEFAULT_STATE)


def _save_state(state: dict) -> None:
    _USAGE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def record_usage(input_tokens: int, output_tokens: int) -> float:
    """يسجّل تكلفة استدعاء API حقيقي (من usage الرد نفسه) ويرجّع تكلفته
    بالدولار."""
    cost = (input_tokens / 1_000_000) * INPUT_PRICE_PER_MTOK + (output_tokens / 1_000_000) * OUTPUT_PRICE_PER_MTOK
    state = _load_state()
    state["total_spent"] = state.get("total_spent", 0.0) + cost
    _save_state(state)
    return cost


def get_total_spent() -> float:
    return _load_state().get("total_spent", 0.0)


def get_starting_balance() -> float | None:
    return _load_state().get("starting_balance")


def set_starting_balance(amount: float) -> None:
    state = _load_state()
    state["starting_balance"] = amount
    _save_state(state)


def get_remaining_balance() -> float | None:
    state = _load_state()
    starting = state.get("starting_balance")
    if starting is None:
        return None
    return starting - state.get("total_spent", 0.0)


def is_ai_enabled() -> bool:
    return _load_state().get("ai_enabled", True)


def set_ai_enabled(enabled: bool) -> None:
    state = _load_state()
    state["ai_enabled"] = enabled
    _save_state(state)
