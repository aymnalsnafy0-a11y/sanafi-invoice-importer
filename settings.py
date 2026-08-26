"""
إعدادات محرك المطابقة الذكي - محفوظة محلياً، قابلة للتعديل من واجهة الأداة.
بنفس نمط usage_tracker.py بالضبط (ملف JSON بمجلد الأداة).

القيم الافتراضية (بالكود) موجودة بـconfig.py، منفصلة تماماً عن
AUTO_MATCH_THRESHOLD الموجود أصلاً (المستخدم بمطابقة items.py الحالية) -
ما نغيّر ولا نلمس ذاك الثابت إطلاقاً.
"""

import json

import config

_SETTINGS_FILE = config.BASE_DIR / "matching_settings.json"

_DEFAULTS = {
    "review_mode": "during_reading",  # أو "after_extraction"
    "auto_accept_threshold": config.MATCH_AUTO_ACCEPT_THRESHOLD,
    "needs_review_threshold": config.MATCH_NEEDS_REVIEW_THRESHOLD,
    "auto_learn_from_manual_edits": True,
}


def get_settings() -> dict:
    if _SETTINGS_FILE.exists():
        try:
            saved = json.loads(_SETTINGS_FILE.read_text(encoding="utf-8"))
            return {**_DEFAULTS, **saved}
        except (json.JSONDecodeError, OSError):
            pass
    return dict(_DEFAULTS)


def update_settings(**kwargs) -> None:
    current = get_settings()
    current.update(kwargs)
    _SETTINGS_FILE.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
