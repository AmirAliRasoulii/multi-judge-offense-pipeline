# پایپ‌لاین چهارمدلی توهین با AvalAI

این نسخه بخش داوری داده را اجرا می‌کند: چهار مدل مستقل → پاسخ کوتاه و JSON → اعتبارسنجی → اجماع و پرچم → JSONL/CSV/اکسل. فارسی و انگلیسی با یک تعریف بررسی می‌شوند. فقط داوری به API می‌رود؛ کش، بررسی قالب، استخراج شاهد، رأی‌گیری، صف بازبینی و خروجی با پایتون انجام می‌شوند.

دادهٔ خصوصی شما در این بسته نیست. فایل نمونه و رأی‌های demo ساختگی‌اند و برای بررسی نرم‌افزارند. این نسخه روی دادهٔ واقعی یا اتصال احراز هویت‌شدهٔ AvalAI آزمایش نشده است؛ برای شروع آن‌ها باید کلید، چهار مدل و فایل ورودی خودتان را تنظیم کنید. مدل‌ها از پیش انتخاب نشده‌اند.

## شروع

Python 3.10 یا جدیدتر لازم است. از پوشهٔ همین پروژه اجرا کنید:

```bash
python -m venv .venv
# Linux / macOS:
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
cp .env.example .env
```

در ویندوز فایل `.env.example` را با نام `.env` کپی کنید. در `.env` این پنج مقدار را پر کنید:

```dotenv
AVALAI_API_KEY=YOUR_KEY
MODEL_1=EXACT_MODEL_ID_1
MODEL_2=EXACT_MODEL_ID_2
MODEL_3=EXACT_MODEL_ID_3
MODEL_4=EXACT_MODEL_ID_4
```

شناسه‌ها باید متفاوت باشند. برنامه نمی‌تواند متفاوت‌بودن خانواده یا وزن‌های پشت دو نام را اثبات کند؛ انتخاب خانواده‌های مختلف و کیفیت فارسی/انگلیسی با پایلوت انجام می‌شود. کلید را داخل پرامپت، EXTRA_JSON یا فایل داده قرار ندهید.

فهرست مدل‌های موجود:

```bash
python -m avalai_offense models --public
python -m avalai_offense models --env .env
```

پرامپت مستقل در `prompts/classifier.md` است. مسیر آن با `PROMPT_FILE` تغییر می‌کند. قواعد بازبینی در `config/review_policy.json` هستند. مسیرهای این دو فایل نسبت به پوشهٔ فایل `.env` حل می‌شوند. متغیرهای محیط سیستم بر `.env` اولویت دارند. parser فایل env عمداً ساده است: مقادیر تک‌خطی، نقل‌قول اختیاری؛ بدون interpolation یا اجرای کد shell. برای توضیح از خط جداگانهٔ `#` استفاده کنید.

## قالب ورودی

CSV با UTF-8، JSONL و XLSX پشتیبانی می‌شوند. فقط `text` الزامی است؛ `id`، `context`، `language` و `source` اختیاری‌اند. ستون‌های اضافه عیناً در metadata حفظ می‌شوند؛ برای تاریخ از رشتهٔ ISO استفاده کنید. XLSX از شیت اول/فعال خوانده می‌شود و نباید فرمول داشته باشد.

```json
{"id":"fa-001","text":"این تحلیل ضعیف است و شواهد کافی ندارد.","context":"","language":"fa","source":"private_collection"}
```

`context` فقط برای تفسیر متن هدف استفاده می‌شود. توهین در context به‌تنهایی برچسب متن هدف را ۱ نمی‌کند. بدون context، زمینهٔ فرضی ساخته نمی‌شود. اگر `id` ندهید، شناسه از شمارهٔ ردیف و hash ساخته می‌شود. شناسهٔ تکراری و متن خالی خطای ورودی هستند و قبل از فراخوانی API رد می‌شوند.

متن خام نگه داشته می‌شود و همان متن به داور می‌رود. `text_normalized` فقط نسخهٔ جدا با Unicode NFC است؛ حذف ایموجی، نفی، فاصله یا شکل پوشیدهٔ فحش انجام نمی‌شود. offsets شاهد روی **متن خام** و بر حسب کاراکتر پایتون هستند، نه byte یا UTF-16؛ نام فیلد text/context نیز ثبت می‌شود.

## اجرای اولیه و ادامهٔ اجرا

```bash
# فقط اعتبارسنجی و برآورد تعداد درخواست؛ بدون شبکه و بدون نیاز به کلید:
python -m avalai_offense dry-run --env .env --input examples/sample_input.csv

# اولین اجرای واقعی روی ۲۰ ردیف در یک پوشهٔ مجزا:
python -m avalai_offense run --env .env --input data.csv --limit 20 --output outputs/pilot20

# اجرای اصلی:
python -m avalai_offense run --env .env --input data.csv --output outputs/main

# اگر قطع شد، همان دستور را دوباره اجرا کنید؛ رأی‌های موفق دوباره ارسال نمی‌شوند.
# برای امتحان دوبارهٔ رأی‌های شکست‌خورده:
python -m avalai_offense run --env .env --input data.csv --output outputs/main --retry-failed

# فقط بازسازی خروجی از SQLite، بدون هیچ فراخوانی مدل:
python -m avalai_offense export --run-dir outputs/main
```

نام ستون متفاوت را با `--text-column comment --id-column record_id --context-column parent_text` تنظیم کنید. `--language-column` و `--source-column` هم موجودند.

هر رأی و هر تلاش API در SQLite ذخیره می‌شود. یک پردازش در هر پوشهٔ اجرا مجاز است. برای resume ورودی، ترتیب ردیف‌ها، پرامپت، schema، مدل‌ها، تنظیمات مدل و review policy باید همان باشند؛ در صورت تغییر، پوشهٔ جدید بسازید. افزودن ردیف یا تغییر `--limit` نیز اجرای جدید است. تغییر کلید API به‌تنهایی رأی‌های موفق را بی‌اعتبار نمی‌کند.

تکرار **دقیق متن + context + زبان** همان چهار درخواست را بازاستفاده می‌کند، ولی هر ردیف و شناسهٔ اصلی حفظ می‌شود. این چهار رأی کش‌شده، داوران تازه‌ای برای ردیف تکراری نیستند. `duplicate_of` و `content_hash` ثبت می‌شوند. near-duplicate، anonymization، split، ترجمه، تولید سفیدها و gold دوبرچسب‌گذار در این نسخه پیاده نشده‌اند.

## تنظیمات AvalAI و خروجی مدل

آدرس پیش‌فرض `https://api.avalai.ir/v1` با Bearer auth است. مسیر پیش‌فرض Chat Completions است. برای هر مدل جدا تنظیم کنید:

| تنظیم | مقادیر / کاربرد |
|---|---|
| `MODEL_1_API` تا `MODEL_4_API` | `chat` یا `responses`، بسته به پشتیبانی مدل در AvalAI |
| `MODEL_n_OUTPUT_MODE` | `reasoning_json` پیش‌فرض؛ `json_object` یا `json_schema` در مدل/مسیر پشتیبان |
| `MODEL_n_MAX_TOKENS` | پیش‌فرض ۴۰۹۶؛ شامل بودجهٔ reasoning داخلی در مدل‌های مربوط است |
| `MODEL_n_TOKEN_FIELD` | در chat: `max_completion_tokens` یا برای مدل سازگار `max_tokens` |
| `MODEL_n_REASONING_EFFORT` | پیش‌فرض خالی؛ فقط اگر پشتیبانی می‌شود مثلاً `low` تنظیم کنید |
| `MODEL_n_EXTRA_JSON` | پارامترهای اختیاری provider، مثلاً `{"temperature":0}` **فقط در مدل سازگار** |
| `MAX_WORKERS` | تعداد درخواست هم‌زمان؛ پیش‌فرض ۴. چهار داور هر ردیف موازی‌اند و ردیف بعد پس از پایان آن شروع می‌شود |
| `REQUEST_TIMEOUT` | timeout درخواست؛ پیش‌فرض ۱۲۰ ثانیه |
| `MAX_RETRIES` | پیش‌فرض ۲؛ حداکثر ۳ تلاش برای هر رأی در هر بار retry |
| `REQUESTS_PER_MINUTE` | صفر یعنی بدون محدودیت نرم‌افزاری؛ محدودیت واقعی حساب/provider همچنان برقرار است |
| `MAX_API_CALLS` | سقف تلاش‌های API در هر invocation؛ صفر یعنی نامحدود، retry هم شمرده می‌شود |
| `MODERATION_MODEL` | مدل moderation برای شکستن تساوی ۲/۲ (پیش‌فرض `omni-moderation-latest`؛ خالی برای غیرفعال‌سازی) |

هیچ temperature یا reasoning effort ناسازگار به‌صورت اجباری فرستاده نمی‌شود. برای مدل‌های reasoning اگر خروجی به سقف برسد، رأی معتبر نیست؛ سقف را در اجرای جدید بالاتر ببرید یا تنظیمات مدل را اصلاح کنید. `json_object` فقط نحو JSON را محدود می‌کند؛ schema همیشه در پایتون بررسی می‌شود. اگر `json_schema` پشتیبانی نشود، برنامه خودکار به قالب دیگری تغییر نمی‌کند.

پیش‌فرض پاسخ قابل‌خواندن:

```text
متن، نقد استدلال است و حمله شخصی ندارد.
FINAL_JSON:
{"label":0,"p_offensive_raw":0.05,"abuse_types":[],"target_types":["none"],"target_basis":["none"],"discourse_tags":["criticism"],"expression":"none","speaker_stance":"neutral","profanity_present":false,"needs_context":false,"evidence":[],"decision_reason":"نقد استدلال بدون توهین شخصی است."}
```

این reasoning فقط **یک دلیل کوتاه برای تصمیم** است؛ زنجیرهٔ فکر خصوصی یا توضیح مفصل درخواست نمی‌شود. در حالت JSON خالص دلیل در `decision_reason` باقی می‌ماند. پاسخ خام، توضیح قبل JSON و reasoning اضافی provider در صورت بازگرداندن، جدا ذخیره می‌شوند. `<think>` در استخراج رأی کنار گذاشته می‌شود، اما نسخهٔ خام حفظ می‌شود.

قالب سخت‌گیرانه در `avalai_offense/schema.py` تعریف شده و snapshot آن داخل manifest ذخیره می‌شود. label رشته‌ای، boolean به‌جای ۰/۱، NaN، کلید اضافه/تکراری، امتیاز خارج از بازه، چند JSON تصمیم و پاسخ ناقص پذیرفته نمی‌شوند. JSON خراب را نرم‌افزار با حدس برچسب «اصلاح» نمی‌کند؛ از همان داور retry محدود گرفته می‌شود، بدون نشان‌دادن رأی دیگران.

منابع رسمی بررسی‌شده: [AvalAI Chat](https://docs.avalai.org/en/api-reference/chat)، [Responses](https://docs.avalai.org/en/api-reference/responses)، [Structured outputs](https://docs.avalai.org/en/guides/structured-outputs)، [Provider parameters](https://docs.avalai.org/en/guides/provider-specific-params)، [Models](https://docs.avalai.org/en/api-reference/models). سازگاری هر شناسه با مسیر و پارامتر انتخابی را با اجرای کوچک واقعی بررسی کنید.

## برچسب‌ها، confidence و پرچم‌ها

`label` و `candidate_label` برچسب اولیهٔ اجماع‌اند؛ برای استفادهٔ آموزشی **`final_label`** را انتخاب کنید. هر چهار رأی معتبر و دودویی لازم است:

| آرای مثبت / منفی | candidate | رفتار پیش‌فرض |
|---|---:|---|
| ۴/۰ | ۱ | پذیرش Silver اگر flag مسدودکننده ندارد؛ ۲٪ ممیزی قطعی با hash |
| ۳/۱ | ۱ | flag اختلاف؛ برچسب Silver می‌تواند نهایی شود، مگر سایر قواعد آن را مسدود کنند |
| ۲/۲ | null (یا ۱/۰ با moderation) | در صورت فعال بودن MODERATION_MODEL تساوی با مدریشن حل می‌شود (`moderation_tiebreak`)؛ در غیر این صورت زرد، نهایی خالی تا حل انسانی |
| ۱/۳ | ۰ | مانند ۳/۱ با برچسب ۰ |
| ۰/۴ | ۰ | مانند ۴/۰ با برچسب ۰ |
| رأی ناقص/نامعتبر | null | قرمز؛ هیچ خطایی رأی ۰ نیست |
| رأی معتبر با label=null | null | ابهام/امتناع از تصمیم، نهایی خالی |

نقل‌قول، گزارش، کاربرد آموزشی، counterspeech و reclaimed usage، نیاز به context، confidence پایین/ناسازگار، شاهد ساختگی و اختلاف مهم subtype، بازبینی مسدودکننده دارند. حتی اجماع ۴/۴ و امتیاز بالا این پرچم‌ها را حذف نمی‌کند. نقل‌قول از تگ هر مدل یا نشانهٔ سطحی جفت گیومه گرفته می‌شود؛ گیومهٔ صرف می‌تواند بیش‌ازحد پرچم ایجاد کند و حذف دستی لازم شود. این heuristic گزارش‌های بدون گیومه را کامل پوشش نمی‌دهد؛ تشخیص آن‌ها وابسته به داوران است.

`review_flags` تمام دلایل را نگه می‌دارد. اولویت رنگ: خطا قرمز، تساوی/رأی نامعلوم زرد، بازبینی نارنجی، پذیرفته‌شده خنثی، رأی انسانی واردشده سبز. رنگ تنها حامل اطلاعات نیست.

`p_offensive_raw` امتیاز خوداظهاری‌شدهٔ مثبت است؛ `score_type=self_reported`. برای رأی ۱، confidence همان p و برای رأی ۰، `1-p` است. آستانهٔ اولیهٔ ۰٫۷ فقط قاعدهٔ صف است، **کالیبره یا معیار علمی تثبیت‌شده نیست**. امتیازها میانگین نمی‌شوند و تساوی را حل نمی‌کنند. برای کالیبراسیون و انتخاب آستانه باید بعداً dev انسانی داشته باشیم.

تگ‌های abuse چندبرچسبی‌اند: `profanity_use`، `insult`، `derogatory_mockery`، `hate_speech`. فحش بدون مخاطب با `untargeted_profanity` نیز در discourse ثبت می‌شود. `profanity_present` مستقل از label است. اهداف، مبنای هدف، صریح/ضمنی، موضع گوینده و شواهد هر داور جدا حفظ می‌شوند. union تگ‌ها برای مرور است، نه اجماع یا حقیقت نهایی. اختلاف subtype بین رأی‌های مثبت بررسی می‌شود. **حتی بعد از بازبینی binary، تگ‌ها model_only می‌مانند.**

## فایل‌های خروجی

| فایل | محتوا |
|---|---|
| `results.xlsx` | شیت Results: برچسب‌ها و flags و چهار مدل؛ Votes: رأی تفصیلی؛ Summary: شمارش و راهنما |
| `results.jsonl` | رکورد کامل با متن اصلی، metadata، آرای چهار مدل و منشأ برچسب |
| `votes.jsonl` | یک رأی برای هر ردیف/مدل، همراه امتیاز، تگ و دلیل |
| `raw_responses.jsonl` | همه تلاش‌ها، پاسخ خام، خطا، usage و زمان؛ شکست‌ها نیز حفظ می‌شوند |
| `results.csv` | نمای تخت برای تحلیل |
| `review_queue.csv` | ردیف‌های نیازمند بررسی |
| `clean.csv` | فقط final_label=0/1؛ شامل Silver و بازبینی انسانی، نه صرفاً Gold |
| `state.sqlite` | checkpoint، کش آرا و ثبت بازبینی؛ برای resume نگه دارید |
| `manifest.json` | مدل‌ها/تنظیمات بدون کلید، hash ورودی، prompt و schema و قواعد |
| `input_snapshot.jsonl` / `prompt_snapshot.md` | ورودی ثابت و نسخهٔ پرامپت |
| `summary.json` | شمارش‌ها و توکن‌های گزارش‌شده، شامل retry؛ قیمت فرضی تولید نمی‌شود |
| `review_log.jsonl` | سابقهٔ ورود/تغییر رأی انسانی |

هیچ null در clean.csv نیست. آن را به‌عنوان «دادهٔ بی‌خطا» یا gold نام‌گذاری نکنید؛ `quality_tier` و `needs_review` را همراه نگه دارید. دو annotation مستقل و adjudication برای Gold هنوز مرحلهٔ جدا هستند. subset آمادهٔ بدون flag را با `needs_review=false` فیلتر کنید.

متن CSV که بتواند فرمول شود با apostrophe محافظت می‌شود؛ متن دقیق در JSONL موجود است. در XLSX متن به‌صورت string نوشته می‌شود. به علت محدودیت Excel، رشته‌های بسیار بلند در نمایش اکسل کوتاه و کنترل‌های نامعتبر حذف می‌شوند؛ تعداد کوتاه‌شدن در Summary ثبت می‌شود و متن کامل در JSONL باقی می‌ماند. فرمول یا محاسبهٔ مدل در اکسل نداریم.

## بازبینی انسانی

از `results.xlsx` یک **کپی** با نام مثلاً `reviewed.xlsx` بسازید. فقط `human_label`، `reviewer` و `review_note` را ویرایش کنید؛ ستون‌های سبز تیره عنوان قابل‌ویرایش‌اند. برچسب ۰ معتبر است و با خالی فرق دارد. برای باقی‌گذاشتن ابهام، label را خالی بگذارید. پس از ورود رأی:

```bash
python -m avalai_offense review --run-dir outputs/main --workbook outputs/main/reviewed.xlsx
```

برنامه run_id، شناسه، hash و متن را بررسی می‌کند و کل workbook را قبل از ذخیرهٔ رأی اعتبارسنجی می‌کند. خطای provenance باعث ورود جزئی رأی‌ها نمی‌شود. فرمول پذیرفته نیست. candidate و رأی‌های مدل عوض نمی‌شوند؛ final_label با رأی انسانی واردشده به‌روز می‌شود و سابقه نگه داشته می‌شود. فایل results.xlsx بازسازی می‌شود؛ نسخهٔ reviewed.xlsx حفظ می‌شود. واردکردن یک رأی انسانی، به معنی دو رأی مستقل یا gold نیست.

این فایل **رابط بازبینی با نمایش رأی مدل** است. برای annotation مستقل و کور انسانی، استفاده از این نما ممکن است سوگیری ایجاد کند؛ آن مرحله در این نسخه ساخته نشده است.

## آزمایش آفلاین

```bash
python -m unittest discover -s tests -v
python -m avalai_offense demo --output outputs/demo
```

Demo شامل نقد سالم، توهین، نقل‌قول، آموزشی، ۲/۲، ۳/۱، خطای عمدی، اطمینان کم، تکرار و متن آغازشده با `=` است. هیچ کلید یا شبکه‌ای مصرف نمی‌کند. برای تست دوباره بعد از تغییر schema/prompt از پوشهٔ demo جدید استفاده کنید.

دادهٔ اصلی و خروجی‌ها خصوصی‌اند؛ برای هر رأی text، context و language به AvalAI و provider مدل ارسال می‌شود. این نسخه در پایان هیچ فایل یا دیتاستی را عمومی منتشر نمی‌کند. GPU در این مرحلهٔ API استفاده نمی‌شود.
