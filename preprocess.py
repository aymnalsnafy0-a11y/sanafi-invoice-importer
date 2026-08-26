"""معالجة أولية للصورة لتحسين دقة OCR: تدرج رمادي، تصحيح ميلان، تحسين تباين."""

import cv2
import numpy as np
from PIL import Image


def _pil_to_cv(img: Image.Image) -> np.ndarray:
    return cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)


def _cv_to_pil(mat: np.ndarray) -> Image.Image:
    return Image.fromarray(cv2.cvtColor(mat, cv2.COLOR_BGR2RGB))


def _deskew(gray: np.ndarray) -> np.ndarray:
    """تقدير زاوية الميلان وتصحيحها بناءً على أقل مستطيل محيط بالنص."""
    inverted = cv2.bitwise_not(gray)
    thresh = cv2.threshold(inverted, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)[1]
    coords = np.column_stack(np.where(thresh > 0))
    if coords.shape[0] < 20:
        return gray  # لا يوجد نص كافٍ لتقدير الميلان بثقة

    angle = cv2.minAreaRect(coords)[-1]
    if angle < -45:
        angle = -(90 + angle)
    else:
        angle = -angle

    if abs(angle) < 0.3:
        return gray  # ميلان مهمَل، لا داعي للتدوير

    (h, w) = gray.shape[:2]
    center = (w // 2, h // 2)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    return cv2.warpAffine(
        gray, matrix, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )


def preprocess_for_ocr(img: Image.Image) -> Image.Image:
    """يرجع صورة مُحسَّنة (رمادية، مُصحَّحة الميلان، بتباين محسَّن) جاهزة لـ OCR."""
    mat = _pil_to_cv(img)
    gray = cv2.cvtColor(mat, cv2.COLOR_BGR2GRAY)
    gray = _deskew(gray)

    # تحسين تباين متكيّف (يفيد الصور الملتقطة بالكاميرا بإضاءة غير منتظمة)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)

    denoised = cv2.fastNlMeansDenoising(enhanced, h=10)
    return Image.fromarray(denoised)
