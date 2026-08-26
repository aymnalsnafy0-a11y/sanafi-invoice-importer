"""
أداة استيراد فاتورة مشتريات من PDF/صورة - الواجهة الرسومية.

سير العمل:
1) اختيار ملف الفاتورة (PDF أو صورة).
2) اختيار ملف قائمة الأصناف المرجعي (مُصدَّر من AccSystem).
3) استخراج البنود (OCR + مطابقة أصناف تلقائية).
4) مراجعة/تعديل الجدول يدوياً (الكهرماني = صنف غير موجود بقاعدتكم، الأخضر = مطابَق).
5) تصدير النتيجة إلى ملف اكسل، ثم استيراده داخل AccSystem بالزر الموجود أصلاً.
"""

import json
import re
import threading
import tkinter as tk
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import ai_client
import config
import learned_matches
import matching_engine
import settings as settings_module
import usage_tracker
from amn_exporter import export_to_amn
from exporter import export_to_excel
from items import load_item_reference, match_line_items
from line_item import ExtractedLine
from pdf_utils import load_pages_as_images
from vision_extract import extract_line_items_vision

# ---------- أدوات الشات (tool calling) - نفس فكرة get_field/edit_field/
# search_in_field اللي وصفها المبرمج، بس مطبَّقة على جدول المراجعة عندنا
# مباشرة بدل شاشة الشامل. الذكاء الاصطناعي يقرر يستدعي أي منها، والتنفيذ
# الفعلي يمر عبر _apply_edit_to_model نفسها (نفس دالة التعديل اليدوي)
# عشان كل فحوصات الأمان/الصحة اللي فيها (رفض nan/inf، منع رقم صنف يتيم...)
# تنطبق تلقائياً على تعديلات الذكاء الاصطناعي كمان.
_CHAT_SYSTEM_PROMPT = """\
أنت مساعد داخل أداة "سنافي" لمراجعة بنود فاتورة مشتريات مستخرجة من صورة/PDF.
عندك أدوات تقرأ وتعدّل جدول المراجعة مباشرة. استخدمها لتنفيذ طلب المستخدم
فعلياً، مو بس تشرح كيف يسويه بنفسه. لو الطلب غامض (مثلاً يذكر صنف بالاسم
بدون تحديد رقم صف)، استخدم list_invoice_lines أو search_catalog_items أول
شي عشان تتأكد أي صف/صنف بالضبط يقصد قبل التعديل. رد نهائي دايماً بالعربي
وبإيجاز، قل وش سويت بالضبط."""

_CHAT_TOOLS = [
    {
        "name": "list_invoice_lines",
        "description": "يرجع كل بنود جدول الفاتورة المعروض حالياً، مع رقم كل صف (index) - استخدمه أول شي لمعرفة أرقام الصفوف قبل أي تعديل أو حذف.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "edit_invoice_line",
        "description": "يعدّل قيمة حقل واحد بصف معيّن من جدول الفاتورة.",
        "input_schema": {
            "type": "object",
            "properties": {
                "index": {"type": "integer", "description": "رقم الصف (index) من list_invoice_lines"},
                "field": {
                    "type": "string",
                    "enum": ["barcode", "code", "matched_name", "unit", "qty", "price", "total"],
                    "description": "code=رقم الصنف, matched_name=اسم الصنف, qty=الكمية, price=سعر الوحدة",
                },
                "value": {"type": "string", "description": "القيمة الجديدة"},
            },
            "required": ["index", "field", "value"],
        },
    },
    {
        "name": "search_catalog_items",
        "description": "يبحث عن صنف بقائمة أصناف قاعدة البيانات المحمَّلة، بالاسم أو رقم الصنف أو الباركود (تطابق جزئي) - يفيد لما المستخدم يطلب صنف بالاسم وتحتاج رقمه الصحيح.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "جزء من الاسم أو رقم الصنف أو الباركود"}},
            "required": ["query"],
        },
    },
    {
        "name": "delete_invoice_line",
        "description": "يحذف صف كامل من جدول الفاتورة.",
        "input_schema": {
            "type": "object",
            "properties": {"index": {"type": "integer", "description": "رقم الصف (index) من list_invoice_lines"}},
            "required": ["index"],
        },
    },
]


@dataclass
class _BatchInvoice:
    """حالة فاتورة واحدة داخل دفعة فواتير مُختارة معاً - كل فاتورة تحتفظ
    ببنودها وملخص ضريبتها لوحدها، عشان التنقل بينها بالمراجعة/التصدير
    ما يخلط بيانات فاتورة بفاتورة ثانية."""

    path: Path
    lines: list[ExtractedLine] = field(default_factory=list)
    subtotal_before_tax: float | None = None
    tax_amount: float | None = None
    grand_total_with_tax: float | None = None
    supplier_name: str | None = None
    error: str | None = None  # لو فشل استخراج هذي الفاتورة بالذات، بدون ما يوقف باقي الدفعة
    extracted: bool = False  # نجح استخراجها من قبل - لا تُعاد (توفير تكلفة API) إلا لو فشلت

COLUMNS = (
    "num",
    "barcode",
    "code",
    "matched_name",
    "unit",
    "qty",
    "price",
    "total",
)
HEADINGS = {
    "num": "#",
    "barcode": "الباركود",
    "code": "رقم الصنف",
    "matched_name": "اسم الصنف",
    "unit": "الوحدة",
    "qty": "الكمية",
    "price": "سعر الوحدة",
    "total": "الإجمالي",
}
# كل هذه الأعمدة قابلة للتعديل بالنقر المزدوج، وتُحدَّث في self.lines مباشرة
_EDITABLE_FIELD_BY_COLUMN = {
    "barcode": "barcode",
    "code": "matched_item_code",
    "matched_name": "matched_item_name",
    "unit": "unit",
    "desc": "description",
}
_NUMERIC_FIELD_BY_COLUMN = {"qty": "quantity", "price": "unit_price", "total": "total"}


_TASHKEEL_RE = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭـ]")
_ALEF_VARIANTS_RE = re.compile(r"[إأآٱ]")


def _normalize_arabic(text: str) -> str:
    """توحيد محافظ للنص العربي قبل مقارنة حرفية - يوحّد أ/إ/آ/ٱ لألف عادية
    ويوحّد ى لياء، يزيل التشكيل والتطويل، ويجمع المسافات المتكررة. لا يوسّع
    ة/ه (فرق حقيقي بالمعنى، مو مجرد اختلاف إملائي - عمداً غير مطلوب هنا)."""
    if not text:
        return ""
    text = _TASHKEEL_RE.sub("", text)
    text = _ALEF_VARIANTS_RE.sub("ا", text)
    text = text.replace("ى", "ي")
    text = re.sub(r"\s+", " ", text).strip()
    return text.casefold()


def _search_reference_items(reference: list, query: str, limit: int = 8) -> list[tuple[float, object]]:
    """بحث موحّد بقائمة الأصناف المرجعية بترتيب أولوية صريح (تراتبي، مو مجرد
    أعلى score): باركود تام -> رقم صنف تام -> باركود/رقم صنف يبدأ به أو
    يحتويه -> اسم مطابق تماماً (بعد توحيد عربي محافظ) -> اسم يبدأ بالاستعلام
    -> اسم يحتويه -> أخيراً fuzzy كـfallback بس لما ما فيه أي تطابق حرفي.
    أي تطابق حرفي بالاسم يسبق أي نتيجة fuzzy-only بغض النظر عن رقم التشابه
    الضبابي (مشكلة حقيقية: "ريتا عصير برتقال..." اسم طويل حقيقي كان ينزل
    تحت نتائج غير ذات صلة إطلاقاً لأن token_sort_ratio يعاقب الأسماء
    الطويلة نسبةً لاستعلام قصير)."""
    query = (query or "").strip()
    if not query or not reference:
        return []
    from rapidfuzz import fuzz

    query_norm = _normalize_arabic(query)

    TIER_EXACT_BARCODE = 0
    TIER_EXACT_CODE = 1
    TIER_CODE_BARCODE_PARTIAL = 2
    TIER_NAME_EXACT = 3
    TIER_NAME_STARTSWITH = 4
    TIER_NAME_CONTAINS = 5
    TIER_FUZZY = 6

    ranked = []
    for item in reference:
        code = item.code or ""
        barcode = item.barcode or ""
        name_norm = _normalize_arabic(item.name or "")

        if barcode and query == barcode:
            ranked.append((TIER_EXACT_BARCODE, 100.0, item))
            continue
        if code and query == code:
            ranked.append((TIER_EXACT_CODE, 100.0, item))
            continue
        if (barcode and query in barcode) or (code and query in code):
            ranked.append((TIER_CODE_BARCODE_PARTIAL, 90.0, item))
            continue
        if name_norm and query_norm and query_norm == name_norm:
            ranked.append((TIER_NAME_EXACT, 95.0, item))
            continue
        if name_norm and query_norm and name_norm.startswith(query_norm):
            ranked.append((TIER_NAME_STARTSWITH, 85.0, item))
            continue
        if name_norm and query_norm and query_norm in name_norm:
            ranked.append((TIER_NAME_CONTAINS, 75.0, item))
            continue
        fuzzy_score = fuzz.token_sort_ratio(query_norm, name_norm) if name_norm else 0.0
        ranked.append((TIER_FUZZY, fuzzy_score, item))

    ranked.sort(key=lambda t: (t[0], -t[1]))
    return [(score, item) for _tier, score, item in ranked[:limit]]


def _plain_confidence_label(confidence: float) -> str:
    """وصف بالعربي البسيط بدل نسبة مئوية خام - قرار صريح من المستخدم
    (اقتراحات المراجعة كانت "مو مفهومة تمام" برأيه) - يوسّع نفس مبدأ
    "بدون نسبة تشابه مربكة" المطبَّق سابقاً بنتائج البحث اليدوي."""
    if confidence >= 90:
        return "✓ مطابقة قوية جداً"
    if confidence >= 70:
        return "قريب جداً"
    if confidence >= 50:
        return "احتمال جيد"
    return "احتمال ضعيف"


def _extract_warning_notes(reason: str) -> str:
    """يستخرج بس أجزاء التحذير (⚠) من نص السبب التقني الكامل - يخفي تفاصيل
    داخلية (نسب فرعية، أسماء فحوصات مثل "الوحدة الصريحة متطابقة") عن مستخدم
    غير تقني، ويبقي بس اللي فعلاً يستاهل انتباهه قبل ما يضغط اختيار."""
    parts = [p.strip() for p in (reason or "").split("،")]
    warnings = [p for p in parts if p.startswith("⚠")]
    return "  ".join(warnings)


class InvoiceImporterApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("سنافي")
        self.geometry("1150x650")
        icon_path = config.BASE_DIR / "app_icon.ico"
        if icon_path.exists():
            self.iconbitmap(str(icon_path))

        self.batch: list[_BatchInvoice] = []
        self.batch_index: int = 0
        self.items_ref_path: Path = config.DEFAULT_ITEMS_REFERENCE_PATH
        self.live_items_reference: list | None = None  # من قاعدة البيانات، إن وُجدت
        self.lines: list[ExtractedLine] = []
        self.last_reference: list = []
        self.reference_attrs_index: dict = {}
        self.invoice_subtotal_before_tax: float | None = None
        self.invoice_tax_amount: float | None = None
        self.invoice_grand_total_with_tax: float | None = None
        self.invoice_supplier_name: str | None = None
        self.chat_history: list = []
        self._chat_window: tk.Toplevel | None = None

        self._build_ui()
        # تحميل قائمة الأصناف من قاعدة البيانات تلقائياً من أول فتح للبرنامج
        # (بدون انتظار ضغط الزر يدوياً - يمنع نسيان تحديثها قبل الاستخراج)
        self._refresh_items_from_db()

    # ---------- UI ----------
    def _build_ui(self):
        # مظهر جدول أوضح (خطوط فاصلة بين الأعمدة والصفوف) - يحتاج ثيم "clam"
        # (المدمج دايماً بـTcl/Tk القياسي، بدون أي مكتبة إضافية) عشان يدعم
        # حدود العناوين؛ لو الثيم غير متوفر لأي سبب، نتجاهل بصمت والجدول
        # يشتغل عادي بس بدون هذا التحسين البصري بالذات
        try:
            style = ttk.Style(self)
            style.theme_use("clam")
            style.configure("Treeview", rowheight=24, borderwidth=1, relief="solid")
            style.configure("Treeview.Heading", borderwidth=1, relief="raised")
        except tk.TclError:
            pass

        # ثلاث صفوف منفصلة بدل صف واحد - عدد الأزرار زاد كثير مع الوقت وصار
        # ما ينضغط بعرض شاشات أصغر من شاشة التطوير (كانت أزرار تختفي برّه
        # حدود النافذة بصمت بدون أي رسالة خطأ، لأن pack() ما يلف الأزرار
        # لسطر جديد تلقائياً). كل صف مجموعة مرتبطة، عشان حتى لو صف طال شوي
        # ما يأثر على باقي الأزرار المهمة بصفوف ثانية:
        # 1) خطوات العمل الأساسية المرقّمة (1، 2، 3)
        # 2) أدوات قاعدة البيانات
        # 3) الذكاء الاصطناعي/الإعدادات/الشات
        top = tk.Frame(self)
        top.pack(fill="x", padx=8, pady=(8, 2))

        top_row1 = tk.Frame(top)
        top_row1.pack(fill="x")
        top_row2 = tk.Frame(top)
        top_row2.pack(fill="x", pady=(4, 0))
        top_row3 = tk.Frame(top)
        top_row3.pack(fill="x", pady=(4, 0))

        tk.Button(top_row1, text="1) اختر فاتورة أو عدة فواتير (PDF / صورة)", command=self._pick_invoice).pack(
            side="right", padx=4
        )
        self.invoice_label = tk.Label(top_row1, text="لم يتم اختيار ملف", fg="gray")
        self.invoice_label.pack(side="right", padx=4)

        tk.Button(top_row1, text="2) اختر ملف الأصناف المرجعي", command=self._pick_items_ref).pack(
            side="right", padx=4
        )
        self.items_label = tk.Label(top_row1, text=str(self.items_ref_path.name), fg="gray")
        self.items_label.pack(side="right", padx=4)

        self.extract_button = tk.Button(
            top_row1, text="3) استخراج", command=self._run_extraction, bg="#2e7d32", fg="white"
        )
        self.extract_button.pack(side="right", padx=12)

        self.refresh_db_button = tk.Button(
            top_row2,
            text="تحديث الأصناف من قاعدة البيانات مباشرة",
            command=self._refresh_items_from_db,
            bg="#6a1b9a",
            fg="white",
        )
        self.refresh_db_button.pack(side="right", padx=4)

        tk.Button(
            top_row2,
            text="⚙ عنوان سيرفر قاعدة البيانات",
            command=self._prompt_server_address,
        ).pack(side="right", padx=4)

        self.ai_budget_button = tk.Button(
            top_row3,
            text="💰 رصيد الذكاء الاصطناعي",
            command=self._open_ai_budget_window,
        )
        self.ai_budget_button.pack(side="right", padx=4)

        tk.Button(
            top_row3,
            text="⚙ إعدادات المطابقة",
            command=self._open_matching_settings_window,
        ).pack(side="right", padx=4)

        tk.Button(
            top_row3,
            text="💬 مساعد سنافي",
            command=self._open_chat_window,
            bg="#4527a0",
            fg="white",
        ).pack(side="left", padx=4)

        self.status_label = tk.Label(self, text="", fg="blue")
        self.status_label.pack(fill="x", padx=8)

        # شريط التنقل بين فواتير الدفعة - يبان بس لما تختار أكثر من ملف
        nav = tk.Frame(self)
        nav.pack(fill="x", padx=8)
        self.batch_prev_button = tk.Button(
            nav, text="◀ السابقة", command=self._go_prev_invoice, state="disabled"
        )
        self.batch_prev_button.pack(side="right", padx=4)
        self.batch_position_label = tk.Label(nav, text="", fg="black", font=("Arial", 9, "bold"))
        self.batch_position_label.pack(side="right", padx=8)
        self.batch_next_button = tk.Button(
            nav, text="التالية ▶", command=self._go_next_invoice, state="disabled"
        )
        self.batch_next_button.pack(side="right", padx=4)
        self.remove_invoice_button = tk.Button(
            nav,
            text="✕ إزالة هذي الفاتورة (اخترتها غلط)",
            command=self._remove_current_invoice_from_batch,
            fg="#b71c1c",
            state="disabled",
        )
        self.remove_invoice_button.pack(side="left", padx=4)

        # جدول واحد يبان بالوقت الواحد، وزرا تبديل فوقه يختارون أي قسم -
        # بدل الجدولين المرصوصين فوق بعض سابقاً. أزرار التبديل نفسها تبين
        # عدد كل قسم فوراً بدون الحاجة نفتحه.
        toggle_bar = tk.Frame(self)
        toggle_bar.pack(fill="x", padx=8, pady=(8, 0))
        self.show_matched_button = tk.Button(
            toggle_bar, text="أصناف مطابَقة (0)", command=lambda: self._show_panel("matched")
        )
        self.show_matched_button.pack(side="right", padx=4)
        self.show_unmatched_button = tk.Button(
            toggle_bar, text="أصناف غير موجودة (0)", command=lambda: self._show_panel("unmatched")
        )
        self.show_unmatched_button.pack(side="right", padx=4)

        tables_container = tk.Frame(self)
        tables_container.pack(fill="both", expand=True, padx=8, pady=8)

        unmatched_section = tk.Frame(tables_container)
        self.unmatched_label = tk.Label(
            unmatched_section, text="أصناف غير موجودة بالقاعدة (0)", fg="#e65100", font=("Arial", 10, "bold")
        )
        self.unmatched_label.pack(fill="x")
        unmatched_frame = tk.Frame(unmatched_section)
        unmatched_frame.pack(fill="both", expand=True)
        self.tree_unmatched = self._build_tree(unmatched_frame)

        matched_section = tk.Frame(tables_container)
        self.matched_label = tk.Label(
            matched_section, text="أصناف مطابَقة (0)", fg="#1b5e20", font=("Arial", 10, "bold")
        )
        self.matched_label.pack(fill="x")
        matched_frame = tk.Frame(matched_section)
        matched_frame.pack(fill="both", expand=True)
        self.tree_matched = self._build_tree(matched_frame)

        self._unmatched_section = unmatched_section
        self._matched_section = matched_section
        self._active_panel = "unmatched"
        self._show_panel("unmatched")

        self.tree_unmatched.bind("<<TreeviewSelect>>", self._on_row_selected)
        self.tree_matched.bind("<<TreeviewSelect>>", self._on_row_selected)

        bottom = tk.Frame(self)
        bottom.pack(fill="x", padx=8, pady=8)
        self._bottom_frame = bottom
        tk.Button(bottom, text="حذف الصف المحدد", command=self._delete_selected_row).pack(side="right", padx=4)
        self.grand_total_label = tk.Label(bottom, text="", font=("Arial", 11, "bold"))
        self.grand_total_label.pack(side="right", padx=12)
        tk.Button(
            bottom, text="4) تصدير إلى اكسل للاستيراد في AccSystem", command=self._export, bg="#1565c0", fg="white"
        ).pack(side="left", padx=4)
        tk.Button(
            bottom,
            text="تصدير إلى ملف قياسي للاستيراد في AccSystem",
            command=self._export_amn,
            bg="#00695c",
            fg="white",
        ).pack(side="left", padx=4)

    def _show_panel(self, panel: str):
        """يبدّل أي جدول يبان: 'matched' أو 'unmatched'، بس واحد بالوقت."""
        self._active_panel = panel
        if panel == "unmatched":
            self._matched_section.pack_forget()
            self._unmatched_section.pack(fill="both", expand=True)
        else:
            self._unmatched_section.pack_forget()
            self._matched_section.pack(fill="both", expand=True)
        self.show_unmatched_button.config(relief=("sunken" if panel == "unmatched" else "raised"))
        self.show_matched_button.config(relief=("sunken" if panel == "matched" else "raised"))

    def _build_tree(self, parent: tk.Frame) -> ttk.Treeview:
        tree = ttk.Treeview(parent, columns=COLUMNS, show="headings", selectmode="browse", height=6)
        for col in COLUMNS:
            tree.heading(col, text=HEADINGS[col])
            tree.column(col, width=130, anchor="e")
        tree.column("matched_name", width=220)
        tree.column("num", width=40, anchor="center")

        vsb = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side="right", fill="both", expand=True)
        vsb.pack(side="left", fill="y")

        tree.tag_configure("matched", background="#c8e6c9")
        tree.tag_configure("matched_alt", background="#b2d8b3")
        tree.tag_configure("not_in_catalog", background="#ffe0b2")
        tree.tag_configure("not_in_catalog_alt", background="#ffd299")
        tree.bind("<Double-1>", self._on_cell_double_click)
        tree.bind("<Button-3>", self._on_tree_right_click)
        tree.bind("<Control-c>", self._copy_selected_row)
        return tree

    # ---------- Actions ----------
    def _pick_invoice(self):
        """يفتح حوار اختيار ملف/ملفات ويضيفها للدفعة الحالية (مو يستبدلها) -
        عشان اختيار ملف ثاني بعد الأول يوسّع الدفعة بدل ما يمسح الأول."""
        paths = filedialog.askopenfilenames(
            title="اختر فاتورة أو عدة فواتير",
            filetypes=[("PDF أو صورة", "*.pdf *.png *.jpg *.jpeg *.bmp *.tif *.tiff")],
        )
        if not paths:
            return

        existing_paths = {str(inv.path).casefold() for inv in self.batch}
        new_paths = [p for p in paths if str(Path(p)).casefold() not in existing_paths]
        if not new_paths:
            messagebox.showinfo("تنبيه", "كل الملفات المختارة مضافة للدفعة مسبقاً.")
            return

        first_new_index = len(self.batch)
        self.batch.extend(_BatchInvoice(path=Path(p)) for p in new_paths)

        if len(self.batch) == 1:
            self.invoice_label.config(text=Path(new_paths[0]).name, fg="black")
        else:
            self.invoice_label.config(text=f"{len(self.batch)} ملفات بالدفعة", fg="black")

        # ننتقل مباشرة لأول فاتورة انضافت جديدة - self.lines وقتها فاضية
        # (فاتورة ما استُخرجت بعد)، فما فيه شي نخسره بتجاهل حفظ الحالة الحالية
        self._show_batch_index_fresh(first_new_index)

    def _pick_items_ref(self):
        path = filedialog.askopenfilename(
            title="اختر ملف قائمة الأصناف (مُصدَّر من AccSystem)",
            filetypes=[("Excel أو ملف قياسي", "*.xlsx *.AmnC *.xml"), ("الكل", "*.*")],
        )
        if path:
            self.items_ref_path = Path(path)
            self.items_label.config(text=self.items_ref_path.name, fg="black")
            self.live_items_reference = None  # رجوع لاعتماد الملف بدل قاعدة البيانات

    def _prompt_server_address(self):
        """نافذة صغيرة لتحديد عنوان سيرفر قاعدة بيانات Oracle الخاص بهذا الجهاز.
        مهم للعملاء اللي عندهم سيرفر مركزي والموظفين يشتغلون من طرفيات (أجهزة)
        بعيدة عن السيرفر - في هالحالة "localhost" غلط، ولازم عنوان السيرفر
        الحقيقي (رقم IP أو اسم الجهاز) بالشبكة الداخلية. اسأل مسؤول الشبكة
        عند العميل أو تحقق من إعدادات الشبكة بجهاز السيرفر نفسه لو ما تعرفه."""
        dialog = tk.Toplevel(self)
        dialog.title("عنوان سيرفر قاعدة البيانات")
        dialog.transient(self)
        dialog.grab_set()

        tk.Label(
            dialog,
            text=(
                "عنوان قاعدة بيانات Oracle لهذا الجهاز تحديداً (host:port/service_name).\n"
                "اتركه كما هو (localhost) لو الأداة تشتغل على نفس جهاز السيرفر.\n"
                "لو تشتغل من طرفية/جهاز موظف بعيد عن السيرفر، اكتب عنوان السيرفر\n"
                "الحقيقي، مثال: 192.168.1.10:1521/orcl"
            ),
            justify="right",
            padx=12,
            pady=8,
        ).pack()

        entry = tk.Entry(dialog, width=40, justify="left")
        entry.insert(0, config.get_dsn())
        entry.pack(padx=12, pady=4)

        def save_and_close():
            new_dsn = entry.get().strip()
            if not new_dsn:
                return
            config.save_dsn(new_dsn)
            dialog.destroy()
            self._refresh_items_from_db()

        tk.Button(dialog, text="حفظ وإعادة الاتصال", command=save_and_close, bg="#6a1b9a", fg="white").pack(
            pady=8
        )

    def _open_ai_budget_window(self):
        """نافذة رصيد الذكاء الاصطناعي: يعرض تقدير للمصروف والمتبقي (محسوب
        محلياً من بيانات usage الحقيقية لكل استدعاء - Anthropic ما توفر
        endpoint لجلب الرصيد الفعلي بمفتاح API عادي)، وزر لإيقاف/تشغيل
        استخدام الذكاء الاصطناعي بالأداة كاملة (الاستخراج + الشات)."""
        dialog = tk.Toplevel(self)
        dialog.title("رصيد الذكاء الاصطناعي")
        dialog.transient(self)
        dialog.grab_set()
        dialog.geometry("360x300")

        info_frame = tk.Frame(dialog)
        info_frame.pack(fill="x", padx=16, pady=(16, 8))

        spent = usage_tracker.get_total_spent()
        starting = usage_tracker.get_starting_balance()

        tk.Label(info_frame, text=f"إجمالي المصروف حتى الآن: ${spent:,.4f}", font=("Arial", 11)).pack(
            anchor="e", pady=2
        )

        remaining_label = tk.Label(info_frame, font=("Arial", 12, "bold"))
        remaining_label.pack(anchor="e", pady=6)

        def refresh_remaining_label():
            bal = usage_tracker.get_remaining_balance()
            if bal is None:
                remaining_label.config(text="حدّد رصيدك الأساسي بالأسفل لمعرفة المتبقي", fg="#6b6055")
            elif bal < 0.5:
                remaining_label.config(text=f"المتبقي التقريبي: ${bal:,.2f} ⚠ قريب ينفد", fg="#c62828")
            else:
                remaining_label.config(text=f"المتبقي التقريبي: ${bal:,.2f}", fg="#1b5e20")

        refresh_remaining_label()

        tk.Label(
            info_frame,
            text="* تقدير محلي من استخدام هذا الجهاز فقط - مو رصيد حقيقي مباشر من Anthropic",
            font=("Arial", 8),
            fg="#6b6055",
            wraplength=320,
            justify="right",
        ).pack(anchor="e", pady=(0, 8))

        balance_frame = tk.Frame(dialog)
        balance_frame.pack(fill="x", padx=16, pady=4)
        tk.Label(balance_frame, text="رصيدك الأساسي بحساب Anthropic ($):").pack(anchor="e")
        balance_entry = tk.Entry(balance_frame, justify="left")
        if starting is not None:
            balance_entry.insert(0, str(starting))
        balance_entry.pack(fill="x", pady=4)

        def save_balance():
            try:
                amount = float(balance_entry.get().strip())
            except ValueError:
                messagebox.showwarning("قيمة غير صحيحة", "اكتب رقم صحيح (مثال: 5 أو 5.5)")
                return
            usage_tracker.set_starting_balance(amount)
            refresh_remaining_label()

        tk.Button(balance_frame, text="حفظ الرصيد", command=save_balance, bg="#6a1b9a", fg="white").pack(pady=4)

        ttk.Separator(dialog, orient="horizontal").pack(fill="x", padx=16, pady=8)

        toggle_frame = tk.Frame(dialog)
        toggle_frame.pack(fill="x", padx=16, pady=4)

        def render_toggle_button():
            enabled = usage_tracker.is_ai_enabled()
            if enabled:
                toggle_button.config(text="⏸ إيقاف الذكاء الاصطناعي مؤقتاً", bg="#c62828")
            else:
                toggle_button.config(text="▶ تشغيل الذكاء الاصطناعي", bg="#2e7d32")

        def toggle_ai():
            usage_tracker.set_ai_enabled(not usage_tracker.is_ai_enabled())
            render_toggle_button()

        toggle_button = tk.Button(toggle_frame, command=toggle_ai, fg="white", font=("Arial", 10, "bold"))
        toggle_button.pack(fill="x", pady=4)
        render_toggle_button()

        tk.Label(
            toggle_frame,
            text="لما يكون متوقف، زر الاستخراج ومساعد الشات ما يشتغلون إطلاقاً - بدون أي مصروف إضافي.",
            font=("Arial", 8),
            fg="#6b6055",
            wraplength=320,
            justify="right",
        ).pack(anchor="e", pady=(4, 0))

    def _open_matching_settings_window(self):
        """نافذة إعدادات محرك المطابقة الذكي: وضع المراجعة (أثناء القراءة/بعد
        الاستخراج)، عتبتا الثقة، وهل التعديل اليدوي يُغذّي جدول التعلّم."""
        current = settings_module.get_settings()

        dialog = tk.Toplevel(self)
        dialog.title("إعدادات المطابقة")
        dialog.transient(self)
        dialog.grab_set()
        dialog.geometry("380x420")

        tk.Label(dialog, text="وضع مراجعة الأصناف غير المؤكدة:", font=("Arial", 10, "bold")).pack(
            anchor="e", padx=16, pady=(16, 4)
        )
        review_mode_var = tk.StringVar(value=current["review_mode"])
        tk.Radiobutton(
            dialog,
            text="أثناء القراءة - توقف عند كل صنف غامض وراجعه فوراً",
            variable=review_mode_var,
            value="during_reading",
            justify="right",
            anchor="e",
        ).pack(fill="x", padx=24)
        tk.Radiobutton(
            dialog,
            text="بعد الاستخراج - خلّص الفاتورة كاملة، راجع الغامض بعدين بالجدول",
            variable=review_mode_var,
            value="after_extraction",
            justify="right",
            anchor="e",
        ).pack(fill="x", padx=24)

        ttk.Separator(dialog, orient="horizontal").pack(fill="x", padx=16, pady=12)

        tk.Label(dialog, text="عتبة القبول التلقائي (%) - فوقها يتقبل الصنف تلقائياً بدون مراجعة:", wraplength=340, justify="right").pack(
            anchor="e", padx=16
        )
        auto_accept_entry = tk.Entry(dialog, justify="left")
        auto_accept_entry.insert(0, str(current["auto_accept_threshold"]))
        auto_accept_entry.pack(fill="x", padx=16, pady=(2, 10))

        tk.Label(dialog, text="عتبة اقتراح المراجعة (%) - تحتها الصنف يُعتبر غامض جداً ولا يُقترح أصلاً:", wraplength=340, justify="right").pack(
            anchor="e", padx=16
        )
        needs_review_entry = tk.Entry(dialog, justify="left")
        needs_review_entry.insert(0, str(current["needs_review_threshold"]))
        needs_review_entry.pack(fill="x", padx=16, pady=(2, 10))

        ttk.Separator(dialog, orient="horizontal").pack(fill="x", padx=16, pady=12)

        auto_learn_var = tk.BooleanVar(value=current["auto_learn_from_manual_edits"])
        tk.Checkbutton(
            dialog,
            text="تعلّم تلقائياً من كل تعديل يدوي ناجح لرقم الصنف/الباركود",
            variable=auto_learn_var,
            justify="right",
            anchor="e",
        ).pack(fill="x", padx=16, pady=4)

        status_label = tk.Label(dialog, text="", fg="#c62828")
        status_label.pack(fill="x", padx=16, pady=(4, 0))

        def save_and_close():
            try:
                auto_accept = float(auto_accept_entry.get().strip())
                needs_review = float(needs_review_entry.get().strip())
            except ValueError:
                status_label.config(text="اكتب رقماً صحيحاً بالعتبتين (مثال: 95 أو 70).")
                return
            if not (0 <= needs_review <= auto_accept <= 100):
                status_label.config(text="لازم: 0 ≤ عتبة المراجعة ≤ عتبة القبول التلقائي ≤ 100.")
                return
            settings_module.update_settings(
                review_mode=review_mode_var.get(),
                auto_accept_threshold=auto_accept,
                needs_review_threshold=needs_review,
                auto_learn_from_manual_edits=auto_learn_var.get(),
            )
            dialog.destroy()

        tk.Button(dialog, text="حفظ", command=save_and_close, bg="#2e7d32", fg="white").pack(pady=16)

    def _refresh_items_from_db(self):
        # منع تشغيل أكثر من عملية تحديث بنفس الوقت - ضغط الزر مرتين بسرعة كان
        # يشغّل خيطين متوازيين يتسابقين على تحديث نفس البيانات
        if str(self.refresh_db_button["state"]) == "disabled":
            return
        self.refresh_db_button.config(state="disabled")
        self.status_label.config(text="جاري الاتصال بقاعدة بيانات AccSystem...")
        self.update_idletasks()
        threading.Thread(target=self._list_schemas_worker, daemon=True).start()

    def _list_schemas_worker(self):
        from db_items import DbConnectionError, list_company_schemas

        try:
            schemas = list_company_schemas()
        except DbConnectionError as exc:
            error_message = (
                str(exc)
                + "\n\nلو تشتغل من جهاز موظف (طرفية) بعيد عن سيرفر قاعدة البيانات، "
                "جرّب زر \"⚙ عنوان سيرفر قاعدة البيانات\" وحدد عنوان السيرفر الصحيح."
            )
            self.after(0, lambda: messagebox.showerror("تعذّر الاتصال بقاعدة البيانات", error_message))
            self.after(0, self._finish_db_refresh)
            return
        except Exception as exc:  # noqa: BLE001 - أي خطأ غير متوقع لازم يبان للمستخدم، مو يجمّد الشاشة بصمت
            error_message = str(exc)
            self.after(0, lambda: messagebox.showerror("خطأ غير متوقع", error_message))
            self.after(0, self._finish_db_refresh)
            return

        if not schemas:
            self.after(
                0,
                lambda: messagebox.showerror(
                    "لا توجد شركات", "لم يتم إيجاد أي ملف/شركة (schema) بنمط SAL_سنة_رقم بقاعدة البيانات."
                ),
            )
            self.after(0, self._finish_db_refresh)
            return

        if len(schemas) == 1:
            self.after(0, lambda: self._load_items_for_schema(schemas[0]))
        else:
            self.after(0, lambda: self._prompt_schema_choice(schemas))

    def _prompt_schema_choice(self, schemas: list[str]):
        """يظهر نافذة صغيرة لاختيار الشركة/الملف عندما يكون هناك أكثر من واحدة."""
        dialog = tk.Toplevel(self)
        dialog.title("اختر الشركة/الملف")
        dialog.transient(self)
        dialog.grab_set()

        tk.Label(dialog, text="عندكم أكثر من ملف/شركة بقاعدة البيانات - اختر الصحيح:", padx=12, pady=8).pack()
        listbox = tk.Listbox(dialog, width=30, height=min(len(schemas), 10), exportselection=False)
        for name in schemas:
            listbox.insert("end", name)
        listbox.selection_set(0)
        listbox.pack(padx=12, pady=4)

        def confirm():
            selection = listbox.curselection()
            chosen = schemas[selection[0]] if selection else schemas[0]
            dialog.destroy()
            self._load_items_for_schema(chosen)

        tk.Button(dialog, text="اختيار", command=confirm, bg="#6a1b9a", fg="white").pack(pady=8)

    def _load_items_for_schema(self, schema: str):
        self.status_label.config(text=f"جاري تحميل أصناف {schema}...")
        self.update_idletasks()
        threading.Thread(target=self._load_items_for_schema_worker, args=(schema,), daemon=True).start()

    def _load_items_for_schema_worker(self, schema: str):
        from db_items import DbConnectionError, load_item_reference_from_db

        try:
            items = load_item_reference_from_db(schema=schema)
        except DbConnectionError as exc:
            error_message = str(exc)
            self.after(0, lambda: messagebox.showerror("تعذّر الاتصال بقاعدة البيانات", error_message))
            self.after(0, self._finish_db_refresh)
            return
        except Exception as exc:  # noqa: BLE001 - أي خطأ غير متوقع لازم يبان للمستخدم، مو يجمّد الشاشة بصمت
            error_message = str(exc)
            self.after(0, lambda: messagebox.showerror("خطأ غير متوقع", error_message))
            self.after(0, self._finish_db_refresh)
            return

        self.live_items_reference = items
        # عدد الأصناف الفريدة (وليس عدد صفوف المرجع، الذي يتكرر مرة لكل
        # باركود إضافي لنفس الصنف) - تجنباً لتضليل المستخدم بعدد أكبر من الحقيقي
        distinct_count = len({item.code for item in items})

        def update_ui():
            self.items_label.config(text=f"مباشر من قاعدة البيانات - {schema} ({distinct_count} صنف)", fg="#6a1b9a")
            self.status_label.config(text="تم تحديث قائمة الأصناف من قاعدة البيانات مباشرة.")
            self.refresh_db_button.config(state="normal")

        self.after(0, update_ui)

    def _finish_db_refresh(self):
        self.status_label.config(text="")
        self.refresh_db_button.config(state="normal")

    def _run_extraction(self):
        if not self.batch:
            messagebox.showwarning("تنبيه", "الرجاء اختيار ملف فاتورة واحد أو أكثر أولاً.")
            return
        if all(inv.extracted for inv in self.batch):
            messagebox.showinfo("تنبيه", "كل فواتير الدفعة مستخرجة مسبقاً. أضف ملفاً جديداً للاستمرار.")
            return
        if config.EXTRACTION_BACKEND == "vision" and not usage_tracker.is_ai_enabled():
            messagebox.showwarning(
                "الذكاء الاصطناعي متوقف",
                "أوقفت الذكاء الاصطناعي يدوياً من زر \"💰 رصيد الذكاء الاصطناعي\". "
                "فعّله من هناك لو تبي تكمل الاستخراج.",
            )
            return
        # منع ضغط الزر مرتين بسرعة أثناء استخراج شغّال - كان يشغّل خيطين
        # متوازيين يتسابقين على تحديث self.lines ونتيجة الجدول
        if str(self.extract_button["state"]) == "disabled":
            return
        self.extract_button.config(state="disabled")
        self.status_label.config(text="جاري الاستخراج...")
        self.update_idletasks()
        threading.Thread(target=self._extraction_worker, daemon=True).start()

    def _extract_one_invoice(self, inv: _BatchInvoice, reference: list, reference_attrs_index: dict) -> None:
        """يستخرج بنود فاتورة وحدة (صفحاتها كلها) ويعبّي حالتها في inv نفسه -
        مفصولة عشان تُستخدم لأي فاتورة داخل الدفعة بدون تكرار الكود."""
        pages = load_pages_as_images(inv.path)

        inv_lines: list[ExtractedLine] = []
        subtotal_before_tax = None
        tax_amount = None
        grand_total_with_tax = None
        supplier_name = None
        for page_img in pages:
            if config.EXTRACTION_BACKEND == "vision":
                page_result = extract_line_items_vision(page_img)
                inv_lines.extend(page_result.items)
                # الملخص (الضريبة/الإجمالي الكلي/اسم المورد) عادة مطبوع
                # بصفحة وحدة بس (آخر صفحة أو أول صفحة غالباً) - نحتفظ بأول
                # قيمة نلقاها
                if subtotal_before_tax is None:
                    subtotal_before_tax = page_result.subtotal_before_tax
                if tax_amount is None:
                    tax_amount = page_result.tax_amount
                if grand_total_with_tax is None:
                    grand_total_with_tax = page_result.grand_total_with_tax
                if supplier_name is None:
                    supplier_name = page_result.supplier_name
            else:
                # مكتبات Tesseract/OpenCV تُستورد هنا فقط (وليست بأعلى الملف)
                # عشان ما تكون مطلوبة على جهاز العميل إلا لو فعلاً بدّلت
                # EXTRACTION_BACKEND لـ"tesseract".
                from ocr import extract_line_items
                from preprocess import preprocess_for_ocr

                clean_img = preprocess_for_ocr(page_img)
                inv_lines.extend(extract_line_items(clean_img))

        # مطابقة items.py الأساسية أولاً بدون أي تعديل - نفس الأولوية للباركود
        # كما هي دايماً. نلقط باركود كل سطر *قبل* هذي الخطوة عشان نعرف بعدين
        # بالضبط أي سطر تطابق فعلياً بالباركود (مو بس صار متطابق بأي طريقة).
        original_barcodes = [line.barcode for line in inv_lines]
        match_line_items(inv_lines, reference)

        if config.EXTRACTION_BACKEND == "vision" and reference:
            ref_barcodes = {item.barcode for item in reference if item.barcode}
            for line, orig_bc in zip(inv_lines, original_barcodes):
                if orig_bc and orig_bc in ref_barcodes:
                    continue  # تطابق باركود مؤكد بالفعل - يُترك كما هو تماماً، ما يُلمس إطلاقاً
                # أي سطر ثاني (حتى لو items.py طابقه بالاسم الضبابي الداخلي)
                # يُعاد حسابه بالكامل بالمحرك الجديد، عشان فحص تعارض الخصائص
                # ينطبق دايماً - راجع matching_engine.py. allow_semantic=False
                # عمداً: الاستخراج محلي بحت بدون أي نداء شبكي لـSemantic AI
                # (راجع docstring enhance_one) - AI يبقى حصراً بنافذة المراجعة.
                matching_engine.enhance_one(
                    line, reference, supplier_name=supplier_name,
                    reference_attrs_index=reference_attrs_index, allow_semantic=False,
                )

        inv.lines = inv_lines
        inv.subtotal_before_tax = subtotal_before_tax
        inv.tax_amount = tax_amount
        inv.grand_total_with_tax = grand_total_with_tax
        inv.supplier_name = supplier_name

    def _extraction_worker(self):
        try:
            if self.live_items_reference is not None:
                reference = self.live_items_reference
            else:
                reference = load_item_reference(self.items_ref_path)
            self.last_reference = reference
            # خصائص كل صنف بالمرجع (ماركة/حجم/عدد/تعبئة) تُحسب مرة وحدة هنا،
            # مو لكل سطر فاتورة - القائمة ممكن تكون عشرات الآلاف من الأصناف.
            # نحفظها بـself كمان عشان لوحة المراجعة (بعد الاستخراج) تعيد
            # استخدامها بدون إعادة حسابها من الصفر عند كل ضغطة على صف
            reference_attrs_index = matching_engine.build_reference_attrs_index(reference)
            self.reference_attrs_index = reference_attrs_index

            # فواتير سبق استخراجها بنجاح (من ضغطة سابقة على الزر، مثلاً قبل
            # إضافة ملف جديد للدفعة) ما تُعاد - توفير تكلفة API حقيقية.
            # الفاشلة تُعاد المحاولة عليها تلقائياً كل مرة.
            pending = [inv for inv in self.batch if not inv.extracted]
            total = len(pending)
            failed = 0
            for i, inv in enumerate(pending):
                progress_msg = f"جاري الاستخراج: الفاتورة {i + 1} من {total} ({inv.path.name})..."
                self.after(0, lambda m=progress_msg: self.status_label.config(text=m))
                try:
                    self._extract_one_invoice(inv, reference, reference_attrs_index)
                    inv.error = None
                    inv.extracted = True
                except Exception as exc:  # noqa: BLE001 - خطأ بفاتورة وحدة ما يوقف باقي الدفعة
                    inv.error = str(exc)
                    failed += 1

            if settings_module.get_settings()["review_mode"] == "during_reading":
                self._run_during_reading_review()

            self.after(0, lambda: self._show_batch_index_fresh(0))

            ok_count = total - failed
            total_lines = sum(len(inv.lines) for inv in self.batch)
            msg = f"تم استخراج {ok_count} من {total} فاتورة ({total_lines} سطر إجمالاً)."
            if failed:
                msg += f" فشلت {failed} فاتورة - تنقّل بينها بالأسهم فوق الجدول، الفاشلة عليها علامة ⚠."
            if not reference:
                msg += " تنبيه: لم يتم تحميل قائمة أصناف مرجعية، فالمطابقة معطّلة."
            self.after(0, lambda: self.status_label.config(text=msg))
        except Exception as exc:  # noqa: BLE001 - خطأ عام قبل حتى البدء (مثلاً ملف الأصناف المرجعي تالف)
            error_message = str(exc)
            self.after(0, lambda: messagebox.showerror("خطأ أثناء الاستخراج", error_message))
            self.after(0, lambda: self.status_label.config(text=""))
        finally:
            self.after(0, lambda: self.extract_button.config(state="normal"))

    def _run_during_reading_review(self):
        """وضع "أثناء القراءة": يمرّ على كل سطر "يحتاج مراجعة" بالدفعة كاملة
        وحدة وحدة، ويعرض نافذة قرار بشري تنتظر بلا حد أقصى (يشتغل هذا على
        خيط الخلفية نفسه اللي يشغّل الاستخراج، ويستخدم _run_on_main_thread_sync
        عشان يعرض وينتظر من غير ما يجمّد واجهة البرنامج)."""
        for batch_idx, inv in enumerate(self.batch):
            if inv.error:
                continue
            for line_idx in range(len(inv.lines)):
                line = inv.lines[line_idx]
                if line is None or not line.needs_review:
                    continue
                action = self._run_on_main_thread_sync(
                    lambda b=batch_idx, i=line_idx: self._review_one_line_blocking(b, i), timeout=None
                )
                if action == "cancel_batch":
                    return

    def _review_one_line_blocking(self, batch_idx: int, line_idx: int) -> str:
        """يشتغل على الخيط الرئيسي فقط (يُستدعى عبر _run_on_main_thread_sync).
        يبدّل عرض الفاتورة الحالية لو لزم، ثم يفتح نافذة المراجعة ويرجّع
        القرار: picked / skip / not_in_catalog / cancel_batch."""
        inv = self.batch[batch_idx]
        line = inv.lines[line_idx] if line_idx < len(inv.lines) else None
        if line is None or not line.needs_review:
            return "skip"
        if self.batch_index != batch_idx:
            self._show_batch_index_fresh(batch_idx)
        return self._open_review_dialog(line_idx, line)

    def _open_review_dialog(self, idx: int, line: ExtractedLine, show_cancel_button: bool = True) -> str:
        """نافذة مراجعة صنف وحد - تُستخدم بوضعين: أثناء القراءة (يمرّ عليها
        كل صف "يحتاج مراجعة" بالدفعة تباعاً، show_cancel_button=True زي
        الافتراضي، عشان زر "إلغاء المراجعة المتبقية" منطقي هناك) وبعد
        الاستخراج (الضغط على أي صف بجدول "أصناف غير موجودة" يفتحها مباشرة
        لصف واحد بس، show_cancel_button=False - ما فيه "طابور" يُلغى).
        حتى 5 مرشّحين، بحث يدوي سريع، وأزرار اختيار/تخطي/اعتباره غير موجود.
        الاختيار يمر عبر _apply_edit_to_model - نفس مسار التعديل اليدوي
        بالضبط، صفر منطق مكرر."""
        result = {"action": "skip"}
        dialog = tk.Toplevel(self)
        dialog.title("مراجعة صنف يحتاج تأكيد")
        dialog.transient(self)
        dialog.grab_set()
        dialog.geometry("520x520")

        header = f"الصنف بالفاتورة: {line.description}"
        if line.barcode:
            header += f"\nباركود الفاتورة: {line.barcode}"
        tk.Label(dialog, text=header, font=("Arial", 11, "bold"), wraplength=480, justify="right", anchor="e").pack(
            fill="x", padx=16, pady=(16, 8)
        )

        ai_status_label = tk.Label(dialog, text="", fg="#6a1b9a", anchor="e", justify="right")
        ai_status_label.pack(fill="x", padx=16)

        tk.Label(
            dialog, text="أقرب الأصناف بالقاعدة - اضغط \"اختيار\" جنب الصنف الصحيح:",
            anchor="e", justify="right", font=("Arial", 9),
        ).pack(fill="x", padx=16, pady=(4, 0))

        suggestions_frame = tk.Frame(dialog)
        suggestions_frame.pack(fill="both", padx=16, pady=(4, 0))

        def pick(candidate):
            self._apply_edit_to_model(idx, "code", candidate.item.code)
            line.needs_review = False
            result["action"] = "picked"
            dialog.destroy()
            self.status_label.config(
                text=f"✓ تم اختيار: {candidate.item.name} (رقم الصنف {candidate.item.code})"
            )

        def render_suggestions(candidates):
            for child in suggestions_frame.winfo_children():
                child.destroy()
            if not candidates:
                tk.Label(
                    suggestions_frame, text="لا يوجد اقتراح مناسب - ابحث يدوياً بالأسفل",
                    anchor="e", justify="right",
                ).pack(fill="x", pady=4)
            for candidate in candidates:
                row = tk.Frame(suggestions_frame, relief="groove", borderwidth=1)
                row.pack(fill="x", pady=3)
                tk.Button(
                    row, text="اختيار", command=lambda c=candidate: pick(c), bg="#1565c0", fg="white",
                    font=("Arial", 10, "bold"), width=8,
                ).pack(side="left", padx=6, pady=6)
                info = tk.Frame(row)
                info.pack(side="right", fill="x", expand=True, padx=(4, 8), pady=4)
                tk.Label(info, text=candidate.item.name, font=("Arial", 11, "bold"), anchor="e", justify="right").pack(fill="x")
                warnings = _extract_warning_notes(candidate.reason)
                subtitle = f"{_plain_confidence_label(candidate.confidence)}   •   {candidate.item.code}   •   {candidate.item.default_unit}"
                tk.Label(info, text=subtitle, fg="#555555", anchor="e", justify="right", font=("Arial", 9)).pack(fill="x")
                if warnings:
                    tk.Label(info, text=warnings, fg="#b71c1c", anchor="e", justify="right", font=("Arial", 9)).pack(fill="x")

        # المحرك المحلي سريع بدون شبكة - يبان فوراً بدون أي تجميد. لو الحالة
        # صعبة (راجع matching_engine.needs_semantic_rerank)، نحسّن الاقتراحات
        # بخيط خلفية منفصل ونحدّث النافذة لما يخلص، بدل ما نجمّد الواجهة
        # بانتظار رد الذكاء الاصطناعي (قد يأخذ ثوانٍ) - هذي النافذة نفسها
        # تُفتح من الخيط الرئيسي (عبر _run_on_main_thread_sync)، فتجميدها
        # يجمّد البرنامج كامل لحد ما ترد الشبكة
        local_candidates = matching_engine.suggest_candidates(
            line, self.last_reference, supplier_name=self.invoice_supplier_name,
            reference_attrs_index=self.reference_attrs_index, top_n=5,
        )
        render_suggestions(local_candidates)

        if matching_engine.needs_semantic_rerank(local_candidates):
            ai_status_label.config(text="🤖 جاري تحسين الاقتراحات بالذكاء الاصطناعي...")

            def ai_worker():
                enhanced, _ = matching_engine.semantic_enhance_candidates(
                    line, self.last_reference, supplier_name=self.invoice_supplier_name,
                    reference_attrs_index=self.reference_attrs_index, top_n=5,
                )

                def apply_result():
                    if not dialog.winfo_exists():
                        return  # المستخدم سكّر النافذة قبل ما AI يخلص - نتجاهل النتيجة بهدوء
                    ai_status_label.config(text="")
                    render_suggestions(enhanced)

                self.after(0, apply_result)

            threading.Thread(target=ai_worker, daemon=True).start()

        ttk.Separator(dialog, orient="horizontal").pack(fill="x", padx=16, pady=8)

        search_frame = tk.Frame(dialog)
        search_frame.pack(fill="x", padx=16)
        tk.Label(search_frame, text="بحث يدوي سريع بقائمة الأصناف:", anchor="e", justify="right").pack(fill="x")
        search_row = tk.Frame(search_frame)
        search_row.pack(fill="x", pady=4)
        search_entry = tk.Entry(search_row, justify="right")
        search_entry.pack(side="right", fill="x", expand=True, padx=(4, 0))
        search_results_frame = tk.Frame(dialog)
        search_results_frame.pack(fill="x", padx=16)

        def do_search(_event=None):
            for child in search_results_frame.winfo_children():
                child.destroy()
            query = search_entry.get().strip()
            for _score, item in _search_reference_items(self.last_reference, query, limit=5):
                row = tk.Frame(search_results_frame)
                row.pack(fill="x", pady=1)
                tk.Button(
                    row, text="اختيار",
                    command=lambda it=item: pick(matching_engine.MatchCandidate(item=it, confidence=100.0, reason="اختيار يدوي من البحث")),
                    bg="#00695c", fg="white",
                ).pack(side="left", padx=4)
                tk.Label(row, text=f"{item.code}  |  {item.name}", anchor="e", justify="right").pack(
                    side="right", fill="x", expand=True
                )

        search_entry.bind("<Return>", do_search)
        tk.Button(search_row, text="بحث", command=do_search).pack(side="left", padx=4)

        actions = tk.Frame(dialog)
        actions.pack(fill="x", padx=16, pady=12, side="bottom")

        def skip():
            result["action"] = "skip"
            dialog.destroy()

        def not_in_catalog():
            line.confirmed_not_in_catalog = True
            line.needs_review = False
            result["action"] = "not_in_catalog"
            dialog.destroy()

        def cancel_batch():
            result["action"] = "cancel_batch"
            dialog.destroy()

        if show_cancel_button:
            tk.Button(actions, text="إلغاء المراجعة المتبقية", command=cancel_batch, bg="#b71c1c", fg="white").pack(
                side="left", padx=4
            )
        tk.Button(
            actions, text="اعتباره غير موجود بالقاعدة", command=not_in_catalog, bg="#ef6c00", fg="white"
        ).pack(side="right", padx=4)
        tk.Button(actions, text="تخطي", command=skip).pack(side="right", padx=4)

        dialog.protocol("WM_DELETE_WINDOW", skip)
        dialog.wait_window()
        return result["action"]

    def _show_batch_index_fresh(self, idx: int):
        """يحمّل فاتورة من الدفعة للعرض بدون أي محاولة حفظ للحالة الحالية -
        يُستخدم فقط مباشرة بعد استخراج جديد بالكامل، لأن self.lines وقتها
        بيانات قديمة (من قبل الاستخراج) ولازم تُتجاهل، مو تُحفظ فوق نتيجة
        الاستخراج الجديدة (استخدم _switch_to_batch_index للتنقل العادي)."""
        if not self.batch:
            return
        self.batch_index = idx
        inv = self.batch[idx]
        self.lines = inv.lines
        self.invoice_subtotal_before_tax = inv.subtotal_before_tax
        self.invoice_tax_amount = inv.tax_amount
        self.invoice_grand_total_with_tax = inv.grand_total_with_tax
        self.invoice_supplier_name = inv.supplier_name
        self._populate_table()
        self._update_batch_nav_ui()

    def _switch_to_batch_index(self, idx: int):
        """تنقّل بين فواتير الدفعة أثناء المراجعة - يحفظ أي تعديل يدوي سويته
        على الفاتورة الحالية أول قبل ما ينتقل، عشان ما يضيع لو رجعت لها."""
        if not self.batch:
            return
        if idx != self.batch_index:
            current = self.batch[self.batch_index]
            current.lines = self.lines
            current.subtotal_before_tax = self.invoice_subtotal_before_tax
            current.tax_amount = self.invoice_tax_amount
            current.grand_total_with_tax = self.invoice_grand_total_with_tax
            current.supplier_name = self.invoice_supplier_name

        self.batch_index = idx
        inv = self.batch[idx]
        self.lines = inv.lines
        self.invoice_subtotal_before_tax = inv.subtotal_before_tax
        self.invoice_tax_amount = inv.tax_amount
        self.invoice_grand_total_with_tax = inv.grand_total_with_tax
        self.invoice_supplier_name = inv.supplier_name
        self._populate_table()
        self._update_batch_nav_ui()

    def _update_batch_nav_ui(self):
        if not self.batch:
            self.batch_position_label.config(text="")
            self.batch_prev_button.config(state="disabled")
            self.batch_next_button.config(state="disabled")
            self.remove_invoice_button.config(state="disabled")
            return
        inv = self.batch[self.batch_index]
        note = "  ⚠ فشل استخراج هذي الفاتورة" if inv.error else ""
        self.batch_position_label.config(
            text=f"الفاتورة {self.batch_index + 1} من {len(self.batch)}: {inv.path.name}{note}"
        )
        self.batch_prev_button.config(state="normal" if self.batch_index > 0 else "disabled")
        self.batch_next_button.config(state="normal" if self.batch_index < len(self.batch) - 1 else "disabled")
        self.remove_invoice_button.config(state="normal")

    def _remove_current_invoice_from_batch(self):
        """لو المستخدم اختار ملف فاتورة غلط بالدفعة - يشيلها بدون ما يأثر
        على باقي فواتير الدفعة."""
        if not self.batch:
            return
        inv = self.batch[self.batch_index]
        proceed = messagebox.askyesno(
            "إزالة الفاتورة",
            f"إزالة \"{inv.path.name}\" من الدفعة؟ أي بيانات مستخرجة لها بتُحذف "
            "(باقي فواتير الدفعة ما تتأثر).",
        )
        if not proceed:
            return

        del self.batch[self.batch_index]
        if not self.batch:
            self.batch_index = 0
            self.lines = []
            self.invoice_subtotal_before_tax = None
            self.invoice_tax_amount = None
            self.invoice_grand_total_with_tax = None
            self.invoice_supplier_name = None
            self.invoice_label.config(text="لم يتم اختيار ملف", fg="gray")
            self._populate_table()
            self._update_batch_nav_ui()
            return

        if len(self.batch) == 1:
            self.invoice_label.config(text=self.batch[0].path.name, fg="black")
        else:
            self.invoice_label.config(text=f"{len(self.batch)} ملفات بالدفعة", fg="black")
        new_idx = min(self.batch_index, len(self.batch) - 1)
        self._show_batch_index_fresh(new_idx)

    def _go_prev_invoice(self):
        if self.batch_index > 0:
            self._switch_to_batch_index(self.batch_index - 1)

    def _go_next_invoice(self):
        if self.batch_index < len(self.batch) - 1:
            self._switch_to_batch_index(self.batch_index + 1)

    def _populate_table(self):
        self.tree_unmatched.delete(*self.tree_unmatched.get_children())
        self.tree_matched.delete(*self.tree_matched.get_children())
        unmatched_count = 0
        matched_count = 0
        for i, line in enumerate(self.lines):
            if line is None:
                continue
            is_matched = bool(line.matched_item_code)
            tree = self.tree_matched if is_matched else self.tree_unmatched
            if is_matched:
                # ظل بديل بين صف وصف - يفصّل الصفوف بصرياً عن بعض مع الحفاظ
                # على لون الحالة (مطابَق/غير موجود) كإشارة أساسية
                row_tag = "matched_alt" if matched_count % 2 == 1 else "matched"
                matched_count += 1
            else:
                row_tag = "not_in_catalog_alt" if unmatched_count % 2 == 1 else "not_in_catalog"
                unmatched_count += 1
            tree.insert(
                "",
                "end",
                iid=str(i),
                values=(
                    i + 1,
                    line.barcode,
                    line.matched_item_code,
                    line.matched_item_name or line.description,
                    line.unit,
                    line.quantity if line.quantity is not None else "",
                    line.unit_price if line.unit_price is not None else "",
                    line.total if line.total is not None else "",
                ),
                tags=(row_tag,),
            )
        self.show_unmatched_button.config(text=f"أصناف غير موجودة ({unmatched_count})")
        self.show_matched_button.config(text=f"أصناف مطابَقة ({matched_count})")
        self.unmatched_label.config(text=f"أصناف غير موجودة بالقاعدة - تحتاج مراجعة/إضافة ({unmatched_count})")
        self.matched_label.config(text=f"أصناف مطابَقة ({matched_count})")
        self._update_grand_total()

    def _update_grand_total(self):
        # محسوبة بالكامل من مجموع عمود "الإجمالي" بالجدول الحالي (يتحدّث حي
        # مع أي تعديل/حذف)، مو من القيم المطبوعة بالفاتورة - أدق لأنها تعكس
        # فعلياً الأصناف الموجودة عندك بالجدول الآن، بعكس رقم مطبوع ممكن يكون
        # ناقص لو صنف انقرا غلط أو انحذف.
        computed_subtotal = sum(l.total for l in self.lines if l is not None and l.total is not None)
        computed_tax = computed_subtotal * config.VAT_RATE
        computed_grand_total = computed_subtotal + computed_tax
        text = (
            f"قبل الضريبة: {computed_subtotal:,.2f}   |   "
            f"الضريبة ({config.VAT_RATE * 100:.0f}%): {computed_tax:,.2f}   |   "
            f"الإجمالي شامل الضريبة: {computed_grand_total:,.2f}"
        )
        # تنبيه لطيف فقط لو فيه فرق حقيقي عن الإجمالي المطبوع بالفاتورة نفسها
        # (مؤشر مفيد إن صنف انقرا غلط أو ناقص) - بدون ما يكون الرقم المعروض
        # الأساسي
        if self.invoice_grand_total_with_tax is not None:
            diff = abs(computed_grand_total - self.invoice_grand_total_with_tax)
            if diff > max(1.0, computed_grand_total * 0.01):
                text += (
                    f"   |   ⚠ يختلف عن إجمالي الفاتورة المطبوع "
                    f"({self.invoice_grand_total_with_tax:,.2f}) - راجع الأصناف"
                )
        self.grand_total_label.config(text=text)

    def _delete_selected_row(self):
        for tree in (self.tree_unmatched, self.tree_matched):
            selected = tree.selection()
            if selected:
                idx = int(selected[0])
                self.lines[idx] = None  # يُستبعد لاحقاً عند التصدير
                self._populate_table()
                return

    def _copy_selected_row(self, event):
        """Ctrl+C على صف محدد بالجدول - ينسخ قيم أعمدته (مفصولة بـTab) للحافظة،
        عشان تلصقها باكسل أو أي مكان ثاني."""
        tree: ttk.Treeview = event.widget
        selected = tree.selection()
        if not selected:
            return
        values = tree.item(selected[0], "values")
        text = "\t".join(str(v) for v in values)
        self.clipboard_clear()
        self.clipboard_append(text)
        self.status_label.config(text="✓ تم نسخ بيانات الصف")

    def _on_tree_right_click(self, event):
        """كلك يمين على أي صف (مطابَق أو غير موجود) - قائمة صغيرة فيها خيار
        بحث يدوي عن الصنف الصحيح، لأي صف مو بس اللي يحتاج مراجعة."""
        tree: ttk.Treeview = event.widget
        row_id = tree.identify_row(event.y)
        if not row_id:
            return
        tree.selection_set(row_id)
        idx = int(row_id)
        if idx >= len(self.lines) or self.lines[idx] is None:
            return
        line = self.lines[idx]
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="🔍 بحث عن الصنف الصحيح", command=lambda: self._open_item_search_dialog(idx, line))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _open_item_search_dialog(self, idx: int, line: ExtractedLine):
        """نافذة بحث عن صنف - تُفتح بدبل كلك على "رقم الصنف"/"اسم الصنف"
        بالجدول، أو من قائمة كلك اليمين. نتائج البحث بجدول أعمدة حقيقي
        (زي شاشة "بيانات الأصناف" بالشامل) بدل قائمة أزرار، واختيار الصف
        يكون بدبل كلك عليه أو زر "اختيار"."""
        dialog = tk.Toplevel(self)
        dialog.title("بحث عن صنف")
        dialog.transient(self)
        dialog.grab_set()
        dialog.geometry("640x420")

        tk.Label(
            dialog, text=f"الصنف بالفاتورة: {line.description}", font=("Arial", 10, "bold"),
            wraplength=600, justify="right", anchor="e",
        ).pack(fill="x", padx=12, pady=(12, 4))

        search_entry = tk.Entry(dialog, justify="right")
        search_entry.pack(fill="x", padx=12, pady=4)
        search_entry.insert(0, line.matched_item_name or line.description)
        search_entry.focus()
        search_entry.select_range(0, "end")

        results_columns = ("code", "name", "barcode", "unit")
        results_headings = {"code": "رقم الصنف", "name": "اسم الصنف", "barcode": "الباركود", "unit": "الوحدة"}
        results_tree = ttk.Treeview(dialog, columns=results_columns, show="headings", selectmode="browse", height=10)
        for col in results_columns:
            results_tree.heading(col, text=results_headings[col])
            results_tree.column(col, width=120, anchor="e")
        results_tree.column("name", width=260)
        results_tree.pack(fill="both", expand=True, padx=12, pady=8)

        results_items: list = []

        def do_search(_event=None):
            nonlocal results_items
            results_tree.delete(*results_tree.get_children())
            query = search_entry.get().strip()
            scored = _search_reference_items(self.last_reference, query, limit=15)
            results_items = [item for _score, item in scored]
            for i, item in enumerate(results_items):
                results_tree.insert(
                    "", "end", iid=str(i), values=(item.code, item.name, item.barcode, item.default_unit)
                )

        def pick_selected(_event=None):
            selected = results_tree.selection()
            if not selected:
                return
            item = results_items[int(selected[0])]
            self._apply_edit_to_model(idx, "code", item.code)
            dialog.destroy()
            self.status_label.config(
                text=f"✓ تم اختيار: {item.name} (رقم الصنف {item.code}) - انتقل الصف لقسم \"أصناف مطابَقة\""
            )

        search_entry.bind("<KeyRelease>", do_search)
        results_tree.bind("<Double-1>", pick_selected)

        button_row = tk.Frame(dialog)
        button_row.pack(fill="x", padx=12, pady=(0, 8))
        tk.Button(button_row, text="اختيار", command=pick_selected, bg="#00695c", fg="white").pack(side="right", padx=4)
        tk.Button(button_row, text="إغلاق", command=dialog.destroy).pack(side="right", padx=4)

        do_search()

    def _on_row_selected(self, event):
        """وضع المراجعة بعد الاستخراج: تحديد صف "يحتاج مراجعة" يفتح نافذة
        منبثقة واضحة بأقرب مرشّحين مباشرة (نفس نافذة وضع "أثناء القراءة" -
        _open_review_dialog - بدون زر "إلغاء المراجعة المتبقية" هنا، لأنه ما
        فيه "طابور" مراجعة متتابع بهذا الوضع، مجرد صف واحد اختاره المستخدم).
        قرار صريح من المستخدم (2026-08-26): كانت لوحة صغيرة تحت الجدول
        تحتاج تمرير/بحث لتلاحظها - النافذة المنبثقة أوضح وأسرع."""
        tree: ttk.Treeview = event.widget
        selected = tree.selection()
        if not selected:
            return
        idx = int(selected[0])
        line = self.lines[idx] if idx < len(self.lines) else None
        if line is None or not line.needs_review:
            return
        self._open_review_dialog(idx, line, show_cancel_button=False)

    def _on_cell_double_click(self, event):
        """يسمح بتعديل قيمة أي خلية يدوياً. لعمودي رقم الصنف واسم الصنف
        تحديداً، دبل كلك يفتح نافذة البحث الكاملة مباشرة (بدل تعديل نصي
        داخلي صغير) - أوضح وأسهل لاختيار الصنف الصحيح من القائمة المرجعية."""
        tree: ttk.Treeview = event.widget
        region = tree.identify("region", event.x, event.y)
        if region != "cell":
            return
        row_id = tree.identify_row(event.y)
        col_id = tree.identify_column(event.x)
        if not row_id:
            return

        col_index = int(col_id.replace("#", "")) - 1
        col_name = COLUMNS[col_index]
        if col_name == "num":
            return  # عمود رقم السطر تلقائي، غير قابل للتعديل

        idx = int(row_id)
        if col_name in ("code", "matched_name"):
            line = self.lines[idx] if idx < len(self.lines) else None
            if line is not None:
                self._open_item_search_dialog(idx, line)
            return

        x, y, w, h = tree.bbox(row_id, col_id)
        current_value = tree.set(row_id, col_name)

        entry = tk.Entry(tree)
        entry.place(x=x, y=y, width=w, height=h)
        entry.insert(0, current_value)
        entry.focus()

        def save_edit(_event=None):
            new_value = entry.get()
            entry.destroy()
            self._apply_edit_to_model(idx, col_name, new_value)

        entry.bind("<Return>", save_edit)
        entry.bind("<FocusOut>", save_edit)
        entry.bind("<Escape>", lambda _e: entry.destroy())

    def _apply_edit_to_model(self, idx: int, col_name: str, value: str):
        line = self.lines[idx]
        if line is None:
            return

        if col_name in _EDITABLE_FIELD_BY_COLUMN:
            setattr(line, _EDITABLE_FIELD_BY_COLUMN[col_name], value)
            if col_name in ("code", "barcode"):
                self._resync_from_reference(line, key=col_name, value=value)
        elif col_name in _NUMERIC_FIELD_BY_COLUMN:
            value = value.strip()
            if not value:
                numeric = None
            else:
                try:
                    numeric = float(value)
                except ValueError:
                    numeric = None
                # float() يقبل "nan"/"inf"/"-inf" كأرقام صحيحة برمجياً، لكنها
                # تكسر ملف الاكسل/الـ.Amn الناتج عند التصدير (إكسل و XML ما
                # يدعمون NaN/Infinity كقيمة رقمية) - نرفضها هنا بدل ما تمر
                # بصمت وتظهر كمشكلة غامضة بعدين وقت التصدير فقط
                if numeric is not None and not (numeric == numeric and abs(numeric) != float("inf")):
                    numeric = None
                if numeric is None:
                    messagebox.showwarning(
                        "قيمة غير صالحة", f"'{value}' مو رقم صحيح - ما راح يتغيّر، القيمة القديمة باقية."
                    )
                    self._populate_table()
                    return
            setattr(line, _NUMERIC_FIELD_BY_COLUMN[col_name], numeric)
        else:
            return  # عمود مرجعي فقط (مثل نسبة التطابق) - لا يُحفظ في البيانات

        # إعادة بناء الجدولين بالكامل - أبسط وأضمن من تحديث خلية بخلية،
        # خصوصاً إن تعديل رقم الصنف ممكن يغيّر مكان الصف نفسه (من قسم "غير
        # موجود" إلى قسم "مطابَق") لو الرقم اتّضح إنه موجود فعلاً بقاعدتكم
        self._populate_table()

    def _resync_from_reference(self, line: ExtractedLine, key: str, value: str):
        """لما تعدّل رقم الصنف أو الباركود يدوياً بالجدول، نعيد البحث عن
        الصنف بقائمة الأصناف المحمَّلة من قاعدتكم (self.last_reference)
        ونعبّي بقية حقوله تلقائياً (الاسم، الوحدة، والأهم: رقمه الداخلي
        CLS_ID/UN_ID). هذا الرقم الداخلي هو اللي يربط الصنف فعلياً عند
        تصدير "ملف قياسي" (.Amn) - كتابة رقم صنف أو باركود صحيح بمفرده
        بدون هذا الربط يصدَّر بصمت وبدون تأثير عند الاستيراد. الباركود
        أقوى معرّف (فريد 100%)، فأي تطابق فيه يُعتمد بثقة كاملة مثل رقم
        الصنف تماماً."""
        value = value.strip()
        if not value:
            if key == "code":
                line.matched_internal_id = ""
                line.matched_unit_id = ""
            return

        match = next((item for item in self.last_reference if getattr(item, key) == value), None)
        if match is None:
            if key == "code":
                line.matched_internal_id = ""
                line.matched_unit_id = ""
            return

        line.matched_item_code = match.code
        line.matched_item_name = match.name
        line.matched_internal_id = match.internal_id
        line.matched_unit_id = match.unit_id
        line.barcode = match.barcode or line.barcode
        line.unit = match.default_unit or line.unit

        # تعديل يدوي ناجح = تأكيد بشري صريح - نغذّي به جدول التعلّم (خلافاً
        # لأي تطابق تلقائي، حتى لو بثقة عالية) عشان الفواتير القادمة من نفس
        # المورد بنفس صياغة الصنف تُقترح تلقائياً
        if settings_module.get_settings().get("auto_learn_from_manual_edits", True):
            learned_matches.record_confirmation(self.invoice_supplier_name or "", line.description, match)

    def _current_default_export_name(self) -> str:
        """اسم ملف افتراضي يعتمد على اسم ملف الفاتورة الحالية من الدفعة، عشان
        صادرات فواتير متعددة ما تتشابه أسماؤها وتضيع/تنكتب فوق بعض."""
        if self.batch:
            return f"{self.batch[self.batch_index].path.stem}_مستوردة"
        return "فاتورة_مستوردة"

    def _warn_if_needs_review_pending(self, remaining: list[ExtractedLine]) -> bool:
        """بوابة ما قبل التصدير: أي سطر لسا "يحتاج مراجعة" (اقترحه المحرك
        الذكي لكن ما تأكد يدوياً - لا باختيار اقتراح ولا باعتباره غير موجود
        صراحة) يُنبَّه عليه صراحة قبل التصدير. يرجّع True لو تكمّل."""
        pending = [l for l in remaining if l.needs_review]
        if not pending:
            return True
        return messagebox.askyesno(
            "أصناف لسا تحتاج مراجعة",
            f"يوجد {len(pending)} صنف اقترحه محرك المطابقة الذكي لكن ما تأكد يدوياً بعد "
            "(لا اخترت له اقتراح ولا اعتبرته صنف غير موجود بالقاعدة) - راجعه بالضغط على صفه "
            "بجدول \"أصناف غير موجودة\" قبل التصدير لضمان دقة الاستيراد. تكمّل التصدير بدونه؟",
        )

    def _export(self):
        if not self.lines:
            messagebox.showwarning("تنبيه", "لا توجد بيانات مستخرجة للتصدير.")
            return
        remaining = [l for l in self.lines if l is not None]
        if not self._warn_if_needs_review_pending(remaining):
            return
        not_in_catalog = [l for l in remaining if not l.matched_item_code]
        if not_in_catalog:
            proceed = messagebox.askyesno(
                "أصناف بدون رقم صنف",
                f"يوجد {len(not_in_catalog)} صنف بدون رقم صنف (مظلَّل بالكهرماني، غير موجود "
                "بقاعدتكم). البيانات الأخرى (الاسم/الكمية/السعر) صحيحة كما هي. تكمّل التصدير؟",
            )
            if not proceed:
                return

        output_path = filedialog.asksaveasfilename(
            title="حفظ ملف الاستيراد",
            defaultextension=".xlsx",
            filetypes=[("Excel", "*.xlsx")],
            initialfile=f"{self._current_default_export_name()}.xlsx",
        )
        if not output_path:
            return

        try:
            saved_path = export_to_excel(remaining, output_path)
            messagebox.showinfo(
                "تم التصدير",
                f"تم حفظ الملف في:\n{saved_path}\n\n"
                "الآن افتح شاشة فاتورة المشتريات في AccSystem واستخدم زر "
                "\"استيراد من اكسل\" لاختيار هذا الملف.",
            )
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("خطأ أثناء التصدير", str(exc))

    def _export_amn(self):
        if not self.lines:
            messagebox.showwarning("تنبيه", "لا توجد بيانات مستخرجة للتصدير.")
            return
        remaining = [l for l in self.lines if l is not None]
        if not self._warn_if_needs_review_pending(remaining):
            return
        # صيغة الملف القياسي تربط الصنف عبر رقمه الداخلي بقاعدة البيانات
        # (CLS_ID)، مو بس رقم الصنف الظاهر (matched_item_code) - سطر عنده
        # رقم صنف مكتوب لكن بدون CLS_ID حقيقي (مثلاً كُتب يدوياً برقم غير
        # موجود فعلياً) يصدَّر بـ CLS_ID=0 ويتجاهله AccSystem بصمت عند
        # الاستيراد، فيبان للمستخدم إنه "ما استورد" بدون أي رسالة خطأ.
        matched = [l for l in remaining if l.matched_internal_id]
        skipped = len(remaining) - len(matched)

        if not matched:
            messagebox.showwarning(
                "لا يوجد أصناف مطابَقة",
                "صيغة الملف القياسي تحتاج رقم صنف حقيقي موجود فعلياً بقاعدتكم لكل سطر "
                "(بعكس الاكسل). كل الأصناف الحالية بدون مطابقة حقيقية - استخدم تصدير "
                "الاكسل بدلاً، أو أضف هذي الأصناف بقاعدتكم أولاً.",
            )
            return

        if skipped:
            proceed = messagebox.askyesno(
                "أصناف بدون رقم صنف حقيقي",
                f"يوجد {skipped} صنف بدون رقم صنف مربوط فعلياً بقاعدتكم (إما غير موجود "
                "بالأصل، أو كتبت رقم صنف يدوياً غير صحيح) - ما يمكن تضمينه بملف قياسي، "
                f"بيُستبعد تلقائياً. باقي {len(matched)} صنف مطابَق بيُصدَّر. تكمّل؟",
            )
            if not proceed:
                return

        output_path = filedialog.asksaveasfilename(
            title="حفظ ملف الاستيراد (قياسي)",
            defaultextension=".Amn",
            filetypes=[("ملف قياسي", "*.Amn")],
            initialfile=f"{self._current_default_export_name()}.Amn",
        )
        if not output_path:
            return

        try:
            saved_path = export_to_amn(matched, output_path)
            messagebox.showinfo(
                "تم التصدير",
                f"تم حفظ الملف في:\n{saved_path}\n\n"
                "الآن افتح شاشة فاتورة المشتريات في AccSystem واستخدم زر "
                "\"استيراد من ملف قياسي\" لاختيار هذا الملف.",
            )
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("خطأ أثناء التصدير", str(exc))

    # ---------- شات مساعد سنافي (tool calling) ----------
    def _open_chat_window(self):
        if self._chat_window is not None and self._chat_window.winfo_exists():
            self._chat_window.lift()
            self.chat_entry.focus()
            return

        win = tk.Toplevel(self)
        win.title("مساعد سنافي")
        win.geometry("480x580")
        self._chat_window = win

        history_frame = tk.Frame(win)
        history_frame.pack(fill="both", expand=True, padx=8, pady=8)
        self.chat_text = tk.Text(history_frame, wrap="word", state="disabled", font=("Arial", 10))
        self.chat_text.pack(side="right", fill="both", expand=True)
        chat_scroll = ttk.Scrollbar(history_frame, command=self.chat_text.yview)
        chat_scroll.pack(side="left", fill="y")
        self.chat_text.configure(yscrollcommand=chat_scroll.set)
        self.chat_text.tag_configure("user", foreground="#1565c0")
        self.chat_text.tag_configure("assistant", foreground="#1b5e20")
        self.chat_text.tag_configure("error", foreground="#c62828")

        entry_frame = tk.Frame(win)
        entry_frame.pack(fill="x", padx=8, pady=8)
        self.chat_send_button = tk.Button(
            entry_frame, text="إرسال", command=self._send_chat_message, bg="#2e7d32", fg="white"
        )
        self.chat_send_button.pack(side="left", padx=4)
        self.chat_entry = tk.Entry(entry_frame, justify="right")
        self.chat_entry.pack(side="right", fill="x", expand=True, padx=4)
        self.chat_entry.bind("<Return>", lambda _e: self._send_chat_message())
        self.chat_entry.focus()

        if not self.chat_history:
            self._append_chat_message(
                "سنافي",
                'أهلاً! اكتب طلبك على جدول الفاتورة الحالية (مثلاً: "غيّر سعر الصنف رقم 2 إلى 5" '
                'أو "دور على صنف حليب وحطه بالصف الأول")، وأنفّذه مباشرة على الجدول.',
            )

    def _append_chat_message(self, sender: str, text: str):
        if not hasattr(self, "chat_text") or not self.chat_text.winfo_exists():
            return
        tag = "user" if sender == "أنت" else ("error" if sender == "خطأ" else "assistant")
        self.chat_text.configure(state="normal")
        self.chat_text.insert("end", f"{sender}: {text}\n\n", tag)
        self.chat_text.see("end")
        self.chat_text.configure(state="disabled")

    def _send_chat_message(self):
        text = self.chat_entry.get().strip()
        if not text:
            return
        if not usage_tracker.is_ai_enabled():
            self._append_chat_message(
                "خطأ",
                "الذكاء الاصطناعي متوقف حالياً. فعّله من زر \"💰 رصيد الذكاء الاصطناعي\" بالنافذة الرئيسية.",
            )
            return
        self.chat_entry.delete(0, "end")
        self.chat_send_button.config(state="disabled")
        threading.Thread(target=self._chat_worker_wrapper, args=(text,), daemon=True).start()

    def _chat_worker_wrapper(self, text: str):
        try:
            self._chat_worker(text)
        finally:
            self.after(0, self._reenable_chat_send)

    def _reenable_chat_send(self):
        if hasattr(self, "chat_send_button") and self.chat_send_button.winfo_exists():
            self.chat_send_button.config(state="normal")

    def _run_on_main_thread_sync(self, func, timeout: float | None = 15):
        """يشغّل func() على الخيط الرئيسي (لازم لأي شي يلمس Tkinter) وينتظر
        نتيجتها - يُستخدم لتنفيذ أدوات الشات اللي تعدّل جدول المراجعة من
        خيط خلفية (نفس خيط طلب Claude)، وكمان لعرض نافذة مراجعة "أثناء
        القراءة" اللي تنتظر قرار بشري (timeout=None هناك، بدون حد أقصى)."""
        done = threading.Event()
        box: dict = {}

        def wrapper():
            try:
                box["value"] = func()
            except Exception as exc:  # noqa: BLE001
                box["error"] = str(exc)
            finally:
                done.set()

        self.after(0, wrapper)
        if not done.wait(timeout=timeout):
            raise RuntimeError("انتهت مهلة تنفيذ الأداة على واجهة البرنامج.")
        if "error" in box:
            raise RuntimeError(box["error"])
        return box.get("value")

    def _chat_worker(self, user_text: str):
        try:
            client = ai_client.get_client()
        except RuntimeError as exc:
            self.after(0, lambda: self._append_chat_message("خطأ", str(exc)))
            return

        self.chat_history.append({"role": "user", "content": user_text})
        self.after(0, lambda: self._append_chat_message("أنت", user_text))

        messages = list(self.chat_history)
        try:
            for _ in range(8):  # حد أقصى للجولات، يمنع حلقة لا نهائية لو صار خطأ منطقي
                response = client.messages.create(
                    model=config.ANTHROPIC_MODEL,
                    max_tokens=2000,
                    system=_CHAT_SYSTEM_PROMPT,
                    tools=_CHAT_TOOLS,
                    messages=messages,
                )
                if response.usage is not None:
                    usage_tracker.record_usage(response.usage.input_tokens, response.usage.output_tokens)
                assistant_content = [b.model_dump() for b in response.content]
                messages.append({"role": "assistant", "content": assistant_content})

                if response.stop_reason != "tool_use":
                    final_text = "".join(b.text for b in response.content if b.type == "text").strip()
                    self.chat_history[:] = messages
                    if final_text:
                        self.after(0, lambda t=final_text: self._append_chat_message("سنافي", t))
                    return

                tool_results = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue
                    try:
                        result = self._execute_chat_tool(block.name, block.input)
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": json.dumps(result, ensure_ascii=False, default=str),
                            }
                        )
                    except Exception as exc:  # noqa: BLE001 - نرجّعه لـClaude كخطأ أداة، مو نفشل الطلب كله
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": f"خطأ: {exc}",
                                "is_error": True,
                            }
                        )
                messages.append({"role": "user", "content": tool_results})

            self.chat_history[:] = messages
            self.after(
                0,
                lambda: self._append_chat_message("خطأ", "تعذّر إنهاء الطلب بعدد محاولات معقول - جرب تصيغه أبسط."),
            )
        except Exception as exc:  # noqa: BLE001 - أي خطأ شبكة/API لازم يبان بالشات، مو يجمّد بصمت
            error_message = str(exc)
            self.after(0, lambda: self._append_chat_message("خطأ", error_message))

    def _execute_chat_tool(self, name: str, tool_input: dict):
        if name == "list_invoice_lines":
            return self._run_on_main_thread_sync(self._tool_list_invoice_lines)
        if name == "edit_invoice_line":
            return self._run_on_main_thread_sync(
                lambda: self._tool_edit_invoice_line(
                    tool_input.get("index"), tool_input.get("field"), tool_input.get("value")
                )
            )
        if name == "search_catalog_items":
            return self._run_on_main_thread_sync(
                lambda: self._tool_search_catalog_items(tool_input.get("query", ""))
            )
        if name == "delete_invoice_line":
            return self._run_on_main_thread_sync(lambda: self._tool_delete_invoice_line(tool_input.get("index")))
        raise ValueError(f"أداة غير معروفة: {name}")

    def _tool_list_invoice_lines(self):
        rows = []
        for i, line in enumerate(self.lines):
            if line is None:
                continue
            rows.append(
                {
                    "index": i,
                    "barcode": line.barcode,
                    "code": line.matched_item_code,
                    "name": line.matched_item_name or line.description,
                    "unit": line.unit,
                    "qty": line.quantity,
                    "price": line.unit_price,
                    "total": line.total,
                    "matched": bool(line.matched_item_code),
                }
            )
        return {"lines": rows}

    def _tool_edit_invoice_line(self, index, field_name, value):
        if not isinstance(index, int) or index < 0 or index >= len(self.lines) or self.lines[index] is None:
            raise ValueError(f"رقم صف غير صحيح: {index}")
        if field_name not in _EDITABLE_FIELD_BY_COLUMN and field_name not in _NUMERIC_FIELD_BY_COLUMN:
            raise ValueError(f"حقل غير معروف: {field_name}")
        value = "" if value is None else str(value).strip()
        if field_name in _NUMERIC_FIELD_BY_COLUMN and value:
            try:
                numeric = float(value)
            except ValueError:
                raise ValueError(f"'{value}' مو رقم صحيح")
            if not (numeric == numeric and abs(numeric) != float("inf")):
                raise ValueError(f"'{value}' قيمة رقمية غير مسموحة (نهائية/غير معرَّفة)")
        self._apply_edit_to_model(index, field_name, value)
        return {"ok": True, "index": index, "field": field_name, "value": value}

    def _tool_search_catalog_items(self, query: str):
        query = (query or "").strip()
        if not query:
            return {"matches": []}
        matches = []
        for item in self.last_reference:
            if query in item.code or query in item.name or (item.barcode and query in item.barcode):
                matches.append(
                    {"code": item.code, "name": item.name, "barcode": item.barcode, "unit": item.default_unit}
                )
            if len(matches) >= 15:
                break
        return {"matches": matches}

    def _tool_delete_invoice_line(self, index):
        if not isinstance(index, int) or index < 0 or index >= len(self.lines) or self.lines[index] is None:
            raise ValueError(f"رقم صف غير صحيح أو محذوف مسبقاً: {index}")
        self.lines[index] = None
        self._populate_table()
        return {"ok": True, "deleted_index": index}


if __name__ == "__main__":
    app = InvoiceImporterApp()
    app.mainloop()
