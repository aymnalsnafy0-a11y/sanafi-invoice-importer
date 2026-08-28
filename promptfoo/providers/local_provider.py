"""
Provider محلي بحت (صفر استدعاء API) - يرجّع فقط أفضل مرشّح محلي (المرتبة
الأولى بقائمة candidates المُعدَّة مسبقاً بـgenerate_dataset.py عبر
matching_engine.suggest_candidates الحقيقية) - يمثّل "لو ما فيه Semantic
إطلاقاً" كخط أساس للمقارنة.
"""

from __future__ import annotations


def call_api(prompt, options, context):
    variables = context.get("vars", {})
    candidates = variables.get("candidates") or []
    if not candidates:
        return {"output": "", "error": "no candidates"}

    top1 = candidates[0]
    return {
        "output": top1["code"],
        "metadata": {
            "confidence": top1.get("local_confidence"),
            "reason": top1.get("local_reason"),
            "name": top1.get("name"),
        },
    }
