"""
تصدير بنود الفاتورة بصيغة "ملف قياسي" (.Amn) - نفس صيغة DataSet XML التي
يصدّرها/يستوردها AccSystem نفسه (جدولا DOC_MST_TABLE و DOC_DET_TABLE)،
مبنية على فحص ملف حقيقي مُصدَّر من داخل البرنامج. أسهل للمستخدم النهائي من
الاكسل حسب طلبه، لأن شاشة "استيراد من ملف قياسي" أبسط بالاستخدام.

تنبيه: الحقول غير المتعلقة ببنود الفاتورة (رقم الفاتورة، العميل...) تُترك
بقيم افتراضية آمنة (صفر/فارغة) لأن الاستيراد يتم داخل فاتورة مفتوحة أصلاً
بالبرنامج - نفس منطق استيراد الاكسل تماماً. اختبر النتيجة على فاتورة
تجريبية قبل الاعتماد عليها فعلياً.
"""

from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from line_item import ExtractedLine

# صف عناوين الأعمدة (xs:schema) كما هو بالضبط في ملف حقيقي مُصدَّر من AccSystem
_SCHEMA_XML = """\
  <xs:schema id="NewDataSet" xmlns="" xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:msdata="urn:schemas-microsoft-com:xml-msdata">
    <xs:element name="NewDataSet" msdata:IsDataSet="true" msdata:UseCurrentLocale="true">
      <xs:complexType>
        <xs:choice minOccurs="0" maxOccurs="unbounded">
          <xs:element name="DOC_MST_TABLE">
            <xs:complexType>
              <xs:sequence>
                <xs:element name="DOC_ID" type="xs:long" minOccurs="0" />
                <xs:element name="DOC_ID_PO" type="xs:long" minOccurs="0" />
                <xs:element name="DOC_NO" type="xs:long" minOccurs="0" />
                <xs:element name="USER_ID" type="xs:long" minOccurs="0" />
                <xs:element name="AC_ID" type="xs:long" minOccurs="0" />
                <xs:element name="INV_ID" type="xs:long" minOccurs="0" />
                <xs:element name="ST_ID" type="xs:long" minOccurs="0" />
                <xs:element name="SM_ID" type="xs:long" minOccurs="0" />
                <xs:element name="CE_ID" type="xs:long" minOccurs="0" />
                <xs:element name="COIN_ID" type="xs:long" minOccurs="0" />
                <xs:element name="CUS_ID" type="xs:long" minOccurs="0" />
                <xs:element name="CUS_ARNAME" type="xs:string" minOccurs="0" />
                <xs:element name="DOC_DATE" type="xs:dateTime" minOccurs="0" />
                <xs:element name="DOC_NOTA" type="xs:string" minOccurs="0" />
                <xs:element name="DOC_NOTA_2" type="xs:string" minOccurs="0" />
                <xs:element name="DOC_NOTA_3" type="xs:string" minOccurs="0" />
                <xs:element name="DOC_RE" type="xs:string" minOccurs="0" />
                <xs:element name="DOC_TYPE" type="xs:long" minOccurs="0" />
                <xs:element name="DOC_PAY_FORIGNVALUE" type="xs:double" minOccurs="0" />
                <xs:element name="DOC_SHABAKH_FORIGNVALUE" type="xs:double" minOccurs="0" />
                <xs:element name="DOC_INC_INV_FORIGNVALUE" type="xs:double" minOccurs="0" />
                <xs:element name="DOC_DIS_INV_FORIGNVALUE" type="xs:double" minOccurs="0" />
                <xs:element name="DOC_DIS_CARD_FORIGNVALUE" type="xs:double" minOccurs="0" />
                <xs:element name="DOC_FORIGNVALUE" type="xs:double" minOccurs="0" />
                <xs:element name="DOC_DIS_FORIGNVALUE" type="xs:double" minOccurs="0" />
                <xs:element name="DOC_INC_FORIGNVALUE" type="xs:double" minOccurs="0" />
                <xs:element name="DOC_TOT_FORIGNVALUE" type="xs:double" minOccurs="0" />
                <xs:element name="VI_ID" type="xs:long" minOccurs="0" />
                <xs:element name="DOC_EQ" type="xs:double" minOccurs="0" />
                <xs:element name="DOC_TAX_VAT_TYPE" type="xs:string" minOccurs="0" />
                <xs:element name="DOC_TAX_W_PRICE" type="xs:long" minOccurs="0" />
              </xs:sequence>
            </xs:complexType>
          </xs:element>
          <xs:element name="DOC_DET_TABLE">
            <xs:complexType>
              <xs:sequence>
                <xs:element name="DOC_D_ID" type="xs:long" minOccurs="0" />
                <xs:element name="DOC_TO_ID" type="xs:long" minOccurs="0" />
                <xs:element name="DOC_ID" type="xs:long" minOccurs="0" />
                <xs:element name="DOC__ID_I" type="xs:long" minOccurs="0" />
                <xs:element name="ST_ID" type="xs:long" minOccurs="0" />
                <xs:element name="CLS_ID" type="xs:long" minOccurs="0" />
                <xs:element name="CLS_NO" type="xs:string" minOccurs="0" />
                <xs:element name="CLS_ARNAME" type="xs:string" minOccurs="0" />
                <xs:element name="CLS_ENNAME" type="xs:string" minOccurs="0" />
                <xs:element name="DOC_D_CLS_BARCODE" type="xs:string" minOccurs="0" />
                <xs:element name="DOC_D_UN_ID" type="xs:long" minOccurs="0" />
                <xs:element name="DOC_D_UN_NAME" type="xs:string" minOccurs="0" />
                <xs:element name="CE_ID" type="xs:long" minOccurs="0" />
                <xs:element name="SM_ID_1" type="xs:long" minOccurs="0" />
                <xs:element name="DOC_D_QLT" type="xs:double" minOccurs="0" />
                <xs:element name="DOC_D_QLT_FREE" type="xs:double" minOccurs="0" />
                <xs:element name="DOC_QTY_ALL" type="xs:double" minOccurs="0" />
                <xs:element name="DOC_D_PRICE" type="xs:double" minOccurs="0" />
                <xs:element name="DOC_D_FORIGNVALUE" type="xs:double" minOccurs="0" />
                <xs:element name="DOC_D_DIS1_FORIGNVALUE" type="xs:double" minOccurs="0" />
                <xs:element name="DOC_D_DIS2_FORIGNVALUE" type="xs:double" minOccurs="0" />
                <xs:element name="DOC_D_DIS3_FORIGNVALUE" type="xs:double" minOccurs="0" />
                <xs:element name="DOC_D_INC1_FORIGNVALUE" type="xs:double" minOccurs="0" />
                <xs:element name="DOC_D_INC2_FORIGNVALUE" type="xs:double" minOccurs="0" />
                <xs:element name="DOC_D_INC3_FORIGNVALUE" type="xs:double" minOccurs="0" />
                <xs:element name="DOC_D_DATE_EX" type="xs:dateTime" minOccurs="0" />
                <xs:element name="DOC_D_DATE_PR" type="xs:dateTime" minOccurs="0" />
                <xs:element name="DOC_D_NOTA_1" type="xs:string" minOccurs="0" />
                <xs:element name="DOC_D_PATCH_NO" type="xs:string" minOccurs="0" />
                <xs:element name="DOC_D_IS_COLOR" type="xs:string" minOccurs="0" />
                <xs:element name="DOC_D_IS_SIZE" type="xs:string" minOccurs="0" />
                <xs:element name="DOC_D_IS_SUM" type="xs:long" minOccurs="0" />
                <xs:element name="DOC_D_TAX" type="xs:double" minOccurs="0" />
                <xs:element name="DOC_D_IS_OUT" type="xs:long" minOccurs="0" />
                <xs:element name="DATAMATRIX" type="xs:string" minOccurs="0" />
              </xs:sequence>
            </xs:complexType>
          </xs:element>
        </xs:choice>
      </xs:complexType>
    </xs:element>
  </xs:schema>"""


def _tag(name: str, value) -> str:
    if value is None or value == "":
        return f"    <{name} />"
    return f"    <{name}>{escape(str(value))}</{name}>"


def export_to_amn(lines: list[ExtractedLine], output_path: str | Path) -> Path:
    output_path = Path(output_path)
    now_iso = datetime.now().isoformat()

    grand_total = sum(l.total for l in lines if l.total is not None)

    mst_fields = [
        ("DOC_ID", 1), ("DOC_ID_PO", 1), ("DOC_NO", 1), ("USER_ID", 1),
        ("AC_ID", 0), ("INV_ID", 0), ("ST_ID", 1), ("SM_ID", 0), ("CE_ID", 0),
        ("COIN_ID", 1), ("CUS_ID", 0), ("CUS_ARNAME", ""), ("DOC_DATE", now_iso),
        ("DOC_NOTA", ""), ("DOC_NOTA_2", ""), ("DOC_NOTA_3", ""), ("DOC_RE", ""),
        ("DOC_TYPE", 1), ("DOC_PAY_FORIGNVALUE", grand_total),
        ("DOC_SHABAKH_FORIGNVALUE", 0), ("DOC_INC_INV_FORIGNVALUE", 0),
        ("DOC_DIS_INV_FORIGNVALUE", 0), ("DOC_DIS_CARD_FORIGNVALUE", 0),
        ("DOC_FORIGNVALUE", grand_total), ("DOC_DIS_FORIGNVALUE", 0),
        ("DOC_INC_FORIGNVALUE", 0), ("DOC_TOT_FORIGNVALUE", grand_total),
        ("VI_ID", 0), ("DOC_EQ", 1), ("DOC_TAX_VAT_TYPE", ""), ("DOC_TAX_W_PRICE", 0),
    ]
    mst_block = "  <DOC_MST_TABLE>\n" + "\n".join(_tag(k, v) for k, v in mst_fields) + "\n  </DOC_MST_TABLE>"

    det_blocks = []
    for i, line in enumerate(lines):
        qty = line.quantity or 0
        price = line.unit_price or 0
        total = line.total if line.total is not None else round(qty * price, 2)
        det_fields = [
            ("DOC_D_ID", 0), ("DOC_TO_ID", 0), ("DOC_ID", 1), ("DOC__ID_I", 0),
            ("ST_ID", 0), ("CLS_ID", line.matched_internal_id or 0),
            ("CLS_NO", line.matched_item_code),
            ("CLS_ARNAME", line.matched_item_name or line.description),
            ("CLS_ENNAME", ""), ("DOC_D_CLS_BARCODE", line.barcode),
            ("DOC_D_UN_ID", line.matched_unit_id or 0), ("DOC_D_UN_NAME", line.unit),
            ("CE_ID", 0), ("SM_ID_1", 0), ("DOC_D_QLT", qty), ("DOC_D_QLT_FREE", 0),
            ("DOC_QTY_ALL", qty), ("DOC_D_PRICE", price), ("DOC_D_FORIGNVALUE", total),
            ("DOC_D_DIS1_FORIGNVALUE", 0), ("DOC_D_DIS2_FORIGNVALUE", 0),
            ("DOC_D_DIS3_FORIGNVALUE", 0), ("DOC_D_INC1_FORIGNVALUE", 0),
            ("DOC_D_INC2_FORIGNVALUE", 0), ("DOC_D_INC3_FORIGNVALUE", 0),
            ("DOC_D_DATE_EX", now_iso), ("DOC_D_DATE_PR", now_iso),
            ("DOC_D_NOTA_1", ""), ("DOC_D_PATCH_NO", ""), ("DOC_D_IS_COLOR", ""),
            ("DOC_D_IS_SIZE", ""), ("DOC_D_IS_SUM", 0), ("DOC_D_TAX", 0),
            ("DOC_D_IS_OUT", 1), ("DATAMATRIX", ""),
        ]
        block = "  <DOC_DET_TABLE>\n" + "\n".join(_tag(k, v) for k, v in det_fields) + "\n  </DOC_DET_TABLE>"
        det_blocks.append(block)

    xml_parts = [
        '<?xml version="1.0" standalone="yes"?>',
        "<NewDataSet>",
        _SCHEMA_XML,
        mst_block,
        *det_blocks,
        "</NewDataSet>",
    ]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(xml_parts), encoding="utf-8")
    return output_path
