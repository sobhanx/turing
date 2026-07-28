"""
Persian (fa) presentation strings for Django Admin.

Presentation layer only — does not change models, APIs, or schema.
Technical identifiers (UUIDs, provider codes, paths) stay English.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Model / page titles (admin index & breadcrumbs)
# ---------------------------------------------------------------------------

MODEL_TITLES: dict[str, tuple[str, str]] = {
    # object_name -> (verbose_name, verbose_name_plural)
    "MediaAsset": ("رسانه", "رسانه‌ها"),
    "ProcessingJob": ("پردازش", "پردازش‌ها"),
    "ProcessingAttempt": ("تلاش پردازش", "تلاش‌های پردازش"),
    "ProcessingLog": ("گزارش پردازش", "گزارش‌های پردازش"),
    "Transcript": ("متن استخراج‌شده", "متن‌های استخراج‌شده"),
    "Speaker": ("گوینده", "گویندگان"),
    "TranscriptSegment": ("بخش متن", "بخش‌های متن"),
    "TranscriptWord": ("کلمه", "کلمات"),
    "TranscriptRevision": ("نسخه متن", "نسخه‌های متن"),
    "ReviewAssignment": ("ارجاع بازبینی", "ارجاع‌های بازبینی"),
    "TranscriptAnalysis": ("تحلیل", "تحلیل‌ها"),
    "SpeechProviderConfig": ("ارائه‌دهنده گفتار", "ارائه‌دهندگان گفتار"),
    "PlatformConfiguration": ("پیکربندی سامانه", "پیکربندی سامانه"),
    "Organization": ("سازمان", "سازمان‌ها"),
    "TuringMembership": ("عضویت", "عضویت‌ها"),
    "ConnectorInstallation": ("اتصال", "اتصال‌ها"),
    "ConnectorCredential": ("اعتبارنامه اتصال", "اعتبارنامه‌های اتصال"),
    "ConnectorSyncJob": ("همگام‌سازی اتصال", "همگام‌سازی‌های اتصال"),
    "WebhookSubscription": ("اشتراک وب‌هوک", "اشتراک‌های وب‌هوک"),
    "WebhookDelivery": ("ارسال وب‌هوک", "ارسال‌های وب‌هوک"),
    "ExternalReference": ("ارجاع خارجی", "ارجاع‌های خارجی"),
    "Embedding": ("بردار معنایی", "بردارهای معنایی"),
    "MediaProcessingArtifact": ("فایل پردازش‌شده", "فایل‌های پردازش‌شده"),
    "OutboxEvent": ("رویداد خروجی", "رویدادهای خروجی"),
}

# ---------------------------------------------------------------------------
# Field labels
# ---------------------------------------------------------------------------

FIELD_LABELS: dict[str, str] = {
    "id": "شناسه",
    "original_filename": "نام فایل",
    "source_type": "نوع منبع",
    "use_case": "کاربرد",
    "file": "فایل",
    "object_key": "کلید ذخیره‌سازی",
    "content_type": "نوع محتوا",
    "byte_size": "حجم",
    "duration_ms": "مدت زمان",
    "sample_rate_hz": "نرخ نمونه‌برداری",
    "channels": "تعداد کانال",
    "audio_format": "قالب صدا",
    "audio_codec": "کدک صدا",
    "checksum": "چک‌سام",
    "external_url": "نشانی خارجی",
    "uploaded_by": "بارگذاری‌کننده",
    "organization": "سازمان",
    "tenant_key": "کلید مستأجر",
    "metadata": "فراداده",
    "storage_backend": "محل ذخیره‌سازی",
    "created_at": "زمان ایجاد",
    "updated_at": "آخرین بروزرسانی",
    "display_name": "نام نمایشی",
    "display_duration": "مدت زمان",
    "display_size": "حجم",
    "language_code": "زبان",
    "status": "وضعیت",
    "full_text": "متن کامل",
    "version": "نسخه",
    "is_primary": "متن اصلی",
    "confidence_avg": "میانگین اطمینان",
    "word_count": "تعداد کلمات",
    "approved_at": "زمان تأیید",
    "approved_by": "تأییدکننده",
    "job": "پردازش",
    "media": "رسانه",
    "speaker_label": "شناسه گوینده",
    "speaker_name": "نام گوینده",
    "external_speaker_id": "شناسه خارجی",
    "confidence": "میزان اطمینان",
    "transcript": "متن",
    "speaker": "گوینده",
    "sequence": "ترتیب",
    "start_ms": "شروع",
    "end_ms": "پایان",
    "text": "متن",
    "words": "کلمات",
    "provider_payload": "داده ارائه‌دهنده",
    "is_edited": "ویرایش‌شده",
    "segment": "بخش",
    "revision_number": "شماره نسخه",
    "source": "منبع",
    "change_summary": "خلاصه تغییرات",
    "snapshot": "تصویر لحظه‌ای",
    "diff": "تفاوت",
    "created_by": "ایجادکننده",
    "assignee": "مسئول",
    "assigned_by": "ارجاع‌دهنده",
    "due_at": "مهلت",
    "analysis_type": "نوع تحلیل",
    "content": "محتوا",
    "provider": "ارائه‌دهنده",
    "model_name": "مدل",
    "capability": "قابلیت",
    "provider_code": "کد ارائه‌دهنده",
    "external_job_id": "شناسه پردازش خارجی",
    "idempotency_key": "کلید یکتایی",
    "attempt_count": "تعداد تلاش",
    "max_attempts": "حداکثر تلاش",
    "error_code": "کد خطا",
    "error_message": "پیام خطا",
    "options": "گزینه‌ها",
    "queued_at": "زمان صف",
    "started_at": "زمان شروع",
    "finished_at": "زمان پایان",
    "attempt_number": "شماره تلاش",
    "level": "سطح",
    "message": "پیام",
    "context": "زمینه",
    "attempt": "تلاش",
    "code": "کد",
    "name": "نام",
    "is_active": "فعال",
    "priority": "اولویت",
    "api_key": "کلید API",
    "base_url": "نشانی پایه",
    "default_language": "زبان پیش‌فرض",
    "operating_point": "نقطه عملکرد",
    "enable_diarization": "تفکیک گوینده",
    "extra_options": "گزینه‌های اضافی",
    "default_provider_code": "ارائه‌دهنده پیش‌فرض",
    "enable_diarization_default": "تفکیک گوینده پیش‌فرض",
    "auto_enqueue": "صف‌بندی خودکار",
    "default_max_attempts": "حداکثر تلاش پیش‌فرض",
    "poll_interval_seconds": "فاصله نظرسنجی (ثانیه)",
    "poll_timeout_seconds": "مهلت نظرسنجی (ثانیه)",
    "poll_timeout_multiplier": "ضریب مهلت نظرسنجی",
    "normalization_enabled": "نرمال‌سازی فعال",
    "max_duration_ms": "حداکثر مدت (میلی‌ثانیه)",
    "max_upload_bytes": "حداکثر حجم بارگذاری",
    "allowed_audio_extensions": "پسوندهای مجاز صدا",
    "allowed_audio_mime_types": "MIMEهای مجاز صدا",
    "api_require_auth": "الزام احراز هویت API",
    "api_page_size": "اندازه صفحه API",
    "webhook_mode": "حالت وب‌هوک",
    "webhook_base_url": "نشانی پایه وب‌هوک",
    "notes": "یادداشت",
    "slug": "نامک",
    "external_key": "کلید خارجی",
    "user": "کاربر",
    "role": "نقش",
    "connector_type": "نوع اتصال",
    "config": "پیکربندی",
    "auth_type": "نوع احراز هویت",
    "expires_at": "انقضا",
    "last_refreshed_at": "آخرین تازه‌سازی",
    "revoked_at": "زمان لغو",
    "connector_installation": "نصب اتصال",
    "installation": "نصب",
    "records_processed": "رکوردهای پردازش‌شده",
    "error": "خطا",
    "url": "نشانی",
    "secret": "رمز امضا",
    "subscribed_events": "رویدادهای مشترک",
    "subscription": "اشتراک",
    "outbox_event": "رویداد خروجی",
    "attempts": "تلاش‌ها",
    "recovery_count": "تعداد بازیابی",
    "response_status_code": "کد پاسخ",
    "response_body_preview": "پیش‌نمایش پاسخ",
    "last_error": "آخرین خطا",
    "processing_started_at": "شروع پردازش",
    "delivered_at": "زمان تحویل",
    "external_system": "سامانه خارجی",
    "external_type": "نوع خارجی",
    "external_id": "شناسه خارجی",
    "target_kind": "نوع هدف",
    # Computed / admin-only
    "status_badge": "وضعیت",
    "confidence_display": "میزان اطمینان",
    "overview_panel": "خلاصه",
    "browser_links": "مرور",
    "media_link": "رسانه",
    "message_short": "پیام",
    "text_short": "متن",
    "start_display": "شروع",
    "end_display": "پایان",
    "duration_display": "مدت زمان",
    "transcript_display": "متن",
    "speaker_display": "گوینده",
    "api_key_display": "کلید API",
    "secret_display": "رمز",
    "subscribed_events_display": "رویدادها",
    "config_public": "پیکربندی (ماسک‌شده)",
    "credential_summary": "اعتبارنامه",
    "last_sync_display": "آخرین همگام‌سازی",
    "has_access": "توکن دسترسی",
    "has_refresh": "توکن تازه‌سازی",
    "event_name": "رویداد",
    "organization_display": "سازمان",
}

# ---------------------------------------------------------------------------
# Help texts (admin forms)
# ---------------------------------------------------------------------------

FIELD_HELP: dict[str, str] = {
    "original_filename": (
        "نام نمایشی این رسانه. به‌صورت خودکار از نام فایل بارگذاری‌شده پر می‌شود؛ "
        "می‌توانید آن را ویرایش کنید بدون آنکه مسیر ذخیره‌شده تغییر کند."
    ),
    "speaker_label": "شناسه داخلی تفکیک گوینده (غیرقابل ویرایش).",
    "speaker_name": "نام نمایشی قابل ویرایش. در صورت خالی بودن، شناسه گوینده نمایش داده می‌شود.",
    "external_speaker_id": "شناسه گوینده در سامانه خارجی (اختیاری).",
    "file": "فایل صوتی را قبل از ذخیره بارگذاری کنید.",
    "api_key": (
        "کلید جدید را برای جایگزینی وارد کنید. "
        "خالی بگذارید تا کلید فعلی حفظ شود. "
        "مقادیر ذخیره‌شده رمزنگاری می‌شوند."
    ),
    "secret": (
        "رمز جدید را برای جایگزینی وارد کنید. "
        "خالی بگذارید تا رمز فعلی حفظ شود. "
        "مقادیر ذخیره‌شده رمزنگاری می‌شوند و هرگز کامل نمایش داده نمی‌شوند."
    ),
}

# ---------------------------------------------------------------------------
# Choice / status labels (values stay English in DB)
# ---------------------------------------------------------------------------

STATUS_LABELS: dict[str, str] = {
    # Job
    "pending": "در انتظار",
    "queued": "در صف",
    "running": "در حال پردازش",
    "succeeded": "تکمیل شد",
    "failed": "ناموفق",
    "cancelled": "لغو شد",
    "partial": "ناقص",
    "submitted": "ارسال شد",
    "processing": "در حال پردازش",
    "completed": "تکمیل شد",
    "retrying": "تلاش مجدد",
    # Transcript
    "draft": "پیش‌نویس",
    "in_review": "در حال بازبینی",
    "approved": "تأییدشده",
    "archived": "بایگانی",
    # Review
    "in_progress": "در حال انجام",
    "changes_requested": "نیاز به اصلاح",
    "rejected": "ردشده",
    # Source / storage / analysis
    "upload": "بارگذاری",
    "url": "نشانی خارجی",
    "stream": "جریان",
    "local": "محلی",
    "s3": "AWS S3",
    "azure": "Azure Blob",
    "gcs": "Google Cloud Storage",
    "summary": "خلاصه",
    "action_items": "اقدامات",
    "topics": "موضوعات",
    # Use cases
    "meeting": "جلسه",
    "crm_call": "تماس CRM",
    "interview": "مصاحبه",
    "voice_file": "فایل صوتی",
    "generic": "عمومی",
    # Roles
    "admin": "مدیر",
    "reviewer": "بازبین",
    "editor": "ویرایشگر",
    "user": "کاربر",
    "viewer": "بیننده",
    # Logs
    "debug": "اشکال‌زدایی",
    "info": "اطلاعات",
    "warning": "هشدار",
    "error": "خطا",
    # Artifacts / deliveries
    "ready": "آماده",
    "skipped": "ردشده",
    "normalized": "نرمال‌شده",
}

# ---------------------------------------------------------------------------
# UI chrome
# ---------------------------------------------------------------------------

SITE_HEADER = "پنل عملیات تورینگ"
SITE_TITLE = "تورینگ"
INDEX_TITLE = "داشبورد عملیات گفتار"
APP_LABEL = "هوش گفتار تورینگ"
SEARCH_PLACEHOLDER = "جستجو..."
EMPTY_VALUE = "—"

EMPTY_SPEAKERS = "گوینده‌ای وجود ندارد."
EMPTY_TRANSCRIPT = "متنی وجود ندارد."
EMPTY_SEGMENTS = "بخشی وجود ندارد."
EMPTY_WORDS = "کلمه‌ای وجود ندارد."
EMPTY_ANALYSES = "تحلیلی وجود ندارد."
EMPTY_GENERIC = "موردی یافت نشد."

BTN_VIEW_SEGMENTS = "مشاهده بخش‌ها"
BTN_VIEW_WORDS = "مشاهده کلمات"
BTN_VIEW_ANALYSIS = "مشاهده تحلیل"
BTN_VIEW_INTELLIGENCE = "مشاهده هوش مصنوعی"

OVERVIEW_MEDIA = "رسانه"
OVERVIEW_DURATION = "مدت زمان"
OVERVIEW_LANGUAGE = "زبان"
OVERVIEW_SPEAKERS = "گویندگان"
OVERVIEW_SEGMENTS = "بخش‌ها"
OVERVIEW_WORDS = "کلمات"

MSG_UPLOAD_REQUIRED = "لطفاً قبل از ذخیره، فایل رسانه را بارگذاری کنید."
MSG_URL_REQUIRED = "برای رسانه مبتنی بر نشانی، وارد کردن نشانی خارجی الزامی است."
MSG_FILENAME_PLACEHOLDER = "به‌صورت خودکار از فایل بارگذاری‌شده پر می‌شود"

FILTER_EVENT = "رویداد"
FILTER_ATTEMPTS = "تلاش‌ها"


def status_label(code: str | None, fallback: str | None = None) -> str:
    if not code:
        return fallback or EMPTY_VALUE
    return STATUS_LABELS.get(str(code), fallback or str(code))


def model_titles(object_name: str) -> tuple[str, str] | None:
    return MODEL_TITLES.get(object_name)
