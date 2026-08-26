"""اختبار دخان (بدون واجهة رسومية) لخط الأنابيب الكامل: صورة -> OCR -> مطابقة -> تصدير."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from exporter import export_to_excel
from items import load_item_reference, match_line_items
from ocr import extract_line_items
from pdf_utils import load_pages_as_images
from preprocess import preprocess_for_ocr

HERE = Path(__file__).resolve().parent


def main():
    invoice_path = HERE / "sample_invoice.png"
    items_path = HERE / "sample_item_master.xlsx"
    output_path = HERE / "sample_output.xlsx"

    print(f"Loading pages from: {invoice_path}")
    pages = load_pages_as_images(invoice_path)
    print(f"  -> {len(pages)} page(s)")

    reference = load_item_reference(items_path)
    print(f"Loaded {len(reference)} reference items")

    all_lines = []
    for page in pages:
        clean = preprocess_for_ocr(page)
        lines = extract_line_items(clean)
        all_lines.extend(lines)

    print(f"\nExtracted {len(all_lines)} candidate line(s):")
    for l in all_lines:
        print(f"  raw={l.raw_text!r}")
        print(f"    desc={l.description!r} qty={l.quantity} price={l.unit_price} total={l.total} conf={l.ocr_confidence:.0f}")

    match_line_items(all_lines, reference)
    print("\nAfter matching:")
    for l in all_lines:
        flag = "NEEDS REVIEW" if l.needs_review else "ok"
        print(f"  [{flag}] '{l.description}' -> code={l.matched_item_code} name={l.matched_item_name} score={l.match_score:.0f}")

    saved = export_to_excel(all_lines, output_path)
    print(f"\nExported to: {saved}")

    assert saved.exists(), "Export file was not created"
    assert len(all_lines) > 0, "No line items were extracted at all"
    print("\nSMOKE TEST PASSED (pipeline ran end-to-end without crashing)")


if __name__ == "__main__":
    main()
