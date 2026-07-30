# Turing

🇬🇧 English documentation: [README.md](README.md)

[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![Django](https://img.shields.io/badge/django-4.2%2B-092E20.svg)](https://www.djangoproject.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)
[![Status](https://img.shields.io/badge/status-beta-orange.svg)](https://pypi.org/project/django-turing/)

**Turing** یک بستهٔ قابل نصب برای جنگو است که هوش گفتاری را در اختیار محصولات شما قرار می‌دهد: بارگذاری صوت، رونویسی گفتار، بازبینی رونوشت ساخت‌یافته و استخراج بینش با هوش مصنوعی. یک موتور واحد می‌تواند جلسات، تماس‌های CRM، مصاحبه‌ها و فایل‌های صوتی را بدون جدا کردن کدبیس برای هر مشتری پوشش دهد.

این نام از آلن تورینگ برگرفته شده و بیانگر هدف ساخت سامانه‌هایی است که ارتباط انسانی را در مقیاس درک کنند.

## ویژگی‌ها

- **دریافت رسانه** — بارگذاری فایل صوتی یا ثبت URL خارجی؛ ذخیره‌سازی سازگار با S3 و لینک‌های امضاشده
- **گفتار به متن** — رونویسی دسته‌ای با Speechmatics و تفکیک گوینده
- **پردازش ناهمزمان** — خط لولهٔ Celery با ارسال، نظرسنجی و ذخیره‌سازی ایدمپوتنت
- **رونوشت ساخت‌یافته** — بخش‌ها، گویندگان، زمان‌بندی واژه‌ای، اطمینان و تاریخچهٔ ویرایش
- **گردش کار بازبینی** — ویرایش انسانی با ردپای ممیزی و وضعیت‌های تأیید
- **تحلیل هوش مصنوعی** — خلاصه، موضوعات و اقدامات پیشنهادی بدون تغییر متن اصلی
- **چندمستأجری** — سازمان‌ها، عضویت‌ها و محدودسازی API بر اساس مستأجر
- **REST API** — رسانه، کارها، رونوشت‌ها، تحلیل‌ها، جستجو، کانکتورها و وب‌هوک
- **مرکز گفتار (Speech Center)** — رابط نمایشی نمونه و API برای جستجوی رونوشت و هوش گفتاری
- **کانکتورها** — یکپارچه‌سازی OAuth (Zoom، Teams، Google Meet، Salesforce، Twilio و غیره)
- **جستجوی معنایی و RAG** — بردارگذاری بخش‌ها و پرسش‌وپاسخ روی رونوشت‌ها

## معماری

```text
┌─────────────┐     ┌──────────────┐     ┌─────────────┐     ┌──────────────┐
│  بارگذاری / │     │   آماده‌سازی │     │  گفتار به   │     │   رونوشت +   │
│   کانکتور   │ ──► │     صوت      │ ──► │    متن      │ ──► │  گویندگان    │
└─────────────┘     └──────────────┘     └─────────────┘     └──────┬───────┘
                                                                    │
                    ┌──────────────┐     ┌──────────────┐           ▼
                    │ خروجی /      │ ◄── │ تحلیل AI +   │ ◄── بازبینی و ویرایش
                    │  وب‌هوک      │     │   جستجو      │
                    └──────────────┘     └──────────────┘
```

جزئیات طراحی، نقشهٔ ماژول‌ها و محدودیت‌ها: [docs/architecture.md](docs/architecture.md)

## فناوری‌ها

| لایه | فناوری |
|------|--------|
| فریم‌ورک | Django 4.2+ |
| API | Django REST Framework |
| کارهای پس‌زمینه | Celery + Redis |
| ارائه‌دهنده STT | Speechmatics Batch API |
| پایگاه داده | SQLite (محلی) / PostgreSQL (تولید) |
| ذخیره‌سازی | فایل‌سیستم محلی یا S3 |

## نصب

**پیش‌نیاز:** Python 3.11+، Redis (برای Celery در محیط تولید).

```bash
git clone <repository-url>
cd turing
pip install -e ".[dev]"
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

- **پنل مدیریت:** http://127.0.0.1:8000/admin/
- **پایه API:** http://127.0.0.1:8000/api/turing/v1/
- **رابط Speech Center:** http://127.0.0.1:8000/speech-center/

تنظیمات پیش‌فرض: `config.settings` (توسعهٔ محلی).

### نصب به‌عنوان بسته در پروژهٔ جنگوی دیگر

```python
INSTALLED_APPS = [
  # ...
  "rest_framework",
  "django_filters",
  "turing.apps.TuringConfig",
]

urlpatterns += [
  path("api/turing/", include("turing.api.urls")),
]
```

مایگریشن‌های `turing` را در پروژهٔ میزبان اجرا کنید.

## شروع سریع

1. در **Admin → Speech provider configs** کلید API مربوط به Speechmatics را وارد کنید.
2. فایل صوتی را در **Media assets** بارگذاری کنید (یا از صفحهٔ بارگذاری Speech Center استفاده کنید).
3. یک **Processing job** با کد زبان (مثلاً `fa` یا `en`) ایجاد کنید.
4. worker مربوط به Celery را اجرا کنید (مسیر تولید):

   ```bash
   celery -A config worker -l info -Q turing.default,turing.high,turing.export
   ```

5. پس از اتمام کار، **Transcript** را باز کنید — بخش‌ها، گویندگان و تحلیل‌های اختیاری از طریق Admin و REST API در دسترس‌اند.

با فعال بودن `Platform configuration → auto_enqueue` (پیش‌فرض)، کارها بلافاصله پس از ایجاد در صف قرار می‌گیرند.

## پیکربندی

| موضوع | محل تنظیم |
|-------|-----------|
| اعتبار ارائه‌دهنده | Admin → Speech provider configs (رمزنگاری‌شده در پایگاه داده) |
| پیش‌فرض‌ها (زبان، محدودیت بارگذاری) | Admin → Platform configuration |
| رازها و TLS در تولید | متغیرهای محیطی — [docs/deployment.md](docs/deployment.md) |
| ذخیره‌سازی شیء | `django-storages` / S3 — [docs/media-storage.md](docs/media-storage.md) |

جایگزین اختیاری از محیط:

```bash
export TURING_SPEECHMATICS_API_KEY=your-key
```

اولویت: **رمز پایگاه داده → متغیر محیطی → خطای پیکربندی**.

فرمت‌های صوتی پیش‌فرض: `mp3`، `wav`، `m4a`، `webm`، `ogg`.

## REST API

endpointهای احراز هویت‌شده زیر `/api/turing/v1/` (نشست یا توکن):

| منبع | مسیر |
|------|------|
| رسانه | `/media/` |
| کارها | `/jobs/` (`retry`، `cancel`، `logs`) |
| رونوشت‌ها | `/transcripts/` (`revisions`، `submit_review`) |
| بخش‌ها و گویندگان | `/segments/`، `/speakers/` |
| تحلیل‌ها | `/analyses/` |
| Speech Center | `/speech-center/` (جستجو، timeline، intelligence، ask) |
| جستجو | `/search/` |
| کانکتورها | `/connectors/` |
| وب‌هوک | `/webhooks/` |
| ارائه‌دهندگان | `/providers/` |

بازخورد ارائه‌دهنده: `POST /api/turing/v1/webhooks/speechmatics/`

## مستندات

| سند | شرح |
|-----|-----|
| [docs/architecture.md](docs/architecture.md) | طراحی سامانه و نقشهٔ ماژول‌ها |
| [docs/deployment.md](docs/deployment.md) | تنظیمات و بهره‌برداری تولید |
| [docs/async-pipeline.md](docs/async-pipeline.md) | وظایف Celery، تلاش مجدد، ایدمپوتنسی |
| [docs/media-storage.md](docs/media-storage.md) | بارگذاری، بک‌اند ذخیره‌سازی، URL امضاشده |
| [docs/webhooks.md](docs/webhooks.md) | وب‌هوک ورودی ارائه‌دهنده و ارسال خروجی |
| [docs/authorization-tenancy.md](docs/authorization-tenancy.md) | سازمان‌ها، نقش‌ها، محدودسازی API |

راهنماهای تکمیلی: [هوش رونوشت](docs/transcript-intelligence.md)، [API Speech Center](docs/speech-center-api.md)، [جستجوی معنایی](docs/search.md)، [کانکتورها](docs/connectors.md)، [دریافت صوت](docs/audio-ingestion.md)، [رویدادها](docs/events.md).

## نقشه راه

- رونویسی جریانی بلادرنگ
- ارائه‌دهندگان STT و embedding بیشتر
- یکپارچه‌سازی عمیق‌تر با CRM و محصولات جلسه
- رابط نصب کانکتور (مارکت‌پلیس)
- لایهٔ صورتحساب و سطح دسترسی

## مجوز

MIT — جزئیات در [pyproject.toml](pyproject.toml).
