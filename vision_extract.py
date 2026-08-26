"""
استخراج بنود الفاتورة عبر الذكاء الاصطناعي البصري (Claude Vision) بدل OCR
تقليدي. أدق بكثير على الفواتير المزدحمة/متعددة اللغات/الصنف على أكثر من
سطر، لكن يحتاج اتصال إنترنت ومفتاح Anthropic API.
"""

import base64
import io
from dataclasses import dataclass

import anthropic
from PIL import Image
from pydantic import BaseModel

import ai_client
import config
import usage_tracker
from line_item import ExtractedLine


@dataclass
class VisionPageResult:
    items: list[ExtractedLine]
    subtotal_before_tax: float | None = None
    tax_amount: float | None = None
    grand_total_with_tax: float | None = None
    supplier_name: str | None = None


class _VisionLineItem(BaseModel):
    description: str
    barcode: str | None = None
    unit: str | None = None
    quantity: float | None = None
    unit_price: float | None = None
    total: float | None = None


class _VisionExtraction(BaseModel):
    items: list[_VisionLineItem]
    subtotal_before_tax: float | None = None
    tax_amount: float | None = None
    grand_total_with_tax: float | None = None
    supplier_name: str | None = None


EXTRACTION_PROMPT = """\
هذه صورة فاتورة مشتريات (قد تكون بالعربي أو الإنجليزي أو مختلطة). استخرج
فقط بنود الأصناف الفعلية المُشتراة (الأسطر التي تمثل منتجاً/صنفاً بكميته
وسعره) داخل مصفوفة items، وتجاهل من هذي المصفوفة: بيانات العميل والمورد،
رأس/تذييل الفاتورة، أسطر الضريبة والملخص الكلي (Total/VAT/Subtotal)،
ملاحظات الطي أو المسح الضوئي.

بالإضافة لذلك، ابحث عن قسم الملخص/الإجمالي المطبوع (عادة بأسفل الفاتورة أو
آخر صفحة إن كانت متعددة الصفحات) واستخرج منه، إن وُجد، ثلاث قيم منفصلة على
مستوى الفاتورة كاملة (وليس لكل صنف):
- subtotal_before_tax: المجموع قبل الضريبة.
- tax_amount: مبلغ الضريبة (ضريبة القيمة المضافة عادة).
- grand_total_with_tax: الإجمالي النهائي شامل الضريبة.
اجعل أي من الثلاثة null إن لم تجدها مطبوعة بوضوح بهذي الصفحة تحديداً (مثلاً
لو الملخص مطبوع بصفحة ثانية من نفس الفاتورة).

استخرج كمان supplier_name: اسم الشركة/المورد اللي أصدر الفاتورة (البائع،
مو المشتري) - عادة مطبوع بأعلى الفاتورة كترويسة/شعار الشركة. اجعله null لو
ما قدرت تحدده بثقة.

ملاحظات مهمة:
- قد يكون الصنف الواحد مطبوعاً على أكثر من سطر (مثلاً وصف الصنف في سطر،
  وتفاصيل الكمية/السعر/الضريبة في سطر أسفله) — اعتبرهما صنفاً واحداً فقط.
  لا تكرر نفس الصنف كصف منفصل بسبب سطر التفاصيل.
- description: اسم/وصف الصنف. **إن كان اسم الصنف مطبوعاً بلغتين (عربي
  وإنجليزي معاً لنفس الصنف)، استخدم الاسم العربي فقط وتجاهل الإنجليزي.**
  استخدم الإنجليزي فقط إن لم يوجد أي اسم عربي مطبوع لذلك الصنف إطلاقاً.
- barcode: رقم الباركود المطبوع بجانب الصنف إن وُجد عمود باركود (عادة رقم
  طويل 8-14 خانة). اجعله null إن لم يوجد عمود باركود بالفاتورة.
- unit: وحدة القياس كما هي مطبوعة إن وُجد عمود وحدة (مثل "حبة"، "كرتون"،
  "لتر"). اجعله null إن لم يوجد عمود وحدة منفصل.
- quantity: الكمية الفعلية المشتراة لهذا الصنف.
- unit_price: سعر الوحدة كما هو مطبوع.
- total: إجمالي سطر هذا الصنف (الكمية × السعر، كما يظهر في عمود الإجمالي).
- إن لم تستطع قراءة رقم أو حقل بثقة، اجعله null بدل تخمينه.
- إن وُجد قسم عروض/خصومات منفصل (Promotions) يمثل أصنافاً فعلية أُضيفت
  للفاتورة، أدرجها أيضاً كبنود منفصلة.
"""


def _image_to_base64_png(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.standard_b64encode(buf.getvalue()).decode("utf-8")


def extract_line_items_vision(img: Image.Image) -> VisionPageResult:
    """يرسل صورة صفحة الفاتورة إلى Claude ويرجع بنودها + ملخص الضريبة إن وُجد."""
    client = ai_client.get_client()
    image_b64 = _image_to_base64_png(img)

    try:
        response = client.messages.parse(
            model=config.ANTHROPIC_MODEL,
            max_tokens=8000,
            output_config={"effort": "high"},
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": image_b64,
                            },
                        },
                        {"type": "text", "text": EXTRACTION_PROMPT},
                    ],
                }
            ],
            output_format=_VisionExtraction,
        )
    except anthropic.AuthenticationError as exc:
        raise RuntimeError(
            "مفتاح Anthropic API غير صحيح أو منتهي. تأكد من الملف "
            f"{config.ANTHROPIC_API_KEY_FILE.name}."
        ) from exc
    except anthropic.APIConnectionError as exc:
        raise RuntimeError("تعذّر الاتصال بالإنترنت للوصول إلى خدمة الذكاء الاصطناعي.") from exc
    except anthropic.RateLimitError as exc:
        raise RuntimeError("تم تجاوز حد الاستخدام المسموح لحساب Anthropic حالياً. حاول بعد قليل.") from exc
    except anthropic.APIError as exc:
        # أي خطأ ثاني من خدمة Anthropic (خطأ سيرفر، صورة مرفوضة، إلخ) - نعرضه
        # برسالة عربية عامة بدل ما يوصل للمستخدم كنص تقني إنجليزي خام
        raise RuntimeError(f"خطأ من خدمة الذكاء الاصطناعي: {exc}") from exc

    if response.usage is not None:
        usage_tracker.record_usage(response.usage.input_tokens, response.usage.output_tokens)

    parsed = response.parsed_output
    results = []
    for item in parsed.items:
        if not item.description.strip():
            continue
        results.append(
            ExtractedLine(
                raw_text=item.description,
                description=item.description.strip(),
                quantity=item.quantity,
                unit_price=item.unit_price,
                total=item.total,
                ocr_confidence=100.0,
                barcode=(item.barcode or "").strip(),
                unit=(item.unit or "").strip(),
            )
        )
    return VisionPageResult(
        items=results,
        subtotal_before_tax=parsed.subtotal_before_tax,
        tax_amount=parsed.tax_amount,
        grand_total_with_tax=parsed.grand_total_with_tax,
        supplier_name=(parsed.supplier_name or "").strip() or None,
    )
