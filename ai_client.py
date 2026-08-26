"""
تهيئة عميل Anthropic المشتركة - نفس آلية المفتاح والتفعيل/الإيقاف المستخدمة
من كل مكان بالمشروع يحتاج يتكلّم مع Claude (قراءة الفاتورة البصرية، الشات،
وإعادة الترتيب الدلالي). استُخرجت من vision_extract.py لملف مستقل عشان أي
وحدة جديدة (زي semantic_matcher.py) تعيد استخدامها بدون تكرار كود أو إنشاء
نظام اعتماد منفصل - نفس المفتاح، نفس فحص "الذكاء الاصطناعي متوقف يدوياً".
"""

import os

import anthropic

import config
import usage_tracker


def load_api_key() -> str | None:
    key_file = config.ANTHROPIC_API_KEY_FILE
    if key_file.exists():
        key = key_file.read_text(encoding="utf-8").strip()
        if key:
            return key
    return os.environ.get("ANTHROPIC_API_KEY")


def get_client(timeout: float | None = None) -> anthropic.Anthropic:
    """يرجّع عميل Anthropic جاهز. timeout اختياري (ثوانٍ) - لو ما تُرك،
    مهلة SDK الافتراضية تُستخدم (نفس سلوك الاستدعاءات الحالية بدون تغيير)."""
    if not usage_tracker.is_ai_enabled():
        raise RuntimeError(
            "الذكاء الاصطناعي متوقف حالياً (أوقفته يدوياً من زر \"💰 رصيد الذكاء "
            "الاصطناعي\"). فعّله من نفس الزر لو تبي تكمل الاستخراج أو الشات."
        )
    api_key = load_api_key()
    if not api_key:
        raise RuntimeError(
            "لم يتم العثور على مفتاح Anthropic API. ضعه في ملف "
            f"{config.ANTHROPIC_API_KEY_FILE.name} داخل مجلد الأداة (سطر واحد "
            "يحتوي المفتاح)، أو في متغير البيئة ANTHROPIC_API_KEY."
        )
    if timeout is not None:
        return anthropic.Anthropic(api_key=api_key, timeout=timeout)
    return anthropic.Anthropic(api_key=api_key)
