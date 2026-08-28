"""
محرك المطابقة الذكي المتدرّج - يعمل فقط على أسطر ما تطابقت بالباركود
(المتصل مسؤول عن هذا الفحص، راجع app.py::_extract_one_invoice). لا يلمس
items.py ولا match_line_items() إطلاقاً - يُستدعى بعدها، مكمّل لها.

أولوية المطابقة هنا لأي سطر بدون باركود:
1. مطابقة متعلّمة سابقة (نفس المورد + نفس/قريب من نص الصنف) - learned_matches.py،
   تتصدّر دايماً بغض النظر عن نتائج بقية المصادر.
2. استرجاع مرشّحين من عدة مصادر مستقلة (خصائص بنيوية: حجم/عدد قطع/تعبئة/
   وحدة صريحة، تاريخ المورد، الماركة، تشابه الاسم) - مو مصدر واحد (تشابه
   الاسم) زي التصميم القديم. صنف بصياغة مختلفة جداً عن اسم الفاتورة، لكن
   بنفس الحجم/العدد/الماركة، ما يُستبعد بصمت قبل حتى فحص خصائصه.
3. تقييم متعدد العوامل حقيقي (اسم + حجم + عدد + تعبئة + وحدة صريحة + ماركة +
   تاريخ مورد + سعر) - كل عامل مكافأة/عقوبة مستقلة، مو "اسم + مكافأة بسيطة".

أي مرشّح، من أي مصدر (حتى المتعلّمة - احتياط لو تغيّرت تعبئة المورد)، يمر
بفحص تعارض الخصائص كخطوة أخيرة: لو تعارض بنيوي حقيقي (حجم/عدد/تعبئة/وحدة
مختلف فعلياً)، الثقة تُقصّ بشدة بغض النظر عن مصدرها أو تشابه الاسم. تشابه
الاسم وحده (بدون أي إشارة داعمة) أبداً ما يوصل لثقة قبول تلقائي - راجع
MATCH_NO_SUPPORT_CONFIDENCE_CAP. حتى لو أفضل مرشّح عدّى عتبة القبول
التلقائي، لو ثاني أفضل مرشّح قريب منه جداً (فجوة غموض) نرفض القبول التلقائي
ونحيلها لمراجعة بشرية - راجع MATCH_MIN_CONFIDENCE_GAP بـenhance_one().
"""

import math
from collections import defaultdict
from dataclasses import dataclass, field

from rapidfuzz import fuzz, process

import config
import learned_matches
import semantic_matcher
import settings as settings_module
from item_attributes import (
    ItemAttributes,
    canonicalize_unit_word,
    extract_attributes,
    pack_count_status,
    size_family,
    size_status,
    unit_word_status,
)
from items import ReferenceItem, _apply_match
from line_item import ExtractedLine

_NAME_POOL_SIZE = 30  # مرشّحين بالتشابه الاسمي الخام - مصدر واحد من عدة مصادر الآن، مو الوحيد
_MAX_CANDIDATES_BEFORE_SCORING = 80  # سقف المجموع بعد دمج كل المصادر، قبل التقييم الكامل (بند 7)

# Semantic Visibility (2026-08-28، Safety Patch #4): سقف صريح "منظم" لقائمة
# Semantic AI الموسّعة - **لا** يمس _MAX_CANDIDATES_BEFORE_SCORING ولا
# SEMANTIC_RERANK_SHORTLIST_SIZE (حجم ما يُرسَل فعلياً لـAPI، أصغر من هذا -
# راجع _build_shortlist_for_semantic) ولا أي سقف/عتبة UI. فقط الحد الأقصى
# لعدد المرشّحين الحقيقيين (من _score_all_candidates، صفر اختراع) اللي
# تُبنى منها قائمة AI الموسّعة *قبل* التنويع.
_SEMANTIC_WIDE_POOL_SIZE = 30
_SEMANTIC_WIDE_POOL_MAX_PER_FAMILY = 3  # يمنع ماركة/عائلة واحدة من ابتلاع القائمة الموسّعة (بند 2: تشبّع)
_SIZE_BUCKET_LOG_STEP = math.log(1.05)  # نفس نسبة تسامح 5% المستخدمة بـitem_attributes.size_status
_MIN_BRAND_TOKEN_LEN = 3  # حد أدنى لطول الماركة عشان "نثق" فيها بما يكفي للاسترجاع/المكافأة/العقوبة

# كلمات "مظلة" معروفة يستخدمها بعض العملاء بالكتالوج لصنف يغطي عدة نكهات/
# أنواع فعلية بكود واحد (Benchmark حقيقي 2026-08-28: "لوزين كرواسان نكهات
# 60جم" يغطي زعتر/جبنة/شوكولاتة... إلخ فعلياً بنفس الكود؛ ونفس النمط شوهد
# سابقاً هالجلسة بحالة "منوع" الحقيقية بـtests). جدول بيانات صريح وعام -
# يُضاف له كلمات جديدة مستقبلاً بدون لمس أي منطق. راجع _content_tokens
# و_retrieve_candidate_hits (مصدر 5) لكيفية استخدامها بحذر (تدخل السباق
# فقط لو تشارك الفاتورة كلمة عائلة المنتج الفعلية، مو wildcard عالمي).
_GENERIC_VARIANT_WORDS = frozenset({"نكهات", "نكهه", "منوع"})

# وحدات تعبئة "خارجية" بالجملة (فاتورة المورد غالباً تكتب بها) مقابل "حبة"
# (القاعدة غالباً مسجّلة بسعر أصغر وحدة بيع) - اختلاف طبيعي متوقع بين
# فاتورة جملة وقاعدة صنف، مو دليل صنف مختلف بمفرده (راجع _is_packaging_tier_pair).
_PACKAGING_TIER_OUTER = frozenset({"carton", "box", "bag", "dozen", "pallet"})


def _is_packaging_tier_pair(unit_a: str, unit_b: str) -> bool:
    """True لو الوحدتان بالضبط زوج (تعبئة خارجية بالجملة، حبة) - كرتون/صندوق/
    كيس/دستة/بالة مقابل حبة، بأي اتجاه. مو أي زوج وحدات مختلفتين (مثلاً كرتون
    مقابل كيس يبقى تعارض حقيقي كالمعتاد - غير مطلوب توسيعه لهذا)."""
    pair = {unit_a, unit_b}
    return "piece" in pair and bool(pair & _PACKAGING_TIER_OUTER)


@dataclass
class MatchCandidate:
    item: ReferenceItem
    confidence: float
    reason: str
    has_structural_conflict: bool = False  # حجم/عدد قطع/تعبئة/وحدة صريحة - يُستثنى من فلترة الثقة الدنيا (مو تعارض الماركة، أضعف/أكثر ضجيجاً)
    from_supplier_history: bool = False  # مسترجَع (كمان) عبر تاريخ المورد - Boost قوي يستاهل يبان حتى لو باقي الأدلة ضعيفة، بس أبداً ما يحتكر القبول التلقائي لوحده


@dataclass
class ReferenceIndex:
    """فهارس محسوبة مرة وحدة لكل تحميل قائمة مرجعية - تُمرَّر لكل نداءات
    matching_engine بدل إعادة حسابها لكل سطر فاتورة (القائمة ممكن تكون
    عشرات الآلاف من الأصناف، تأكدنا فعلياً من ملف حقيقي 35,699 صف). كل
    فهرس: مفتاح خاصية -> قائمة أرقام صفوف (indices) بـreference تحمل هذي
    الخاصية. هذا هو أساس الاسترجاع متعدد المصادر - بدل مسح القائمة كاملة
    بحثاً عن تشابه اسمي فقط لكل سطر فاتورة.

    القيمة المُرجعة من build_reference_attrs_index() (الاسم نفسه محفوظ
    للتوافق - app.py يستخدمها كصندوق أسود بدون فحص نوعها، صفر تعديل هناك)."""

    attrs: dict[int, ItemAttributes]
    by_size_bucket: dict[tuple[str, int], list[int]] = field(default_factory=dict)
    by_pack_count: dict[int, list[int]] = field(default_factory=dict)
    by_unit_word: dict[str, list[int]] = field(default_factory=dict)
    by_unit_field: dict[str, list[int]] = field(default_factory=dict)
    by_brand: dict[str, list[int]] = field(default_factory=dict)
    by_code: dict[str, list[int]] = field(default_factory=dict)
    # بند 5 (Safety Patch #3، 2026-08-28): صفوف كتالوج مسجَّلة باسم "مظلة"
    # عام (_GENERIC_VARIANT_WORDS، مثلاً "نكهات") - مفتاح كلمة عائلة المنتج
    # الفعلية (مثلاً "كرواسان") -> قائمة صفوف تلك العائلة. راجع _content_tokens.
    by_generic_variant_token: dict[str, list[int]] = field(default_factory=dict)


def _size_bucket_key(value: float) -> int:
    """تجميع لوغاريتمي بخطوة 5% - قيمتين بفارق ≤5% تقريباً تقعان بنفس
    الصندوق أو صندوق مجاور، بغض النظر عن حجم القيمة نفسها (يشتغل نفس الشي
    لقيم صغيرة كـ50غ وكبيرة كـ10000غ)."""
    return round(math.log(value) / _SIZE_BUCKET_LOG_STEP)


def _size_buckets_for(value: float) -> tuple[int, int, int]:
    """3 صناديق حول القيمة (احتياط لحدود التقريب قرب عتبة الـ5%) - تُستخدم
    وقت الاسترجاع فقط؛ الفهرسة نفسها تخزّن كل صنف بصندوقه المضبوط بس."""
    center = _size_bucket_key(value)
    return (center - 1, center, center + 1)


def _content_tokens(attrs: ItemAttributes) -> frozenset[str]:
    """كلمات "عائلة المنتج الفعلية" لنص مطبَّع - تُستخدم فقط لمصدر الاسترجاع
    Generic Variant (بند 5). تستبعد فقط: كلمات تحتوي رقم (حجم/عدد)، وكلمات
    "المظلة العامة" نفسها (_GENERIC_VARIANT_WORDS). حد أدنى لطول الكلمة (نفس
    _MIN_BRAND_TOKEN_LEN) يمنع أدوات ربط قصيرة من إحداث تقاطع كاذب.
    **عمداً ما تستبعد attrs.brand**: تخمين الماركة ضعيف عمداً (أول كلمة
    بالنص) - لسطر فاتورة بلا ماركة حقيقية مطبوعة، الكلمة المخمَّنة غالباً
    هي فعلاً كلمة "عائلة المنتج" (مثال حقيقي: "كرواسان الزعتر" -> brand
    المخمَّن = "كرواسان" نفسها، بالضبط الكلمة المطلوبة للمطابقة هنا) -
    استبعادها كان يفشّل التطابق بدل ما يحميه."""
    if not attrs.normalized_text:
        return frozenset()
    tokens = set()
    for tok in attrs.normalized_text.split():
        if len(tok) < _MIN_BRAND_TOKEN_LEN:
            continue
        if any(ch.isdigit() for ch in tok):
            continue
        if tok in _GENERIC_VARIANT_WORDS:
            continue
        tokens.add(tok)
    return frozenset(tokens)


def build_reference_attrs_index(reference: list[ReferenceItem]) -> ReferenceIndex:
    """يبني فهرس متعدد الخصائص لقائمة الأصناف المرجعية - مرة وحدة لكل دفعة
    فواتير، يُمرَّر لبقية دوال هذا الملف بدل إعادة الحساب لكل سطر."""
    index = ReferenceIndex(attrs={})
    for i, item in enumerate(reference):
        attrs = extract_attributes(item.name)
        index.attrs[i] = attrs

        if attrs.size_value_base is not None:
            family = size_family(attrs.size_unit)
            if family is not None:
                index.by_size_bucket.setdefault((family, _size_bucket_key(attrs.size_value_base)), []).append(i)
        if attrs.pack_count is not None:
            index.by_pack_count.setdefault(attrs.pack_count, []).append(i)
        if attrs.unit_word is not None:
            index.by_unit_word.setdefault(attrs.unit_word, []).append(i)
        unit_field_canon = canonicalize_unit_word(item.default_unit)
        if unit_field_canon is not None:
            index.by_unit_field.setdefault(unit_field_canon, []).append(i)
        if attrs.brand and len(attrs.brand) >= _MIN_BRAND_TOKEN_LEN:
            index.by_brand.setdefault(attrs.brand, []).append(i)
        if item.code:
            index.by_code.setdefault(item.code, []).append(i)
        if attrs.normalized_text and (set(attrs.normalized_text.split()) & _GENERIC_VARIANT_WORDS):
            for tok in _content_tokens(attrs):
                index.by_generic_variant_token.setdefault(tok, []).append(i)

    return index


def _find_by_code(reference: list[ReferenceItem], code: str) -> ReferenceItem | None:
    for item in reference:
        if item.code == code:
            return item
    return None


def _retrieve_candidate_hits(
    line: ExtractedLine,
    line_attrs: ItemAttributes,
    reference: list[ReferenceItem],
    reference_index: ReferenceIndex,
    supplier_confirmed_codes: set[str],
) -> dict[int, set[str]]:
    """يرجّع {رقم صف بالمرجع: مجموعة أسماء المصادر اللي رشّحته} - عدة
    مصادر مستقلة بدل الاعتماد على تشابه الاسم وحده. صنف ورد من أكثر من
    مصدر يترجّح تلقائياً بترتيب الاسترجاع (_rank_and_cap_candidates)."""
    hits: dict[int, set[str]] = defaultdict(set)

    # مصدر 1: خصائص بنيوية - استرجاع مباشر من فهارس محسوبة مسبقاً، بدون أي
    # حاجة لتشابه بالاسم إطلاقاً (هذا يحل المشكلة الأساسية: صنف بصياغة اسم
    # مختلفة جداً لسا يُسترجع لو حجمه/عدده/تعبئته متطابقة)
    if line_attrs.size_value_base is not None:
        family = size_family(line_attrs.size_unit)
        if family is not None:
            for bucket in _size_buckets_for(line_attrs.size_value_base):
                for idx in reference_index.by_size_bucket.get((family, bucket), ()):
                    hits[idx].add("size")
    if line_attrs.pack_count is not None:
        for idx in reference_index.by_pack_count.get(line_attrs.pack_count, ()):
            hits[idx].add("pack")
    if line_attrs.unit_word is not None:
        for idx in reference_index.by_unit_word.get(line_attrs.unit_word, ()):
            hits[idx].add("unit_word")
    line_unit_canon = canonicalize_unit_word(line.unit)
    if line_unit_canon is not None:
        for idx in reference_index.by_unit_field.get(line_unit_canon, ()):
            hits[idx].add("unit_field")

    # مصدر 2: تاريخ المورد - أي رقم صنف سبق تأكيده لنفس المورد، بغض النظر
    # عن نص الوصف الحالي. Boost قوي بالتقييم لاحقاً، مو فلتر استبعاد هنا -
    # نضيفه للمرشّحين فقط، ما نمنع أي صنف ثاني من الظهور بسببه
    for code in supplier_confirmed_codes:
        for idx in reference_index.by_code.get(code, ()):
            hits[idx].add("supplier_history")

    # مصدر 3: الماركة (تخمين ضعيف عمداً بـitem_attributes.py) - توسيع فقط،
    # أضعف مصدر، ما نعتمد عليه لوحده لاسترجاع مرشّح بثقة
    if line_attrs.brand and len(line_attrs.brand) >= _MIN_BRAND_TOKEN_LEN:
        for idx in reference_index.by_brand.get(line_attrs.brand, ()):
            hits[idx].add("brand")

    # مصدر 4: تشابه الاسم (الموجود سابقاً كمصدر وحيد) - يبقى مصدر واحد من
    # عدة مصادر الآن، مو المصدر الوحيد اللي يحدّد مين يوصل لمرحلة التقييم.
    # Safety Patch #3 (2026-08-28، Benchmark حقيقي): يقارن النص *المطبَّع*
    # بالطرفين (بدل line.description/item.name الخام كما كان) - عشان
    # _SPELLING_ALIASES/_BRAND_ALIASES وكل تطبيع حروف عربي آخر (أ/إ/آ/ة/ى..)
    # يستفيد منه هذا المصدر الضبابي أيضاً، مو بس فحص التطابق الحرفي. نفس
    # الـscorer/الحد الأقصى بالضبط - توسيع صحة المقارنة، مو تساهل جديد.
    if line_attrs.normalized_text and reference:
        choices = {i: reference_index.attrs[i].normalized_text for i in range(len(reference))}
        pool = process.extract(line_attrs.normalized_text, choices, scorer=fuzz.token_sort_ratio, limit=_NAME_POOL_SIZE)
        for _, _score, idx in pool:
            hits[idx].add("name")

    # مصدر 5: بديل عام بالكتالوج (بند 5 Safety Patch #3) - راجع توثيق
    # _GENERIC_VARIANT_WORDS/_content_tokens أعلاه. محافظ: يُفعَّل فقط لصفوف
    # مسجَّلة صراحة بكلمة مظلة معروفة، وفقط لو الفاتورة تشارك كلمة عائلة
    # المنتج الفعلية معها - صنف محدد بكوده الخاص (لو موجود) ينافس بنفس
    # المجموعة بمصادره القوية (name/size/...) عادي، القرار النهائي للتقييم
    # الكامل كالعادة (هذا بس يضمن دخول السباق، مو فوز تلقائي).
    if reference_index.by_generic_variant_token:
        for tok in _content_tokens(line_attrs):
            for idx in reference_index.by_generic_variant_token.get(tok, ()):
                hits[idx].add("generic_variant")

    return hits


_STRUCTURAL_SOURCES = frozenset({"size", "pack", "unit_word", "unit_field"})


def _candidate_priority_tier(sources: set[str]) -> int:
    """رتبة أولوية (0 = الأعلى) - تُستخدم للترتيب قبل القصّ للسقف الأقصى،
    عشان خاصية بنيوية شائعة جداً (مثلاً "1 لتر" لوحدها بقائمة ضخمة) ما تملأ
    السقف على حساب مرشّحين من مصادر أدقّ/أقوى دلالة. الترتيب المطلوب صراحة:
    تاريخ المورد أولاً، بعده تقاطع خصائص بنيوية متعددة، بعده ماركة+خاصية
    بنيوية، بعده تشابه اسم حقيقي، وأخيراً خاصية بنيوية وحيدة شائعة."""
    if "supplier_history" in sources:
        return 0
    structural_hits = sources & _STRUCTURAL_SOURCES
    if len(structural_hits) >= 2:
        return 1
    if "brand" in sources and structural_hits:
        return 2
    # "name" و"generic_variant" (بند 5 Safety Patch #3) كلاهما إشارة نصية
    # تقريبية بقوة مشابهة - نفس الرتبة، نفس الحصة (_TIER_QUOTAS[3])
    if "name" in sources or "generic_variant" in sources:
        return 3
    return 4  # مصدر بنيوي وحيد بس (مثلاً حجم فقط) - أضعف أولوية، أول من يُقصّ


# حصص محجوزة لكل رتبة أولوية (راجع _candidate_priority_tier) - مو مجرد
# ترتيب، لأن رتبة "الأعلى أولوية" (تاريخ المورد) نفسها ممكن تفيض عن السقف
# (مثلاً مورد عنده أكثر من 100 صنف مؤكَّد سابقاً) وتبتلع كل الحصة على حساب
# مرشّح اسم قوي برتبة أضعف. كل رتبة محفوظ لها حد أقصى، والفائض (لو رتبة
# استخدمت أقل من حصتها) يتوزّع على باقي الرتب بترتيب الأولوية نفسه - الرتبة
# الأخيرة (4: خاصية بنيوية وحيدة شائعة) بلا حصة مخصصة، تاخذ أي شي يفضل.
_TIER_QUOTAS = {0: 25, 1: 20, 2: 10, 3: 20}


def _has_exact_normalized_name_match(line_attrs: ItemAttributes, idx: int, reference_index: ReferenceIndex) -> bool:
    """True لو نص الفاتورة يطابق اسم الصنف المرجعي حرفياً بعد نفس التطبيع
    المستخدم بكل مكان ثاني بهذا الملف (normalized_text، محسوب مسبقاً
    بـbuild_reference_attrs_index - صفر تكلفة إضافية هنا). **قاعدة صريحة
    (REAL BUG FIX 2026-08-28)**: هذا المرشّح يُضمَن دخوله قبل أي معالجة حصص
    عادية بـ_rank_and_cap_candidates - أبداً ما يسقط بسبب ازدحام رتبته، بغض
    النظر عن ترتيب القائمة المرجعية أو عدد المرشّحين المنافسين. هذا ضمان
    "يدخل السباق"، مو "يفوز تلقائياً" - فحص التعارضات البنيوية بـ
    _score_one_candidate يبقى الحاكم النهائي لثقته الفعلية."""
    if not line_attrs.normalized_text:
        return False
    ref_attrs = reference_index.attrs.get(idx)
    return bool(ref_attrs) and ref_attrs.normalized_text == line_attrs.normalized_text


def _pre_rank_quality_key(line: ExtractedLine, reference: list[ReferenceItem], hits: dict[int, set[str]], idx: int) -> tuple:
    """مفتاح ترتيب *داخل* نفس رتبة الأولوية (Pre-Rank) - رخيص عمداً (تشابه
    اسم خام بس، rapidfuzz)، مو التقييم الكامل النهائي (_score_one_candidate:
    حجم/عدد/وحدة/ماركة/مورد/سعر). عدد المصادر يبقى أول معيار (دليل مستقل
    أكثر = أقوى)، وتشابه الاسم يكسر التعادل الحقيقي بدل ترتيب الفهرسة الخام -
    بالضبط هذا كان السبب المؤكَّد (Benchmark حقيقي، عائلتا "الوطنية"
    و"انتاج") لسقوط مرشّح اسمه 100% مطابق عشوائياً بسبب تعادل عدد المصادر
    مع 31 مرشّح ثاني بنفس الرتبة."""
    name_score = fuzz.token_sort_ratio(line.description, reference[idx].name) if line.description else 0.0
    return (-len(hits[idx]), -name_score)


def _guaranteed_identity(idx: int, reference: list[ReferenceItem]) -> tuple:
    """هوية "نفس الصنف المنطقي" لصف مرجعي - code أولاً (نفس معيار
    suggest_candidates)، وإلا internal_id إن كان موثوقاً (موجود وغير فارغ)،
    وإلا الصف يُعامَل كهوية مستقلة عن أي صف ثاني (idx نفسه، فريد ضمن نفس
    الاستدعاء). Safety Patch #2 (2026-08-28): code الفارغ ("" أو None) لا
    يُستخدم كمفتاح مشترك أبداً - كان قبل هذا الإصلاح يجمع كل الأصناف
    *المختلفة* بلا كود مسجَّل تحت نفس المفتاح "" فينهار أحدها فوق الآخر
    خطأً (انهيار كاذب لصفر علاقة منطقية بينها سوى غياب الكود). **لا** يوجد
    أي fallback بالاسم - الاسم عمداً غير مستخدم كهوية."""
    code = reference[idx].code
    if code:
        return ("code", code)
    internal_id = reference[idx].internal_id
    if internal_id:
        return ("internal_id", internal_id)
    return ("row", idx)


def _dedupe_guaranteed_by_code(candidate_idxs: list[int], reference: list[ReferenceItem], hits: dict[int, set[str]]) -> list[int]:
    """يبقي صفاً مرجعياً واحداً بس *للضمان* لكل هوية صنف منطقي فريدة (انظر
    _guaranteed_identity) - نفس معيار "نفس الصنف المنطقي" المستخدم أصلاً
    بنهاية suggest_candidates (dedup نهائي بالكود)، مع fallback آمن لصفوف
    بلا كود. **مو** dedup بالاسم - أكواد مختلفة (أو هويات مستقلة) بنفس
    الاسم المطابَق حرفياً تحصل كل واحدة على مكانها الضامن الخاص (تبقى قادرة
    على المنافسة). الاختيار بين صفوف نفس الهوية حتمي (محتوى الصف، مو ترتيبه
    بالقائمة - Order-Invariance حتى مع تعادل تام): أكثر عدد مصادر مطابقة
    أولاً، ثم unit_id كفاصل تعادل مستقر. الصفوف الأخرى لنفس الهوية لا تُحذف
    من hits/by_tier - تبقى قابلة للدخول عبر الحصص العادية لاحقاً، فمعلومات
    بدائل الوحدة لا تضيع."""
    best_by_identity: dict[tuple, int] = {}
    for idx in candidate_idxs:
        key = _guaranteed_identity(idx, reference)
        current = best_by_identity.get(key)
        if current is None:
            best_by_identity[key] = idx
            continue
        key_new = (-len(hits[idx]), reference[idx].unit_id)
        key_current = (-len(hits[current]), reference[current].unit_id)
        if key_new < key_current:
            best_by_identity[key] = idx
    return list(best_by_identity.values())


_GUARANTEED_STRUCTURAL_SOURCES = {"size", "pack", "unit_word", "unit_field", "brand"}


def _guaranteed_overflow_key(idx: int, reference: list[ReferenceItem], hits: dict[int, set[str]], line: ExtractedLine) -> tuple:
    """مفتاح ترتيب حتمي وquality-aware لقصّ guaranteed لو تجاوز عدد الأصناف
    المنطقية المختلفة (بعد dedup الهوية) السقف الأقصى العام - يُستخدم فقط
    بحالة الفيضان النادرة هذي (Safety Patch #2). كل المرشّحين هنا تطابق اسم
    حرفي (name equality متساوية بينهم)، فالفرق الحقيقي المتاح: (1) عدد
    المصادر المطابقة الكلي، (2) عدد إشارات structural agreement الفعلية
    (حجم/عبوة/كلمة وحدة/حقل وحدة/ماركة) ضمن نفس المصادر، (3) تشابه الاسم
    الخام (fuzz) - الاسم المطبَّع متطابق لكن الخام قد يختلف بمسافات/حالة
    أحرف بسيطة. آخر فاصل تعادل: حقول محتوى الصف نفسها (code, internal_id,
    unit_id, barcode, name) - مو idx أو ترتيب القائمة - يبقى Order-Invariant
    حتى بتعادل تام كامل."""
    ref_item = reference[idx]
    sources = hits[idx]
    structural_agreement = len(sources & _GUARANTEED_STRUCTURAL_SOURCES)
    name_score = fuzz.token_sort_ratio(line.description, ref_item.name) if line.description else 0.0
    return (
        -len(sources),
        -structural_agreement,
        -name_score,
        ref_item.code,
        ref_item.internal_id,
        ref_item.unit_id,
        ref_item.barcode,
        ref_item.name,
    )


def _rank_and_cap_candidates(
    hits: dict[int, set[str]],
    line: ExtractedLine,
    reference: list[ReferenceItem],
    line_attrs: ItemAttributes,
    reference_index: ReferenceIndex,
) -> list[int]:
    """يقصّ المرشّحين لسقف أقصى قبل التقييم الكامل - يحمي الأداء لو خاصية
    شائعة جداً (مثلاً "1 لتر" بقائمة 35 ألف صنف) رجّعت مئات المرشّحين من
    مصدر واحد بس. **مو ترتيب عالمي بس** - كل رتبة أولوية لها حصة محجوزة
    (_TIER_QUOTAS)، عشان رتبة عالية الأولوية بس فياضة العدد (مثلاً تاريخ
    مورد عنده مئات الأصناف) ما تبتلع السقف كامل وتُسقط مرشّح اسم قوي برتبة
    أضعف - الحصص تضمن كل رتبة تاخذ نصيبها الأدنى المضمون قبل أي فائض يتوزّع.

    REAL BUG FIX (2026-08-28، Retrieval Failure مؤكَّد بـBenchmark حقيقي
    مرتين على عائلتين مختلفتين): الترتيب *داخل* كل رتبة كان يعتمد فقط على
    عدد المصادر المطابقة (len(hits[idx])) - رقم صغير يتعادل بسهولة بين
    عشرات المرشّحين بنفس العائلة (نفس الماركة، نفس الحجم تقريباً)، فيصير
    ترتيب الفهرسة الخام (مو أي مؤشر جودة) هو الحكم الفعلي عند التعادل. مع
    32 مرشّح متعادل يتنافسون على حصة 10 فقط، مرشّح بتطابق اسم 100% حرفي وقع
    بالمركز 15 وانقُصّ قبل حتى مرحلة التقييم. الإصلاح بجزأين: (1) تطابق اسم
    حرفي بعد التطبيع يُضمَن دخوله دائماً (_has_exact_normalized_name_match)،
    بغض النظر عن أي شيء آخر؛ (2) الرتب الفياضة فعلاً عن حصتها (العدد الخام >
    الحصة) تُرتَّب بمفتاح جودة حقيقي (_pre_rank_quality_key: عدد المصادر ثم
    تشابه الاسم) بدل عدد المصادر بس - الرتب غير الفياضة تبقى بالترتيب
    الرخيص السابق (كلهم بيدخلون على أي حال، توفير أداء حقيقي). **لا تغيير
    بمنطق الحصص/التوزيع/الفائض نفسه** - فقط الترتيب الداخلي *قبل* تطبيقها."""
    # ضمان صريح (بند 3 بالمواصفة): تطابق اسم حرفي بعد التطبيع لا يسقط بسبب
    # حصة رتبة أبداً - يُضاف *قبل* أي معالجة حصص عادية
    guaranteed = [idx for idx in hits if _has_exact_normalized_name_match(line_attrs, idx, reference_index)]
    # Safety Patch (2026-08-28): نفس الصنف المنطقي (code) يتكرر بالمرجع
    # الحقيقي بعدة صفوف (وحدة/باركود مختلف لكل صف) - أحياناً كلها بنص اسم
    # مطابق حرفياً. بدون هذا الفلتر، صنف واحد بـ20 صف كان يستهلك 20 مكان
    # "ضامن" (حتى يتجاوز السقف العام نظرياً) بلا أي فائدة حقيقية - نفس
    # المعيار المستخدم أصلاً بنهاية suggest_candidates لتعريف "نفس الصنف".
    # الصفوف الأخرى لنفس الكود لا تُحذف من hits/by_tier - تبقى قابلة للدخول
    # عبر الحصص العادية لاحقاً لو فيه متسع، فمعلومات بدائل الوحدة (unit_id)
    # ما تضيع لمرحلة اختيار الوحدة المستقبلية - فقط الدخول "المضمون" يُرشَّد.
    guaranteed = _dedupe_guaranteed_by_code(guaranteed, reference, hits)
    # Safety Patch #2 (2026-08-28): حتى بعد dedup الهوية، عدد الأصناف
    # المنطقية *المختلفة فعلاً* المطابقة اسماً حرفياً قد يتجاوز نظرياً
    # _MAX_CANDIDATES_BEFORE_SCORING (مثلاً 150 كود مختلف بنفس الاسم) -
    # يجب أن يبقى len(candidate_indices) <= السقف invariant صريح بلا
    # استثناء أبداً. القصّ هنا ليس عشوائياً - ترتيب حتمي وquality-aware
    # (_guaranteed_overflow_key) قبل أي قصّ، مو الاعتماد على ترتيب المرجع
    # الخام (Order-Invariance محفوظة).
    if len(guaranteed) > _MAX_CANDIDATES_BEFORE_SCORING:
        guaranteed = sorted(guaranteed, key=lambda idx: _guaranteed_overflow_key(idx, reference, hits, line))
        guaranteed = guaranteed[:_MAX_CANDIDATES_BEFORE_SCORING]

    by_tier: dict[int, list[int]] = defaultdict(list)
    for idx, sources in hits.items():
        by_tier[_candidate_priority_tier(sources)].append(idx)

    for tier, tier_indices in by_tier.items():
        quota = _TIER_QUOTAS.get(tier, _MAX_CANDIDATES_BEFORE_SCORING)
        if len(tier_indices) > quota:
            # الرتبة فياضة فعلاً عن حصتها - ترتيب بجودة حقيقية يمنع القصّ
            # العشوائي (المشكلة المؤكَّدة). ترتيب المرجع نفسه ما يعود يؤثر.
            tier_indices.sort(key=lambda idx: _pre_rank_quality_key(line, reference, hits, idx))
        else:
            # كلهم بيدخلون على أي حال - نفس الترتيب الرخيص السابق يكفي،
            # صفر داعٍ لحساب تشابه اسم إضافي (توفير أداء حقيقي)
            tier_indices.sort(key=lambda idx: -len(hits[idx]))

    selected: list[int] = list(guaranteed)
    selected_set: set[int] = set(guaranteed)
    for tier in sorted(by_tier.keys()):
        quota = _TIER_QUOTAS.get(tier, _MAX_CANDIDATES_BEFORE_SCORING)
        remaining_cap = _MAX_CANDIDATES_BEFORE_SCORING - len(selected)
        available = [idx for idx in by_tier[tier] if idx not in selected_set]
        take = min(quota, remaining_cap, len(available))
        if take <= 0:
            continue
        chosen = available[:take]
        selected.extend(chosen)
        selected_set.update(chosen)

    # فائض: رتبة استخدمت أقل من حصتها المحجوزة تسيب مجال بالسقف - نعبّيه من
    # أي مرشّح لسا ما أُخذ، بنفس ترتيب أولوية الرتب (الأعلى أولوية أولاً)
    if len(selected) < _MAX_CANDIDATES_BEFORE_SCORING:
        for tier in sorted(by_tier.keys()):
            if len(selected) >= _MAX_CANDIDATES_BEFORE_SCORING:
                break
            for idx in by_tier[tier]:
                if idx in selected_set:
                    continue
                selected.append(idx)
                selected_set.add(idx)
                if len(selected) >= _MAX_CANDIDATES_BEFORE_SCORING:
                    break

    return selected


def _price_signal(line_price: float | None, ref_item: ReferenceItem) -> tuple[float, str | None]:
    """إشارة سعر - غير مفعّلة فعلياً حالياً لأن ReferenceItem ما فيه
    last_purchase_price بعد (getattr الآمن يخليها معطّلة بهدوء بدون كسر
    شي، بدل توسيع قاعدة البيانات الآن لمجرد هذي المرحلة). التصميم جاهز لما
    تتوفر بيانات سعر مستقبلاً: فرق بسيط = مكافأة خفيفة، فرق كبير جداً (3x+)
    = عقوبة/تحذير - أبداً مو سبب رفض وحيد (ممكن يكون فرق حبة/كرتون، خصم،
    تغيّر سعر، أو ضريبة، مو بالضرورة صنف غلط)."""
    ref_price = getattr(ref_item, "last_purchase_price", None)
    if line_price is None or not ref_price or ref_price <= 0:
        return 0.0, None
    bigger = max(line_price, ref_price)
    smaller = min(line_price, ref_price)
    ratio = bigger / smaller
    if ratio <= 1.1:
        return 5.0, "السعر قريب جداً من آخر سعر شراء"
    if ratio <= 1.3:
        return 2.0, "السعر قريب من آخر سعر شراء"
    if ratio >= 3.0:
        return -10.0, f"⚠ فرق سعر كبير جداً ({ratio:.1f}x) - راجع يدوياً (ممكن وحدة مختلفة/خصم/ضريبة)"
    return 0.0, None


def _score_one_candidate(
    line: ExtractedLine,
    line_attrs: ItemAttributes,
    ref_item: ReferenceItem,
    ref_attrs: ItemAttributes,
    sources: set[str],
) -> MatchCandidate:
    """تقييم متعدد العوامل حقيقي: كل عامل (اسم/حجم/عدد/تعبئة/وحدة صريحة/
    ماركة/تاريخ مورد/سعر) مستقل، يضيف مكافأته لوحده بدل معادلة "اسم + بونص
    بسيط". التعارضات البنيوية (حجم/عدد/تعبئة/وحدة) تُجمَّع بسقف واحد
    (hard_cap) يُطبَّق **بعد** جمع كل المكافآت - عشان مكافأة متأخرة (مثلاً
    عدد قطع متطابق) ما "تفكّ" سقف تعارض سابق (مثلاً حجم مختلف) بالغلط.
    تعارض الماركة **ليس** له سقف صارم (راجع config.MATCH_BRAND_MISMATCH_PENALTY) -
    استخراج الماركة تخمين ضعيف بدون قاموس ماركات موثوق، فمعاملته كتعارض
    بنيوي مؤكد كان يرفض مرشّحين صحيحين بالغلط."""
    name_score = fuzz.token_sort_ratio(line.description, ref_item.name) if line.description else 0.0
    bonus = 0.0
    reason_parts = [f"الاسم {name_score:.0f}%"]
    supported = False
    hard_cap = 100.0

    size_stat = size_status(line_attrs, ref_attrs)
    if size_stat == "agree":
        bonus += 10
        reason_parts.append("الحجم/الوزن متطابق")
        supported = True
    elif size_stat == "conflict":
        hard_cap = min(hard_cap, config.MATCH_ATTRIBUTE_CONFLICT_CAP)
        reason_parts.append(
            f"⚠ اختلاف الحجم/الوزن: {line_attrs.size_value}{line_attrs.size_unit} مقابل "
            f"{ref_attrs.size_value}{ref_attrs.size_unit}"
        )

    pack_stat = pack_count_status(line_attrs, ref_attrs)
    if pack_stat == "agree":
        bonus += 8
        reason_parts.append("عدد القطع متطابق")
        supported = True
    elif pack_stat == "conflict":
        hard_cap = min(hard_cap, config.MATCH_ATTRIBUTE_CONFLICT_CAP)
        reason_parts.append(f"⚠ اختلاف عدد القطع: {line_attrs.pack_count} مقابل {ref_attrs.pack_count}")

    unit_word_stat = unit_word_status(line_attrs, ref_attrs)
    if unit_word_stat == "agree":
        bonus += 7
        reason_parts.append("نوع التعبئة متطابق")
        supported = True
    elif unit_word_stat == "conflict":
        hard_cap = min(hard_cap, config.MATCH_ATTRIBUTE_CONFLICT_CAP)
        reason_parts.append(f"⚠ اختلاف نوع التعبئة: {line_attrs.unit_word} مقابل {ref_attrs.unit_word}")

    # الوحدة الصريحة (line.unit مقابل ref_item.default_unit) - منفصلة عمداً
    # عن unit_word (المستنتج من نص الاسم)، لأن الحقل الصريح موجود حتى لو
    # الاسم نفسه ما يذكر الوحدة إطلاقاً
    line_unit_canon = canonicalize_unit_word(line.unit)
    ref_unit_canon = canonicalize_unit_word(ref_item.default_unit)
    if line_unit_canon is not None and ref_unit_canon is not None:
        if line_unit_canon == ref_unit_canon:
            bonus += 6
            reason_parts.append("الوحدة الصريحة متطابقة")
            supported = True
        elif (
            _is_packaging_tier_pair(line_unit_canon, ref_unit_canon)
            and name_score >= config.MIN_SUGGEST_THRESHOLD
            and size_stat == "agree"
            and pack_stat != "conflict"
            and unit_word_stat != "conflict"
        ):
            # كرتون/صندوق/كيس.. بالفاتورة مقابل حبة بالقاعدة (أو العكس) طبيعي
            # جداً بفواتير الجملة - القاعدة غالباً مسجّلة بسعر أصغر وحدة بيع
            # بينما المورد يفوتر بالكرتون. **قرار صريح من المستخدم**: هذا
            # وحده ليس تعارض صنف، طالما بقية الأدلة قوية وواضحة (اسم مو
            # ضعيف، حجم/وزن متطابق فعلاً، وعدد القطع/التعبئة إما متطابق أو
            # غير معروف - مو متعارض). الشرط صارم عمداً: لو أي بند ناقص أو
            # فعلاً متعارض، نرجع للسلوك القديم بالـelse أدناه (يبقى سبب مراجعة).
            reason_parts.append(
                f"ملاحظة: وحدة تعبئة مختلفة عن القاعدة بالفاتورة ({line.unit} مقابل {ref_item.default_unit}) - ليست تعارض صنف طالما باقي الأدلة متطابقة"
            )
        else:
            hard_cap = min(hard_cap, config.MATCH_ATTRIBUTE_CONFLICT_CAP)
            reason_parts.append(f"⚠ اختلاف الوحدة الصريحة: {line.unit} مقابل {ref_item.default_unit}")

    # الماركة - إشارة ضعيفة عمداً (استخراج تخميني، أول كلمة غير وصفية
    # بالنص، بدون قاموس ماركات معروفة أو أي قياس ثقة حقيقي - كلمة نوع منتج
    # عامة زي "عصير"/"حليب" ممكن تُقرأ كماركة بالغلط). موافقتها Boost خفيف
    # فقط، واختلافها عقوبة خفيفة تراكمية - **مو سقف صارم يمنع القبول
    # التلقائي بمفرده**، عشان تخمين ماركة غلط ما يرفض مرشّح صحيح فعلاً.
    if (
        line_attrs.brand
        and ref_attrs.brand
        and len(line_attrs.brand) >= _MIN_BRAND_TOKEN_LEN
        and len(ref_attrs.brand) >= _MIN_BRAND_TOKEN_LEN
    ):
        if line_attrs.brand == ref_attrs.brand:
            bonus += 5
            reason_parts.append("الماركة متطابقة (تخمين)")
            supported = True
        else:
            bonus -= config.MATCH_BRAND_MISMATCH_PENALTY
            reason_parts.append(f"الماركة مختلفة (تخمين ضعيف): {line_attrs.brand} مقابل {ref_attrs.brand}")

    if "supplier_history" in sources:
        bonus += 6
        reason_parts.append("هذا المورد باع هذا الصنف قبل")
        supported = True

    price_delta, price_note = _price_signal(line.unit_price, ref_item)
    if price_delta:
        bonus += price_delta
        if price_note:
            reason_parts.append(price_note)
        if price_delta > 0:
            supported = True

    confidence = name_score + bonus
    confidence = min(confidence, hard_cap)

    # سقف "الاسم بدون دعم" - آخر خطوة، يمنع تشابه اسم عالي صدفة من الوصول
    # لثقة قبول تلقائي بمفرده. تعارض حقيقي أصلاً يبقى تحته من الأسقف أعلاه،
    # فهذا السقف يهم بس بحالة "ما فيه أي إشارة موافقة ولا متعارضة إطلاقاً"
    if not supported:
        confidence = min(confidence, config.MATCH_NO_SUPPORT_CONFIDENCE_CAP)

    confidence = max(0.0, min(100.0, confidence))
    return MatchCandidate(
        item=ref_item,
        confidence=confidence,
        reason="، ".join(reason_parts),
        has_structural_conflict=hard_cap < 100.0,
        from_supplier_history="supplier_history" in sources,
    )


def _score_all_candidates(
    line: ExtractedLine,
    reference: list[ReferenceItem],
    reference_index: ReferenceIndex,
    supplier_confirmed_codes: set[str],
) -> list[MatchCandidate]:
    if not line.description or not reference:
        return []

    line_attrs = extract_attributes(line.description)
    hits = _retrieve_candidate_hits(line, line_attrs, reference, reference_index, supplier_confirmed_codes)
    candidate_indices = _rank_and_cap_candidates(hits, line, reference, line_attrs, reference_index)

    candidates = []
    for idx in candidate_indices:
        ref_item = reference[idx]
        ref_attrs = reference_index.attrs.get(idx) or extract_attributes(ref_item.name)
        candidates.append(_score_one_candidate(line, line_attrs, ref_item, ref_attrs, hits[idx]))

    candidates.sort(key=lambda c: c.confidence, reverse=True)
    return candidates


def _score_learned_candidate(
    line: ExtractedLine,
    line_attrs: ItemAttributes,
    learned_item: ReferenceItem,
    confirm_count: int,
) -> MatchCandidate:
    """يحسب مرشّح الذاكرة المتعلّمة بنفس فحوصات التعارض البنيوي اللي يمر
    فيها أي مرشّح عادي (حجم/عدد قطع/تعبئة مستنتجة من الاسم/وحدة صريحة) -
    عشان ذاكرة قديمة تتعارض مع بيانات الفاتورة الحالية أبداً ما توصل لقبول
    تلقائي، حتى لو ثقتها الأساسية عالية جداً من كثرة التأكيدات القديمة.
    الثقة الأساسية (confirm_count) تبقى "الأولوية القوية" المطلوبة بالتصميم -
    بس تبقى خاضعة لنفس قواعد الأمان، مو محصّنة منها (راجع suggest_candidates:
    هذا المرشّح يشارك بنفس الترتيب العادل مع الباقي، ما يتصدّر بشكل مطلق)."""
    ref_attrs = extract_attributes(learned_item.name)
    confidence = learned_matches.confidence_for_confirm_count(confirm_count)
    reason_parts = [f"مطابقة سابقة مؤكَّدة ({confirm_count} مرة)"]
    hard_cap = 100.0

    size_stat = size_status(line_attrs, ref_attrs)
    if size_stat == "conflict":
        hard_cap = min(hard_cap, config.MATCH_ATTRIBUTE_CONFLICT_CAP)
        reason_parts.append(
            f"⚠ اختلاف الحجم/الوزن: {line_attrs.size_value}{line_attrs.size_unit} مقابل "
            f"{ref_attrs.size_value}{ref_attrs.size_unit}"
        )

    pack_stat = pack_count_status(line_attrs, ref_attrs)
    if pack_stat == "conflict":
        hard_cap = min(hard_cap, config.MATCH_ATTRIBUTE_CONFLICT_CAP)
        reason_parts.append(f"⚠ اختلاف عدد القطع: {line_attrs.pack_count} مقابل {ref_attrs.pack_count}")

    unit_word_stat = unit_word_status(line_attrs, ref_attrs)
    if unit_word_stat == "conflict":
        hard_cap = min(hard_cap, config.MATCH_ATTRIBUTE_CONFLICT_CAP)
        reason_parts.append(f"⚠ اختلاف نوع التعبئة: {line_attrs.unit_word} مقابل {ref_attrs.unit_word}")

    line_unit_canon = canonicalize_unit_word(line.unit)
    ref_unit_canon = canonicalize_unit_word(learned_item.default_unit)
    if line_unit_canon is not None and ref_unit_canon is not None and line_unit_canon != ref_unit_canon:
        # نفس استثناء "كرتون بالفاتورة مقابل حبة بالقاعدة" المطبَّق بالتقييم
        # العادي (_score_one_candidate) - بدون شرط اسم هنا (المطابقة أصلاً
        # مؤكَّدة بشرياً سابقاً لنفس الوصف والمورد، هوية الصنف ليست موضع شك،
        # فقط نتأكد بقية الخصائص البنيوية ما تتعارض فعلياً).
        if _is_packaging_tier_pair(line_unit_canon, ref_unit_canon) and size_stat == "agree" and pack_stat != "conflict" and unit_word_stat != "conflict":
            reason_parts.append(
                f"ملاحظة: وحدة تعبئة مختلفة عن القاعدة بالفاتورة ({line.unit} مقابل {learned_item.default_unit}) - ليست تعارض صنف طالما باقي الأدلة متطابقة"
            )
        else:
            hard_cap = min(hard_cap, config.MATCH_ATTRIBUTE_CONFLICT_CAP)
            reason_parts.append(f"⚠ اختلاف الوحدة الصريحة: {line.unit} مقابل {learned_item.default_unit}")

    confidence = min(confidence, hard_cap)
    return MatchCandidate(
        item=learned_item,
        confidence=confidence,
        reason="، ".join(reason_parts),
        has_structural_conflict=hard_cap < 100.0,
    )


def _merge_learned_with_ordinary(
    ordinary: MatchCandidate, learned: MatchCandidate, confirm_count: int
) -> MatchCandidate:
    """يدمج دليل المطابقة العادي (اسم+خصائص+مورد+إلخ) مع دليل الذاكرة
    المتعلّمة لنفس الصنف - بدل استبدال أحدهما بالثاني بالكامل (كان يخفّض
    ثقة مرشّح قوي فعلاً بصمت لمجرد وجود تأكيد ذاكرة واحد أضعف، مثلاً من 98%
    لـ80%). الثقة النهائية = أعلى الاثنين. هذا مكافئ رياضياً لتطبيق نفس سقف
    التعارض البنيوي المشترك على المجموع (max(min(a,cap), min(b,cap)) ==
    min(max(a,b), cap))، لأن الاثنين يحسبان نفس فحص التعارض من نفس الحقائق
    (نفس line_attrs، نفس اسم الصنف) - فتعارض بنيوي حقيقي يبقى سقف فعلي، ما
    يقدر "بوست" الذاكرة يتجاوزه، بينما دليل عادي قوي فعلاً ما ينخفض بسبب
    ذاكرة أضعف."""
    if ordinary.confidence >= learned.confidence:
        winner, note_only = ordinary, True
    else:
        winner, note_only = learned, False

    reason = winner.reason
    if note_only:
        reason += f"، وأيضاً مطابقة سابقة مؤكَّدة ({confirm_count} مرة)"

    return MatchCandidate(
        item=winner.item,
        confidence=winner.confidence,
        reason=reason,
        has_structural_conflict=ordinary.has_structural_conflict or learned.has_structural_conflict,
        from_supplier_history=ordinary.from_supplier_history or learned.from_supplier_history,
    )


def suggest_candidates(
    line: ExtractedLine,
    reference: list[ReferenceItem],
    supplier_name: str | None = None,
    reference_attrs_index: ReferenceIndex | None = None,
    top_n: int = 5,
) -> list[MatchCandidate]:
    """يرجّع أفضل top_n مرشّحين لعرضهم بنافذة المراجعة. مرشّح الذاكرة
    المتعلّمة (لو موجود) يحصل على ثقة أساسية عالية (أولوية/boost قوي
    بالتصميم - راجع learned_matches.confidence_for_confirm_count)، لكنه
    **يشارك بنفس الترتيب العادل** مع باقي المرشّحين حسب الثقة الفعلية بعد
    فحص التعارضات البنيوية (_score_learned_candidate) - مو محصّن أو مثبّت
    بالمرتبة الأولى بشكل مطلق. لو نفس الصنف عنده دليل عادي (اسم/خصائص/مورد)
    ودليل ذاكرة معاً، **يُدمَجان** (_merge_learned_with_ordinary) بدل ما
    يستبدل أحدهما الثاني - ذاكرة أضعف (مثلاً تأكيد واحد بس) أبداً ما تخفّض
    ثقة دليل عادي أقوى فعلاً. ذاكرة قديمة تتعارض مع الفاتورة الحالية
    (حجم/عدد/تعبئة/وحدة مختلف فعلياً) تنزل ثقتها فعلياً، فمرشّح ثاني أسلم
    وأقوى ممكن يتصدّر بدلها - هذا يمنع enhance_one() من اعتبار ذاكرة
    متعارضة "أفضل مرشّح" بصمت."""
    if reference_attrs_index is None:
        reference_attrs_index = build_reference_attrs_index(reference)

    line_attrs = extract_attributes(line.description)
    supplier_confirmed_codes = learned_matches.codes_confirmed_for_supplier(supplier_name)
    all_candidates = _score_all_candidates(line, reference, reference_attrs_index, supplier_confirmed_codes)

    learned = learned_matches.lookup(supplier_name, line.description)
    if learned is not None:
        entry, confirm_count = learned
        learned_item = _find_by_code(reference, entry["matched_item_code"])
        if learned_item is not None:
            learned_candidate = _score_learned_candidate(line, line_attrs, learned_item, confirm_count)
            # الصنف نفسه ممكن يتكرر بعدة صفوف بالمرجع (باركود/وحدة مختلفة) -
            # لو المصادر العادية رجّعت أكثر من صف لنفس الكود، نأخذ أقواهم
            # للدمج (نفس فلسفة "أفضل صف لكل كود" المستخدمة بالدمج النهائي)
            same_code_ordinary = [c for c in all_candidates if c.item.code == learned_candidate.item.code]
            all_candidates = [c for c in all_candidates if c.item.code != learned_candidate.item.code]
            if same_code_ordinary:
                best_ordinary = max(same_code_ordinary, key=lambda c: c.confidence)
                all_candidates.append(_merge_learned_with_ordinary(best_ordinary, learned_candidate, confirm_count))
            else:
                all_candidates.append(learned_candidate)

    all_candidates.sort(key=lambda c: c.confidence, reverse=True)

    # نسمح بمرشّح "معطوب" بتعارض خصائص بنيوي، أو مرشّح من تاريخ المورد،
    # يمر رغم ثقته المنخفضة - سبب الرفض نفسه معلومة مفيدة للمراجع البشري
    # (أو "المورد باع هذا قبل" إشارة يستاهل يشوفها)، ما نبيهم يختفوا بصمت
    rest = [
        c
        for c in all_candidates
        if c.confidence >= config.MIN_SUGGEST_THRESHOLD or c.has_structural_conflict or c.from_supplier_history
    ]

    # الصنف الواحد بالمرجع ممكن يتكرر بعدة صفوف (باركود/وحدة مختلفة لكل
    # صف - راجع items.py::_load_from_amnc_xml) - كلها بنفس الاسم فتاخذ نفس
    # درجة التشابه، فتمتلئ قائمة الاقتراحات بنسخ متطابقة الشكل لنفس الصنف
    # بدل بدائل حقيقية مختلفة. نبقي أفضل صف بس لكل رقم صنف.
    seen_codes = set()
    deduped = []
    for c in rest:
        if c.item.code in seen_codes:
            continue
        seen_codes.add(c.item.code)
        deduped.append(c)

    return deduped[:top_n]


# ============ إعادة الترتيب الدلالي بالذكاء الاصطناعي (طبقة اختيارية) ============
# ملاحظة معمارية مهمة: suggest_candidates() أعلاه تبقى دالة محلية سريعة
# deterministic بدون أي استدعاء شبكي - تُستخدم لوحدها بأي مكان (خصوصاً
# الاختبارات، بدون حاجة لأي محاكاة/موك). طبقة الذكاء الاصطناعي منفصلة
# تماماً هنا (semantic_enhance_candidates) - orchestration فوق
# suggest_candidates، تُستدعى فقط من نقاط التكامل اللي تقبل تأخير شبكي
# محتمل (enhance_one، ونافذتا المراجعة بـapp.py عبر خيط خلفية).


def _should_try_semantic_rerank(candidates: list[MatchCandidate]) -> bool:
    """سياسة التفعيل: استدعِ AI فقط لو القائمة المحلية غير فارغة، و(أفضل
    مرشّح < عتبة القبول التلقائي، أو فجوة الغموض غير كافية). قائمة فارغة
    كلياً تعني مراجعة بشرية مباشرة بدون استدعاء - AI هنا مُعيد ترتيب لقائمة
    موجودة، مو محرك بحث، وممنوع يختار صنفاً غير موجود بالمرشّحين المحليين
    أصلاً. مطابقة باركود/رقم صنف مؤكدة، أو ذاكرة/محلي قوي بلا غموض ولا
    تعارض، أصلاً ما توصل هنا (تُقبل تلقائياً محلياً قبل أي تفكير باستدعاء AI)."""
    if not candidates:
        return False
    thresholds = settings_module.get_settings()
    best = candidates[0]
    if best.confidence < thresholds["auto_accept_threshold"]:
        return True
    if len(candidates) >= 2:
        gap = best.confidence - candidates[1].confidence
        min_gap = thresholds.get("min_confidence_gap", config.MATCH_MIN_CONFIDENCE_GAP)
        if gap < min_gap:
            return True
    return False


def needs_semantic_rerank(candidates: list[MatchCandidate]) -> bool:
    """واجهة عامة رفيعة فوق _should_try_semantic_rerank - يستخدمها app.py
    (بدون الاعتماد على دالة داخلية بادئتها _) عشان يقرر هل يبدأ خيط خلفية
    لإعادة الترتيب الدلالي أو يعرض المرشّحين المحليين فوراً بدون أي تأخير."""
    return _should_try_semantic_rerank(candidates)


def _diversity_family_key(candidate: MatchCandidate) -> str:
    """مفتاح "عائلة/ماركة" لصنف مرجعي - يُستخدم فقط لتنويع القائمة الموسّعة
    (بند 2). الماركة المخمَّنة (attrs.brand) أولاً لو موثوقة، وإلا أقوى
    كلمة محتوى بالاسم (_content_tokens، نفس آلية بند 5 Safety Patch #3)،
    وإلا كود الصنف نفسه (يعني يُعامَل كعائلة مستقلة - لا يتزاحم مع أي صنف
    ثاني على حصته)."""
    attrs = extract_attributes(candidate.item.name)
    if attrs.brand and len(attrs.brand) >= _MIN_BRAND_TOKEN_LEN:
        return attrs.brand
    content = _content_tokens(attrs)
    if content:
        return sorted(content)[0]
    return candidate.item.code


def _dedupe_and_diversify(
    scored: list[MatchCandidate], limit: int, max_per_family: int
) -> list[MatchCandidate]:
    """بند 2 (Safety Patch #4، 2026-08-28، Benchmark حقيقي): بدون هذا،
    عائلة/ماركة واحدة كبيرة بالكتالوج (مثلاً 5 أصناف "يومي" مختلفة، أو نفس
    الصنف مكرَّر 3 صفوف بسبب وحدات بيع مختلفة) كانت تستهلك أغلب القائمة
    الموسّعة قبل حتى وصول مرشّح صحيح من عائلة أقل تكراراً - اكتُشف فعلياً
    (Benchmark فاتورة المراعي: 'بسكويت اولكر فينجر 70جم' يحتل 3 مراتب متتالية
    بالقائمة الخام لمجرد 3 صفوف وحدة/باركود لنفس الصنف).

    خطوتان: (1) dedup بالكود - نفس الصنف المنطقي (وحدات/باركودات مختلفة)
    يبقى صفاً واحداً بأفضل ثقة له (نفس معيار suggest_candidates). (2) تنويع
    بالعائلة/الماركة - حد أقصى max_per_family لكل مفتاح، بترتيب الثقة، مع
    تعبئة أي مقاعد فاضية من الفائض المؤجَّل لو التنوع الحقيقي أقل من الحد
    (ما نضيّع مكاناً فاضياً تعسفاً). **لا تغيير على الثقة نفسها ولا على أي
    عتبة/quota إنتاجية أخرى** - فقط ترتيب/اختيار القائمة الموسّعة لـSemantic."""
    best_by_code: dict[str, MatchCandidate] = {}
    for c in scored:
        current = best_by_code.get(c.item.code)
        if current is None or c.confidence > current.confidence:
            best_by_code[c.item.code] = c
    deduped = sorted(best_by_code.values(), key=lambda c: -c.confidence)

    family_counts: dict[str, int] = defaultdict(int)
    selected: list[MatchCandidate] = []
    deferred: list[MatchCandidate] = []
    for c in deduped:
        if len(selected) >= limit:
            break
        key = _diversity_family_key(c)
        if family_counts[key] < max_per_family:
            selected.append(c)
            family_counts[key] += 1
        else:
            deferred.append(c)

    for c in deferred:
        if len(selected) >= limit:
            break
        selected.append(c)

    return selected[:limit]


def _build_wide_pool_for_semantic(
    line: ExtractedLine,
    reference: list[ReferenceItem],
    reference_attrs_index: ReferenceIndex,
    supplier_name: str | None,
) -> list[MatchCandidate]:
    """قائمة موسّعة *لـSemantic AI فقط* (بند 1، Safety Patch #4، 2026-08-28):
    تشمل مرشّحين حقيقيين من الاسترجاع حتى لو ثقتهم المحلية تحت عتبة عرض UI
    (config.MIN_SUGGEST_THRESHOLD) - **لا تؤثر على UI ولا على قواعد القبول
    التلقائي إطلاقاً**، فقط تعطي AI صورة أوسع/أدق لتحليلها لما لا يوجد Match
    قوي محلياً (تُستدعى فقط من semantic_enhance_candidates بعد
    _should_try_semantic_rerank، نفس سياسة التفعيل الموجودة أصلاً - صفر
    تغيير عليها). كل مرشّح هنا جاء فعلياً من _score_all_candidates (نفس
    شبكة الاسترجاع/التقييم الحقيقية المستخدمة بكل مكان ثانٍ - صفر اختراع
    أصناف)، بعد dedup+تنويع (_dedupe_and_diversify، بند 2)."""
    supplier_confirmed_codes = learned_matches.codes_confirmed_for_supplier(supplier_name)
    all_scored = _score_all_candidates(line, reference, reference_attrs_index, supplier_confirmed_codes)
    return _dedupe_and_diversify(all_scored, _SEMANTIC_WIDE_POOL_SIZE, _SEMANTIC_WIDE_POOL_MAX_PER_FAMILY)


def _build_shortlist_for_semantic(
    candidates: list[MatchCandidate], limit: int | None = None
) -> list[MatchCandidate]:
    """يبني قائمة مختصرة متنوعة المصادر لإرسالها لـAI - مو بس أفضل N
    بالثقة المحلية (ممكن يفقد تنوّع مهم، مثلاً مرشّح تاريخ مورد ذو صلة
    حقيقية لكن ثقته المحلية أقل من عدة مرشّحين اسم). نحجز شرائح صغيرة
    لمصادر مميّزة أولاً (تاريخ مورد، تعارض بنيوي - يستاهل AI يشوفه كـ"قريب
    لكن فيه مشكلة")، ثم نعبّي الباقي بالأعلى ثقة عموماً (يغطي تلقائياً
    مرشّحي الاسم/الخصائص القوية، لأن القائمة مرتّبة بالثقة أصلاً)."""
    if limit is None:
        limit = config.SEMANTIC_RERANK_SHORTLIST_SIZE
    if len(candidates) <= limit:
        return list(candidates)

    selected: list[MatchCandidate] = []
    selected_codes: set[str] = set()

    def take(predicate, quota):
        count = 0
        for c in candidates:
            if len(selected) >= limit or count >= quota:
                return
            if c.item.code in selected_codes or not predicate(c):
                continue
            selected.append(c)
            selected_codes.add(c.item.code)
            count += 1

    take(lambda c: c.from_supplier_history, 3)
    take(lambda c: c.has_structural_conflict, 3)

    for c in candidates:
        if len(selected) >= limit:
            break
        if c.item.code in selected_codes:
            continue
        selected.append(c)
        selected_codes.add(c.item.code)

    return selected[:limit]


def _semantic_confidence_boost(ai_confidence: float, ai_ambiguous: bool) -> float:
    """صيغة محافظة عمداً - رقم ثقة AI الخام أبداً ما يساوي ثقة نظامية
    مباشرة. لو AI نفسه أعلن ambiguous=true، صفر بوست (عدم يقين AI ما
    يستاهل رفع ثقة النظام). قيم متحفّظة (8/4/2) مقارنة بإصدار أول اقترحناه
    (15/8/3) - AI بالإصدار الأول أقوى بإعادة الترتيب منه برفع الثقة المطلقة."""
    if ai_ambiguous:
        return 0.0
    if ai_confidence >= 85:
        return 8.0
    if ai_confidence >= 70:
        return 4.0
    if ai_confidence >= 50:
        return 2.0
    return 0.0


_STRONG_LOCAL_SUPPORT_MARKERS = (
    "الحجم/الوزن متطابق",
    "عدد القطع متطابق",
    "نوع التعبئة متطابق",
    "الوحدة الصريحة متطابقة",
    "مطابقة سابقة مؤكَّدة",
)


def _has_strong_local_support(candidate: MatchCandidate) -> bool:
    """"أدلة محلية قوية وواضحة" - شرط إضافي مطلوب عشان AI يُسمح له يساهم
    بعبور عتبة القبول التلقائي (راجع _merge_semantic_result). نطلب إشارتين
    بنيويتين موافقتين على الأقل (أو ذاكرة متعلّمة + إشارة وحدة مثلاً) -
    مجرد "عدم وجود تعارض" لا يكفي، لازم موافقة فعلية مذكورة صراحة بالسبب."""
    hits = sum(1 for marker in _STRONG_LOCAL_SUPPORT_MARKERS if marker in candidate.reason)
    return hits >= 2


def _merge_semantic_result(
    local_candidates: list[MatchCandidate],
    wide_candidates: list[MatchCandidate],
    ai_result: semantic_matcher.SemanticRerankResult,
    auto_accept_threshold: float,
) -> tuple[list[MatchCandidate], bool]:
    """يدمج نتيجة AI مع القائمة المحلية. يرجّع (القائمة المحدَّثة،
    ai_was_deciding_factor). العلَم الثاني = True فقط لو AI كان **السبب
    الوحيد** لعبور عتبة القبول التلقائي (المرشّح كان تحتها محلياً، وما فيه
    أدلة محلية قوية داعمة كافية) - بهذا الإصدار الأول، هذي الحالة تبقى
    تحتاج مراجعة بشرية إلزامياً (قرار محافظ متعمَّد، راجع enhance_one).

    Semantic Visibility (بند 1، Safety Patch #4، 2026-08-28): AI يختار من
    wide_candidates (القائمة الموسّعة اللي شافها فعلياً - قد تشمل مرشّحين
    تحت عتبة عرض UI)، مو local_candidates وحدها - عشان اختياره لصنف صحيح
    لكن ضعيف الثقة محلياً ما يُرفض بصمت لمجرد إنه غير موجود بالقائمة
    الأضيق. **لكن** local_candidates تبقى الأساس المُرجَع/المعروض فعلياً -
    مرشّح من wide_candidates فقط (غير موجود أصلاً بـlocal_candidates، يعني
    ثقته المحلية كانت تحت عتبة UI) لا يُضاف للقائمة المُرجَعة إلا لو ثقته
    *بعد* بوست AI تعبر **نفس عتبة العرض بالضبط** المستخدمة بـsuggest_candidates
    (MIN_SUGGEST_THRESHOLD أو تعارض بنيوي أو تاريخ مورد) - نفس القاعدة
    الموجودة أصلاً، مو قاعدة أضعف جديدة. AI "يشوف" أكثر لتحليل أفضل، هذا لا
    يعني UI "يعرض" أكثر تلقائياً."""
    selected = next((c for c in wide_candidates if c.item.code == ai_result.selected_code), None)
    if selected is None:
        # دفاع مضاعف - matching_engine ما يفترض صحة رد semantic_matcher عمياً
        # حتى لو فحصه هو نفسه أصلاً (طبقتان أمان أفضل من طبقة وحدة)
        return local_candidates, False

    boost = _semantic_confidence_boost(ai_result.confidence, ai_result.ambiguous)
    local_hard_cap = config.MATCH_ATTRIBUTE_CONFLICT_CAP if selected.has_structural_conflict else 100.0
    was_below_threshold_locally = selected.confidence < auto_accept_threshold
    boosted_confidence = min(selected.confidence + boost, local_hard_cap, 100.0)

    was_in_local_pool = any(c.item.code == selected.item.code for c in local_candidates)
    if not was_in_local_pool:
        clears_ui_display_bar = (
            boosted_confidence >= config.MIN_SUGGEST_THRESHOLD
            or selected.has_structural_conflict
            or selected.from_supplier_history
        )
        if not clears_ui_display_bar:
            # AI حلّله فعلاً (استُخدم بتحليل بقية المرشّحين وربما تجنّب
            # اختيار مرشّح خاطئ آخر)، لكن ثقته النهائية ما زالت أضعف من
            # عتبة عرض UI - يبقى مخفياً عن القائمة المعروضة، بلا استثناء
            return local_candidates, False

    ai_was_deciding_factor = (
        was_below_threshold_locally
        and boosted_confidence >= auto_accept_threshold
        and not _has_strong_local_support(selected)
    )

    reason_note = f"AI semantic rerank: {ai_result.reason}" if ai_result.reason else "AI semantic rerank: تشابه دلالي قوي"
    if ai_result.ambiguous:
        reason_note += " (AI نفسه غير متأكد)"

    boosted = MatchCandidate(
        item=selected.item,
        confidence=boosted_confidence,
        reason=f"{selected.reason}  |  {reason_note}",
        has_structural_conflict=selected.has_structural_conflict,
        from_supplier_history=selected.from_supplier_history,
    )

    updated = [c for c in local_candidates if c.item.code != ai_result.selected_code]
    updated.append(boosted)
    updated.sort(key=lambda c: c.confidence, reverse=True)
    return updated, ai_was_deciding_factor


def semantic_enhance_candidates(
    line: ExtractedLine,
    reference: list[ReferenceItem],
    supplier_name: str | None = None,
    reference_attrs_index: ReferenceIndex | None = None,
    top_n: int = 5,
) -> tuple[list[MatchCandidate], bool]:
    """طبقة orchestration: تحسب المرشّحين المحليين أولاً (سريع، بدون شبكة)،
    وبس لو سياسة التفعيل قالت الحالة صعبة فعلاً، تستدعي semantic_matcher.rerank()
    (قد يستغرق ثوانٍ - المتصل مسؤول يستدعيها من خيط خلفية لو حساس للتجميد،
    راجع app.py). يرجّع (المرشّحين النهائيين بعد top_n، ai_was_deciding_factor).

    مهم (إصلاح خطأ حقيقي): حجم القائمة المحلية المستخدمة لبناء shortlist
    الذكاء الاصطناعي **مستقل تماماً** عن top_n (عدد النتائج المعروضة
    للمستخدم بنهاية الدالة، يختلف حسب نقطة الاستدعاء - 2/3/5). لو استُخدم
    top_n نفسه لجلب القائمة المحلية، AI ما كان يشوف إلا نفس العدد القليل
    جداً المعروض أصلاً - يفقد بالضبط المرشّح الصحيح لو صياغة اسمه بعيدة
    كفاية عن الفاتورة إنه يترتّب برقم 6 أو 10 محلياً (بالضبط الحالة اللي
    هذي الطبقة أُنشئت عشانها). نجلب pool محلي بحجم
    SEMANTIC_RERANK_SHORTLIST_SIZE على الأقل (suggest_candidates تبقى محلية
    deterministic - top_n هنا مجرد قصّ نهائي على قائمة محسوبة مسبقاً، ما
    يغيّر أي تقييم/ترتيب)، ونقصّ لـtop_n فقط بالنهاية بعد الدمج."""
    pool_size = max(top_n, config.SEMANTIC_RERANK_SHORTLIST_SIZE, 2)
    local_pool = suggest_candidates(line, reference, supplier_name, reference_attrs_index, top_n=pool_size)

    if not _should_try_semantic_rerank(local_pool):
        return local_pool[:top_n], False

    thresholds = settings_module.get_settings()
    line_attrs = extract_attributes(line.description)
    # Semantic Visibility (بند 1، Safety Patch #4، 2026-08-28): وصلنا هنا
    # فقط لأن local_pool (محكوم بعتبة عرض UI) ضعيف/غامض فعلاً
    # (_should_try_semantic_rerank أعلاه) - AI يبني shortlist من wide_pool
    # (مرشّحون حقيقيون من الاسترجاع، منوَّعون، حتى لو تحت عتبة UI) بدل
    # local_pool وحدها، عشان مرشّح صحيح رتبته منخفضة محلياً (مثلاً 13) ما
    # يُحجَب عن AI بصمت. القائمة *المُرجَعة* تبقى محكومة بنفس عتبة UI (راجع
    # _merge_semantic_result) - هذا يوسّع ما يشوفه AI، مو ما يعرضه UI.
    wide_pool = _build_wide_pool_for_semantic(line, reference, reference_attrs_index, supplier_name)
    shortlist = _build_shortlist_for_semantic(wide_pool)

    ai_inputs = []
    for c in shortlist:
        ref_attrs = extract_attributes(c.item.name)
        ai_inputs.append(
            semantic_matcher.SemanticCandidateInput(
                code=c.item.code,
                name=c.item.name,
                barcode=c.item.barcode,
                unit=c.item.default_unit,
                size_value=ref_attrs.size_value,
                size_unit=ref_attrs.size_unit,
                pack_count=ref_attrs.pack_count,
                unit_word=ref_attrs.unit_word,
                local_confidence=c.confidence,
                local_reason=c.reason,
            )
        )

    ai_result = semantic_matcher.rerank(
        description=line.description,
        supplier_name=supplier_name,
        quantity=line.quantity,
        unit=line.unit,
        unit_price=line.unit_price,
        size_value=line_attrs.size_value,
        size_unit=line_attrs.size_unit,
        pack_count=line_attrs.pack_count,
        unit_word=line_attrs.unit_word,
        candidates=ai_inputs,
    )

    if ai_result is None:
        return local_pool[:top_n], False

    merged, ai_was_deciding_factor = _merge_semantic_result(
        local_pool, wide_pool, ai_result, thresholds["auto_accept_threshold"]
    )
    return merged[:top_n], ai_was_deciding_factor


def enhance_one(
    line: ExtractedLine,
    reference: list[ReferenceItem],
    supplier_name: str | None = None,
    reference_attrs_index: ReferenceIndex | None = None,
    allow_semantic: bool = True,
) -> None:
    """يعيد حساب مطابقة سطر ما تطابق بالباركود من الصفر. لو allow_semantic=True
    (الافتراضي، للتوافق الخلفي مع الاختبارات القديمة)، يحاول أيضاً إعادة
    ترتيب دلالي بالذكاء الاصطناعي للحالات الصعبة (راجع semantic_enhance_candidates).

    قاعدة معمارية صريحة: مسار الاستخراج الفعلي (_extract_one_invoice بـapp.py)
    يستدعي هذي الدالة بـallow_semantic=False دائماً - الاستخراج محلي بحت،
    صفر نداء شبكي لـSemantic AI، حتى لو يشتغل بخيط خلفية أصلاً (كان التبرير
    السابق: "تأخير شبكي محتمل آمن هنا" لأنه خيط خلفية - لكن هذا يعني كل سطر
    "يحتاج مراجعة" بكل فاتورة بالدفعة ينتظر نداء AI متسلسل، وهذا فعلياً كان
    سبب بطء قراءة الفواتير الرئيسي، مو مجرد نظري - قياس فعلي: نداء AI واحد
    ~4.5 ثانية مقابل ~0.08 ثانية للمطابقة المحلية البحتة لنفس السطر). AI يبقى
    حصراً بنافذة المراجعة (_open_review_dialog بـapp.py) عبر خيط خلفية غير
    محاجب - ذاك المسار غير ملموس هنا ولا يستدعي enhance_one إطلاقاً.

    يُطبَّق التطابق فعلياً (matched_item_code وغيره) بس لو تحقق **كل** مما يلي:
    1. ثقة أفضل مرشّح >= عتبة القبول التلقائي.
    2. الفرق عن ثاني أفضل مرشّح كافٍ (فجوة غموض - config.MATCH_MIN_CONFIDENCE_GAP).
    3. لو AI ساهم بالثقة، ما يكون **السبب الوحيد** لعبور العتبة بدون أدلة
       محلية قوية داعمة (راجع _merge_semantic_result - قرار محافظ متعمَّد
       بهذا الإصدار الأول).
    غير كذا يبقى matched_item_code فاضي (نفس مبدأ الأداة: لا تخمّن) بس
    match_score/match_reason يتعبّون لعرض أقرب اقتراح بجدول المراجعة."""
    if reference_attrs_index is None:
        reference_attrs_index = build_reference_attrs_index(reference)

    if allow_semantic:
        candidates, ai_was_deciding_factor = semantic_enhance_candidates(
            line, reference, supplier_name, reference_attrs_index, top_n=2
        )
    else:
        candidates = suggest_candidates(
            line, reference, supplier_name, reference_attrs_index, top_n=2
        )
        ai_was_deciding_factor = False
    if not candidates:
        # نفس الإصلاح أدناه (راجع تعليق الفرع else) - صفر مرشّحين محليين
        # يعني صفر ثقة إطلاقاً، فأي تخمين قديم من items.py::match_line_items
        # يُمسح هنا أيضاً بدل ما يبقى يوهم بثقة غير موجودة
        line.matched_item_code = ""
        line.matched_item_name = ""
        line.matched_internal_id = ""
        line.matched_unit_id = ""
        line.needs_review = True
        return

    best = candidates[0]
    thresholds = settings_module.get_settings()
    line.match_reason = best.reason

    ambiguous = False
    if len(candidates) >= 2:
        gap = best.confidence - candidates[1].confidence
        min_gap = thresholds.get("min_confidence_gap", config.MATCH_MIN_CONFIDENCE_GAP)
        if gap < min_gap:
            ambiguous = True

    if best.confidence >= thresholds["auto_accept_threshold"] and not ambiguous and not ai_was_deciding_factor:
        _apply_match(line, best.item, best.confidence)
    else:
        # REAL BUG FIX (اكتُشف 2026-08-27 عبر benchmark بقائمة أصناف حقيقية):
        # items.py::match_line_items (الأساس، يشتغل قبل هذي الدالة على كل
        # سطر بدون باركود) عنده مطابق تشابه-اسم مستقل وأضعف (AUTO_MATCH_THRESHOLD
        # ~80، تشابه اسم فقط بدون فحص حجم/عدد/تعارضات) - ممكن يملأ
        # matched_item_code بتخمين واثق ظاهرياً (needs_review=False) قبل ما
        # هذي الدالة (المحرك الأدق) تعيد الحساب من الصفر وتكتشف غموض حقيقي
        # (مرشّح ثاني قريب، أو تعارض بنيوي). سابقاً: كنا نكتفي بضبط
        # needs_review=True بدون مسح matched_item_code القديم - فيبقى السطر
        # بحالة متناقضة (كود موجود + يحتاج مراجعة)، و_populate_table() بـ
        # app.py يوجّهه لجدول "مطابَقة" (يعتمد فقط bool(matched_item_code))
        # رغم إن المحرك الذكي فعلياً غير واثق - يخفي شك حقيقي عن المستخدم.
        # الإصلاح: أي قرار "يحتاج مراجعة" يمسح أي تخمين قديم مسح كامل، بدل
        # ما يتركه يوهم الواجهة/التصدير بثقة غير موجودة - نفس مبدأ "لا تخمّن"
        # المطبَّق بكل مكان ثاني بالمشروع.
        line.matched_item_code = ""
        line.matched_item_name = ""
        line.matched_internal_id = ""
        line.matched_unit_id = ""
        line.match_score = best.confidence
        line.needs_review = True
        if ambiguous:
            line.match_reason += f"  |  ⚠ مرشّح ثاني قريب جداً ({candidates[1].confidence:.0f}%) - يحتاج مراجعة بشرية"
        if ai_was_deciding_factor:
            line.match_reason += (
                "  |  ⚠ الذكاء الاصطناعي هو سبب عبور عتبة القبول لوحده بدون أدلة "
                "محلية قوية كافية - يحتاج مراجعة بشرية"
            )
