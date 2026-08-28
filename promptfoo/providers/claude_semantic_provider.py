"""
Provider يستدعي إعادة الترتيب الدلالي الحقيقية بـClaude (semantic_matcher.py
نفسه المستخدَم بالتطبيق فعلياً - صفر منطق مكرَّر) على نفس مجموعة المرشّحين
المحليين بالضبط (top-15 من generate_dataset.py) - مقارنة عادلة "نفس البيانات
لكل نموذج". يستخدم نفس مفتاح API/الكاش المحليين الموجودين بالمشروع أصلاً
(anthropic_api_key.txt، semantic_rerank_cache.json) - صفر بيانات إضافية تُرسل
لأي خدمة خارجية غير ضرورية غير Claude نفسه.
"""

from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(APP_ROOT))

import semantic_matcher


def call_api(prompt, options, context):
    variables = context.get("vars", {})
    candidates = variables.get("candidates") or []
    if not candidates:
        return {"output": "", "error": "no candidates"}

    semantic_candidates = [
        semantic_matcher.SemanticCandidateInput(
            code=c["code"], name=c["name"], barcode=c.get("barcode") or "",
            unit=c.get("unit") or "", size_value=c.get("size_value"),
            size_unit=c.get("size_unit"), pack_count=c.get("pack_count"),
            unit_word=c.get("unit_word"), local_confidence=c.get("local_confidence") or 0.0,
            local_reason=c.get("local_reason") or "",
        )
        for c in candidates
    ]

    result = semantic_matcher.rerank(
        description=variables.get("description", ""),
        supplier_name=variables.get("supplier_name") or None,
        quantity=variables.get("quantity"),
        unit=variables.get("unit") or "",
        unit_price=variables.get("unit_price"),
        size_value=variables.get("size_value"),
        size_unit=variables.get("size_unit"),
        pack_count=variables.get("pack_count"),
        unit_word=variables.get("unit_word"),
        candidates=semantic_candidates,
    )

    if result is None:
        # فشل الاستدعاء (شبكة/مفتاح/إلخ) - نرجّع فشلاً واضحاً، مو نتراجع
        # صامتين للمحلي (كان يخفي الفشل الحقيقي عن تقرير Promptfoo)
        return {"output": "", "error": "semantic_matcher.rerank returned None (API/network/key failure)"}

    return {
        "output": result.selected_code,
        "metadata": {
            "confidence": result.confidence,
            "reason": result.reason,
            "ambiguous": result.ambiguous,
            "alternative_codes": result.alternative_codes,
        },
    }
