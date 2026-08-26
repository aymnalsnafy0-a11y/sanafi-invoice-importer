"""تحويل صفحات PDF إلى صور، أو تمرير ملف الصورة كما هو."""

from pathlib import Path
from PIL import Image
import pymupdf as fitz

# دقة التحويل (نقطة/بوصة) - كلما زادت تحسنت دقة OCR لكن يبطئ المعالجة
RENDER_DPI = 300

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}


def load_pages_as_images(file_path: str | Path) -> list[Image.Image]:
    """يرجع قائمة صور PIL، صورة واحدة لكل صفحة (أو صورة واحدة إن كان الملف صورة)."""
    file_path = Path(file_path)
    suffix = file_path.suffix.lower()

    if suffix == ".pdf":
        images = []
        doc = fitz.open(file_path)
        zoom = RENDER_DPI / 72
        matrix = fitz.Matrix(zoom, zoom)
        for page in doc:
            pix = page.get_pixmap(matrix=matrix)
            mode = "RGB" if pix.n < 4 else "RGBA"
            img = Image.frombytes(mode, (pix.width, pix.height), pix.samples)
            images.append(img.convert("RGB"))
        doc.close()
        return images

    if suffix in IMAGE_EXTENSIONS:
        return [Image.open(file_path).convert("RGB")]

    raise ValueError(f"صيغة ملف غير مدعومة: {suffix}")
