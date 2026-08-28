"""
يقرأ promptfoo/results.json (ناتج `npx promptfoo eval`) ويطبع مقارنة واضحة
Local مقابل Claude Semantic فقط (Ground Truth حقيقي) - Gemini غير مُفعَّل
بعد (stub، راجع providers/gemini_provider.py)، يُستبعد من هذا الملخّص عمداً.

الاستخدام: python promptfoo/summarize_results.py
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


def main():
    results_path = Path(__file__).parent / "results.json"
    if not results_path.exists():
        print(f"[ERROR] {results_path} غير موجود - شغّل `npx promptfoo eval` أولاً.")
        sys.exit(1)

    data = json.loads(results_path.read_text(encoding="utf-8"))
    rows = data["results"]["results"]

    by_case: dict[str, dict] = {}
    for r in rows:
        label = r["provider"]["label"]
        if label == "gemini_stub":
            continue  # غير مُفعَّل بعد - يُستبعد من هذا الملخّص عمداً
        desc = r.get("vars", {}).get("description", "?")
        folder = r.get("vars", {}).get("folder", "?")
        expected = r.get("vars", {}).get("expected_code", "?")
        # المفتاح يشمل رقم حالة الاختبار الأصلي (testIdx) - عشان وصفين
        # متطابقين حرفياً بنفس الفاتورة (مثال حقيقي: "كب كيك الفانيلا 60غم"
        # يتكرر سطرين منفصلين) ما ينهاران على مفتاح واحد فيضيع أحدهما بصمت
        key = f"{folder}::{desc}::{r.get('testIdx', id(r))}"
        entry = by_case.setdefault(key, {"description": desc, "folder": folder, "expected_code": expected})
        output = (r.get("response") or {}).get("output") if isinstance(r.get("response"), dict) else None
        error = r.get("error")
        correct = bool(r.get("success")) and not error
        entry[label] = {"output": output, "correct": correct, "error": error}

    local_correct = sum(1 for e in by_case.values() if e.get("local", {}).get("correct"))
    claude_correct = sum(1 for e in by_case.values() if e.get("claude_semantic", {}).get("correct"))
    total = len(by_case)

    print("=" * 78)
    print(f"Promptfoo - Local Matching مقابل Claude Semantic (Ground Truth حقيقي، {total} حالة)")
    print("=" * 78)
    print(f"{'الوصف':40s} {'المتوقَّع':10s} {'Local':10s} {'Claude':10s}")
    print("-" * 78)
    for e in by_case.values():
        local = e.get("local", {})
        claude = e.get("claude_semantic", {})
        local_mark = "✓" if local.get("correct") else ("✗" if local.get("output") else "ERR")
        claude_mark = "✓" if claude.get("correct") else ("✗" if claude.get("output") else "ERR")
        desc = (e["description"][:38] + "…") if len(e["description"]) > 39 else e["description"]
        print(f"{desc:40s} {e['expected_code']:10s} {local_mark:10s} {claude_mark:10s}")
    print("-" * 78)
    print(f"Local Matching   : {local_correct}/{total} ({local_correct/total:.1%})" if total else "لا حالات")
    print(f"Claude Semantic  : {claude_correct}/{total} ({claude_correct/total:.1%})" if total else "")
    print(f"Gemini           : NOT_IMPLEMENTED (غير مُفعَّل بعد)")
    print("=" * 78)


if __name__ == "__main__":
    main()
