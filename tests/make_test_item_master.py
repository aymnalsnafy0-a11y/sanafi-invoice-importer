"""يولّد ملف item_master.xlsx اختباري يحاكي تصدير قائمة الأصناف من AccSystem."""

from pathlib import Path

import openpyxl

OUT_PATH = Path(__file__).resolve().parent / "sample_item_master.xlsx"

ITEMS = [
    ("1001", "Rice Bag 5kg", "622001"),
    ("1002", "Cooking Oil 1 Liter", "622002"),
    ("1003", "White Sugar 1kg", "622003"),
    ("1004", "Tea Box 100g", "622004"),
]


def build():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["رقم الصنف", "اسم الصنف", "الباركود"])
    for code, name, barcode in ITEMS:
        ws.append([code, name, barcode])
    wb.save(OUT_PATH)
    print(f"Saved: {OUT_PATH}")


if __name__ == "__main__":
    build()
