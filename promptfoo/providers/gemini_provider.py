"""
Placeholder فقط - Gemini غير مُدمَج بالمشروع حالياً (لا مفتاح API، لا مكتبة،
لا استدعاء حقيقي أي مكان). لا تخمين/محاكاة لنتيجة Gemini - يرجّع خطأ صريح
NOT_IMPLEMENTED بدل نتيجة وهمية قد تُقرأ بالغلط كأداء حقيقي.

لإضافته فعلياً مستقبلاً: أضف مكتبة/مفتاح Gemini الرسمي، ثم استبدل جسم
call_api بنداء حقيقي بنفس شكل claude_semantic_provider.py (نفس candidates/
اسم الحقل)، بدون تغيير promptfooconfig.yaml (فقط فعّل provider هذا الملف).
"""

from __future__ import annotations


def call_api(prompt, options, context):
    return {"output": "", "error": "NOT_IMPLEMENTED: Gemini غير مُدمَج بعد - راجع تعليق أعلى الملف"}
