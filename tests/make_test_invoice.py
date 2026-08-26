"""يولّد صورة فاتورة اختبارية بسيطة (نص إنجليزي/أرقام) لاختبار خط الأنابيب آلياً،
بدون الاعتماد على دقة قراءة العربية (التي هي مسؤولية محرك Tesseract نفسه
وليست جزءاً من منطق الأداة الذي نريد اختباره هنا)."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT_DIR = Path(__file__).resolve().parent
OUT_PATH = OUT_DIR / "sample_invoice.png"

ROWS = [
    ("Rice Bag 5kg", "2", "40.00", "80.00"),
    ("Cooking Oil 1L", "5", "12.50", "62.50"),
    ("Sugar 1kg", "10", "5.00", "50.00"),
]


def build():
    img = Image.new("RGB", (900, 400), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 24)
    except OSError:
        font = ImageFont.load_default()

    y = 30
    draw.text((30, y), "Purchase Invoice - Supplier Test Co.", fill="black", font=font)
    y += 60
    header = f"{'Item':<25}{'Qty':<10}{'Price':<10}{'Total':<10}"
    draw.text((30, y), header, fill="black", font=font)
    y += 20
    draw.line((30, y, 850, y), fill="black", width=2)
    y += 20

    for name, qty, price, total in ROWS:
        line = f"{name:<25}{qty:<10}{price:<10}{total:<10}"
        draw.text((30, y), line, fill="black", font=font)
        y += 45

    y += 10
    draw.line((30, y, 850, y), fill="black", width=2)
    y += 20
    draw.text((30, y), "Total Amount: 192.50", fill="black", font=font)

    img.save(OUT_PATH)
    print(f"Saved: {OUT_PATH}")


if __name__ == "__main__":
    build()
