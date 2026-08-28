# مختبر Promptfoo - تقييم مطوّر فقط

**ليس جزءاً من التطبيق النهائي.** أداة تطوير محلية تقارن من يختار "الصنف
الصحيح" لنفس سطر الفاتورة + نفس قائمة المرشّحين (top-15 من
`matching_engine.suggest_candidates` الحقيقية):

- **local**: أفضل مرشّح محلي (بلا أي استدعاء API - صفر تكلفة).
- **claude_semantic**: `semantic_matcher.rerank()` الحقيقية (نفس الدالة
  المستخدمة بالتطبيق فعلاً - صفر منطق مكرَّر).
- **gemini_stub**: placeholder فقط، يرجّع `NOT_IMPLEMENTED` صراحة - Gemini
  غير مُدمَج بالمشروع حالياً (لا مفتاح، لا مكتبة). لتفعيله لاحقاً: أضف مكتبة
  Gemini الرسمية واستبدل جسم `providers/gemini_provider.py`.

Ground Truth حقيقي 100% - من فواتير مؤكَّدة فعلياً بالمشروع
(`test_invoices/` و`test_invoices/dataset/*/`)، **مو بيانات مصطنعة**.

## التشغيل

```
# 1) ولّد بيانات الاختبار من الفواتير المؤكَّدة (يحتاج Vision cache موجود
#    مسبقاً بكل مجلد فاتورة - شغّل tools/benchmark_invoices.py مرة بدون
#    --skip-vision أولاً لو ما فيه)
python promptfoo/generate_dataset.py

# 2) شغّل التقييم (يحتاج Node.js/npx مثبَّت، ويحتاج مفتاح Anthropic API
#    الحقيقي بـanthropic_api_key.txt - نفس اللي يستخدمه التطبيق - لأن
#    claude_semantic يستدعي API حقيقي فعلياً، بتكلفة حقيقية صغيرة)
cd promptfoo
$env:PROMPTFOO_PYTHON = "D:\scanar\.venv\Scripts\python.exe"   # PowerShell
npx promptfoo@latest eval -c promptfooconfig.yaml

# 3) اعرض النتائج بواجهة محلية
npx promptfoo@latest view
```

## ملاحظات مهمة

- **صفر بيانات تُرسل لخدمة خارجية غير ضرورية**: local لا يستدعي أي شبكة
  إطلاقاً. claude_semantic يستدعي Claude فقط (نفس ما يستدعيه التطبيق
  أصلاً، بنفس المفتاح/الكاش المحليين - `semantic_rerank_cache.json`).
  gemini_stub لا يستدعي أي شي (NOT_IMPLEMENTED).
- **التكلفة**: نتائج معاد استخدامها من كاش `semantic_matcher.py` الموجود
  أصلاً (لو سبق تشغيل `--with-semantic` على نفس البيانات) - صفر تكلفة
  إضافية بأغلب الحالات. حالات جديدة فعلياً تكلّف نفس تكلفة استدعاء AI عادي
  بالتطبيق (سنتات قليلة لكل سطر).
- **`generate_dataset.py` يحتاج Vision cache** (`.benchmark_cache/*.vision_raw.json`)
  موجود مسبقاً بكل مجلد فاتورة - لا يستدعي Vision من جديد أبداً (صفر تكلفة
  Vision إضافية لهذا المختبر).
- **مقارنة "بلا اختصار باركود" عمداً**: كل الحالات هنا تمر عبر
  `suggest_candidates` الكاملة (تجاهل اختصار الباركود المباشر) - يقيس قوة
  المطابقة بالاسم/الخصائص وحدها، حتى لو الإنتاج الحقيقي كان سيحسم السطر
  فوراً بالباركود. هذا مقصود (يفصل جودة المطابقة عن حظ توفّر باركود)، ليس
  عيباً بالمختبر.
- `results.json`/`tests.json` نواتج محلية (ما تُرفع لـgit - راجع
  `.gitignore` بجذر المشروع).
