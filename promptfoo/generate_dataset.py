"""
يبني dataset حقيقي لـPromptfoo من فواتير Ground Truth الحقيقية المؤكَّدة
بالمشروع (test_invoices/ وtest_invoices/dataset/*/) - كل سطر فاتورة مقترن
فعلياً بـGround Truth يصير حالة اختبار واحدة: الوصف + نفس مجموعة المرشّحين
المحليين (top-15 - نفس المسار الحقيقي المستخدم بالتطبيق) + الكود الصحيح
المتوقَّع. أداة تطوير/تقييم فقط (راجع README.md) - لا تُستدعى من التطبيق.

الاستخدام:
    python promptfoo/generate_dataset.py
يكتب promptfoo/tests.json (يُقرأ من promptfooconfig.yaml).
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_ROOT))
sys.path.insert(0, str(APP_ROOT / "tools"))

import matching_engine
from items import load_item_reference
import benchmark_invoices as bi


def _load_vision_lines(folder: Path, pdf_path: Path):
    cache_path = bi.vision_cache_path(folder, pdf_path.stem)
    if not cache_path.exists():
        print(f"[SKIP] لا يوجد Vision cache لـ{folder} - شغّل benchmark_invoices.py مرة بدون --skip-vision أولاً.")
        return None
    from line_item import ExtractedLine
    from vision_extract import VisionPageResult
    pages = bi.load_vision_cache(cache_path, VisionPageResult, ExtractedLine)
    lines = []
    for page in pages:
        lines.extend(page.items)
    return lines, (pages[0].supplier_name if pages else None)


def _dataset_folders() -> list[Path]:
    base = APP_ROOT / "test_invoices"
    folders = [base]
    dataset_dir = base / "dataset"
    if dataset_dir.is_dir():
        folders.extend(p for p in sorted(dataset_dir.iterdir()) if p.is_dir())
    return folders


def build_cases_for_folder(folder: Path) -> list[dict]:
    pdfs = sorted(folder.glob("*.pdf"))
    amncs = sorted(folder.glob("*.AmnC"))
    xlsxs = sorted(folder.glob("*.xlsx"))
    amns = sorted(folder.glob("*.Amn"))
    if not (pdfs and amncs and xlsxs and amns):
        return []

    loaded = _load_vision_lines(folder, pdfs[0])
    if loaded is None:
        return []
    vision_lines, supplier_name = loaded

    reference = load_item_reference(amncs[0])
    ref_index = matching_engine.build_reference_attrs_index(reference)
    ground_truth = bi.load_ground_truth_excel(xlsxs[0])

    original_barcodes = [line.barcode for line in vision_lines]
    pairs, _missing, _extra = bi.align_lines(vision_lines, original_barcodes, ground_truth)

    cases = []
    for i, j, _method, _score in pairs:
        line = vision_lines[i]
        truth = ground_truth[j]
        if not truth.code:
            continue

        pool = matching_engine.suggest_candidates(
            line, reference, supplier_name=supplier_name, reference_attrs_index=ref_index, top_n=15,
        )
        if not pool:
            continue  # لا فائدة مقارنة إعادة ترتيب بلا أي مرشّح أصلاً

        line_attrs = matching_engine.extract_attributes(line.description)
        candidates = []
        for c in pool:
            ref_attrs = matching_engine.extract_attributes(c.item.name)
            candidates.append({
                "code": c.item.code, "name": c.item.name, "barcode": c.item.barcode,
                "unit": c.item.default_unit,
                "size_value": ref_attrs.size_value, "size_unit": ref_attrs.size_unit,
                "pack_count": ref_attrs.pack_count, "unit_word": ref_attrs.unit_word,
                "local_confidence": c.confidence, "local_reason": c.reason,
            })

        cases.append({
            "vars": {
                "folder": folder.name,
                "description": line.description,
                "supplier_name": supplier_name or "",
                "quantity": line.quantity, "unit": line.unit, "unit_price": line.unit_price,
                "size_value": line_attrs.size_value, "size_unit": line_attrs.size_unit,
                "pack_count": line_attrs.pack_count, "unit_word": line_attrs.unit_word,
                "candidates": candidates,
                "expected_code": truth.code,
                "local_top1_code": pool[0].item.code,
            },
        })
    return cases


def main():
    all_cases = []
    for folder in _dataset_folders():
        cases = build_cases_for_folder(folder)
        print(f"{folder}: {len(cases)} حالة")
        all_cases.extend(cases)

    out_path = Path(__file__).parent / "tests.json"
    out_path.write_text(json.dumps(all_cases, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nكتبت {len(all_cases)} حالة إجمالاً إلى {out_path}")


if __name__ == "__main__":
    main()
