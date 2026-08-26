"""
"ذاكرة" المطابقات اللي أكّدها المستخدم يدوياً - مورد + نص صنف الفاتورة ->
صنف حقيقي بالقاعدة. مرة تصحّح صنف يدوياً، الأداة تتذكره للأبد؛ المرة الجاية
نفس الوصف (أو قريب منه) من نفس المورد يتطابق فوراً بدون مراجعة.

محلي بالكامل (ملف JSON بمجلد الأداة) - بنفس نمط usage_tracker.py بالضبط.
لا علاقة له بقاعدة بيانات AccSystem إطلاقاً، لا قراءة ولا كتابة.

قرار مهم: بس التأكيد اليدوي الصريح من المستخدم (اختيار من نافذة اقتراحات،
أو تعديل رقم صنف نجح) يُغذّي هذا الجدول - مو أي تطابق تلقائي بثقة عالية،
حتى لو ≥95%. الهدف: منع تطابق تلقائي خاطئ من "يتعلّم" كحقيقة مؤكدة بدون
ما تشوفه عين بشرية ولو مرة.
"""

import json
from datetime import datetime, timezone

import config
from item_attributes import extract_attributes
from rapidfuzz import fuzz

_MATCHES_FILE = config.BASE_DIR / "learned_matches.json"
_FUZZY_LOOKUP_THRESHOLD = 92  # تطابق تقريبي بس بين نصوص نفس المورد، مو القاعدة كلها

_DEFAULT_STATE = {"matches": {}}


def _load_state() -> dict:
    if _MATCHES_FILE.exists():
        try:
            state = json.loads(_MATCHES_FILE.read_text(encoding="utf-8"))
            return {**_DEFAULT_STATE, **state}
        except (json.JSONDecodeError, OSError):
            pass
    return {"matches": {}}


def _save_state(state: dict) -> None:
    _MATCHES_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_supplier(name: str | None) -> str:
    if not name:
        return ""
    return " ".join(name.strip().casefold().split())


def normalize_item_text(text: str) -> str:
    return extract_attributes(text or "").normalized_text


def _make_key(supplier_norm: str, item_text_norm: str) -> str:
    return f"{supplier_norm}||{item_text_norm}"


def lookup(supplier: str | None, item_text: str) -> tuple[dict, int] | None:
    """يرجّع (بيانات الصنف المحفوظة, عدد التأكيدات) لو لقى تطابق، وإلا None.
    أول تطابق مباشر (سريع)، وإلا تشابه ضبابي بس بين مفاتيح نفس المورد."""
    state = _load_state()
    supplier_norm = normalize_supplier(supplier)
    item_norm = normalize_item_text(item_text)
    if not item_norm:
        return None

    direct_key = _make_key(supplier_norm, item_norm)
    entry = state["matches"].get(direct_key)
    if entry is not None:
        return entry, entry.get("confirm_count", 1)

    prefix = f"{supplier_norm}||"
    best_entry = None
    best_score = 0.0
    for key, entry in state["matches"].items():
        if not key.startswith(prefix):
            continue
        stored_item_text = key[len(prefix):]
        score = fuzz.token_sort_ratio(item_norm, stored_item_text)
        if score >= _FUZZY_LOOKUP_THRESHOLD and score > best_score:
            best_entry, best_score = entry, score

    if best_entry is not None:
        return best_entry, best_entry.get("confirm_count", 1)
    return None


def codes_confirmed_for_supplier(supplier: str | None) -> set[str]:
    """يرجّع كل أرقام الأصناف اللي سبق تأكيدها لنفس المورد، من أي نص وصف
    (بعكس lookup اللي يحتاج نفس/قريب من نص معيّن). تُستخدم من
    matching_engine.py كإشارة "هذا المورد باع هذا الصنف قبل" لتوسيع
    الاسترجاع - مو فلتر حصري، لأن المورد ممكن يبيع صنف جديد أول مرة."""
    supplier_norm = normalize_supplier(supplier)
    if not supplier_norm:
        return set()
    state = _load_state()
    prefix = f"{supplier_norm}||"
    return {
        entry["matched_item_code"]
        for key, entry in state["matches"].items()
        if key.startswith(prefix) and entry.get("matched_item_code")
    }


def record_confirmation(supplier: str | None, item_text: str, ref_item) -> None:
    """يسجّل تأكيد المستخدم اليدوي. لو نفس المفتاح ونفس الصنف من قبل، يزيد
    عداد التأكيد (يرفع الثقة). لو المستخدم صحّح لصنف مختلف عن المحفوظ سابقاً،
    التصحيح اليدوي الجديد يفوز دايماً ويرجّع العداد لـ1 (ثقة جديدة تُبنى
    من الصفر، مو تراكم فوق قرار كان غلط)."""
    supplier_norm = normalize_supplier(supplier)
    item_norm = normalize_item_text(item_text)
    if not item_norm:
        return

    state = _load_state()
    key = _make_key(supplier_norm, item_norm)
    existing = state["matches"].get(key)
    now = datetime.now(timezone.utc).isoformat()

    if existing is not None and existing.get("matched_item_code") == ref_item.code:
        existing["confirm_count"] = existing.get("confirm_count", 1) + 1
        existing["last_confirmed"] = now
    else:
        state["matches"][key] = {
            "matched_item_code": ref_item.code,
            "matched_item_name": ref_item.name,
            "matched_internal_id": ref_item.internal_id,
            "matched_unit_id": ref_item.unit_id,
            "barcode": ref_item.barcode,
            "unit": ref_item.default_unit,
            "confirm_count": 1,
            "first_confirmed": now,
            "last_confirmed": now,
        }
    _save_state(state)


def confidence_for_confirm_count(confirm_count: int) -> float:
    """1 تأكيد=80%، كل تأكيد إضافي +5%، بحد أقصى 99% (أبداً 100% - الباركود
    بس يستاهل يقين كامل)."""
    return min(99.0, 80.0 + (confirm_count - 1) * 5.0)
