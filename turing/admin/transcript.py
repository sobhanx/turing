from __future__ import annotations

from django.contrib import admin, messages
from django.db.models import Count
from django.urls import reverse
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe

from turing.admin import fa as fa_labels
from turing.admin.authz import (
    AppendOnlyBrowseAdminMixin,
    CapabilityGatedAdminMixin,
    admin_assert_capability,
    admin_scope_queryset,
)
from turing.admin.formatting import format_confidence_pct, format_timestamp_ms
from turing.admin.persian import PersianAdminMixin, PersianInlineMixin
from turing.domain.enums import TranscriptStatus
from turing.domain.exceptions import PermissionDeniedError, ValidationError
from turing.models import (
    ReviewAssignment,
    Speaker,
    Transcript,
    TranscriptRevision,
    TranscriptSegment,
    TranscriptWord,
)
from turing.services.transcript import TranscriptService


class SpeakerInline(PersianInlineMixin, admin.TabularInline):
    """Edit display names only — never segments/words, never diarization labels."""

    model = Speaker
    extra = 0
    verbose_name = fa_labels.MODEL_TITLES["Speaker"][0]
    verbose_name_plural = fa_labels.MODEL_TITLES["Speaker"][1]
    fields = (
        "speaker_label",
        "speaker_name",
        "confidence_display",
        "external_speaker_id",
    )
    readonly_fields = ("speaker_label", "confidence_display")
    ordering = ("speaker_label",)
    show_change_link = True
    can_delete = False

    def has_add_permission(self, request, obj=None) -> bool:
        return False

    @admin.display(description=fa_labels.FIELD_LABELS["confidence"])
    def confidence_display(self, obj: Speaker) -> str:
        return format_confidence_pct(obj.confidence if obj else None)


class TranscriptRevisionInline(PersianInlineMixin, admin.TabularInline):
    model = TranscriptRevision
    extra = 0
    verbose_name = fa_labels.MODEL_TITLES["TranscriptRevision"][0]
    verbose_name_plural = fa_labels.MODEL_TITLES["TranscriptRevision"][1]
    readonly_fields = (
        "revision_number",
        "source",
        "change_summary",
        "created_by",
        "created_at",
    )
    can_delete = False
    ordering = ("-revision_number",)
    show_change_link = True

    def has_add_permission(self, request, obj=None) -> bool:
        return False


@admin.register(Transcript)
class TranscriptAdmin(PersianAdminMixin, CapabilityGatedAdminMixin, admin.ModelAdmin):
    """
    Lightweight transcript change page.

    Segments and words are browsed via dedicated changelists (not inlines) so
    large production transcripts never blow past form field limits.
    """

    turing_view_capability = "view_transcript"
    turing_change_capability = "edit_transcript"
    turing_add_capability = "edit_transcript"
    turing_delete_capability = "edit_transcript"

    list_display = (
        "id",
        "status_badge",
        "language_code",
        "version",
        "word_count",
        "confidence_display",
        "is_primary",
        "organization",
        "media",
        "updated_at",
    )
    list_filter = ("status", "is_primary", "language_code", "organization", "created_at")
    search_fields = ("id", "full_text", "media__original_filename", "job__id")
    list_select_related = ("media", "organization", "job")
    list_per_page = 50
    readonly_fields = (
        "overview_panel",
        "browser_links",
        "job",
        "media",
        "organization",
        "full_text",
        "version",
        "word_count",
        "confidence_avg",
        "approved_at",
        "approved_by",
        "created_at",
        "updated_at",
    )
    fieldsets = (
        (
            "نمای کلی",
            {
                "fields": ("overview_panel", "browser_links"),
                "description": (
                    "بخش‌ها و کلمات در فهرست‌های جداگانه باز می‌شوند و "
                    "به‌صورت توکار در این صفحه ویرایش نمی‌شوند."
                ),
            },
        ),
        (
            "گردش‌کار",
            {
                "fields": (
                    "status",
                    "language_code",
                    "is_primary",
                    "approved_at",
                    "approved_by",
                ),
            },
        ),
        (
            "منبع",
            {
                "fields": ("job", "media", "organization", "version", "word_count", "confidence_avg"),
            },
        ),
        (
            "متن کامل",
            {
                "classes": ("collapse",),
                "fields": ("full_text",),
            },
        ),
        (
            "زمان‌ها",
            {
                "classes": ("collapse",),
                "fields": ("created_at", "updated_at"),
            },
        ),
    )
    autocomplete_fields = ("organization",)
    inlines = [SpeakerInline, TranscriptRevisionInline]
    actions = (
        "submit_for_self_review",
        "mark_approved",
        "return_to_draft",
    )

    def get_queryset(self, request):
        return admin_scope_queryset(
            super()
            .get_queryset(request)
            .select_related("media", "organization", "job", "approved_by")
            .annotate(
                _segment_count=Count("segments", distinct=True),
                _speaker_count=Count("speakers", distinct=True),
            ),
            request.user,
        )

    def save_model(self, request, obj, form, change):
        capability = "edit_transcript"
        if change and "status" in form.changed_data:
            if obj.status == TranscriptStatus.APPROVED:
                capability = "approve_transcript"
            elif obj.status == TranscriptStatus.IN_REVIEW:
                capability = "edit_transcript"
        try:
            admin_assert_capability(
                request.user,
                organization=obj.organization,
                capability=capability,
            )
        except PermissionDeniedError as exc:
            self.message_user(request, str(exc), messages.ERROR)
            return
        admin.ModelAdmin.save_model(self, request, obj, form, change)

    @admin.display(description=fa_labels.FIELD_LABELS["status"])
    def status_badge(self, obj: Transcript):
        colors = {
            TranscriptStatus.DRAFT: "#6c757d",
            TranscriptStatus.IN_REVIEW: "#0d6efd",
            TranscriptStatus.APPROVED: "#198754",
            TranscriptStatus.ARCHIVED: "#adb5bd",
        }
        return format_html(
            '<span style="padding:2px 8px;border-radius:4px;background:{};color:#fff;">{}</span>',
            colors.get(obj.status, "#6c757d"),
            fa_labels.status_label(obj.status, obj.get_status_display()),
        )

    @admin.display(description=fa_labels.FIELD_LABELS["confidence"])
    def confidence_display(self, obj: Transcript):
        return format_confidence_pct(obj.confidence_avg)

    @admin.display(description="خلاصه")
    def overview_panel(self, obj: Transcript):
        if not obj or not obj.pk:
            return fa_labels.EMPTY_TRANSCRIPT
        media_name = "—"
        duration = "—"
        if obj.media_id:
            media_name = (
                obj.media.original_filename
                or obj.media.object_key
                or str(obj.media_id)
            )
            duration = format_timestamp_ms(obj.media.duration_ms)
        speakers = getattr(obj, "_speaker_count", None)
        if speakers is None:
            speakers = obj.speakers.count()
        segments = getattr(obj, "_segment_count", None)
        if segments is None:
            segments = obj.segments.count()
        words = obj.word_count
        if not words:
            words = TranscriptWord.objects.filter(segment__transcript_id=obj.pk).count()
        rows = (
            (fa_labels.OVERVIEW_MEDIA, media_name),
            (fa_labels.OVERVIEW_DURATION, duration),
            (fa_labels.OVERVIEW_LANGUAGE, obj.language_code or "—"),
            (fa_labels.OVERVIEW_SPEAKERS, str(speakers)),
            (fa_labels.OVERVIEW_SEGMENTS, f"{segments:,}"),
            (fa_labels.OVERVIEW_WORDS, f"{words:,}"),
        )
        body = format_html_join(
            "",
            "<tr><th style='text-align:right;padding:4px 0 4px 12px;color:#666;'>"
            "{}</th><td class='ltr' style='padding:4px 0;direction:ltr;text-align:left;'>"
            "{}</td></tr>",
            rows,
        )
        return format_html(
            "<table style='border-collapse:collapse;'>{}</table>",
            body,
        )

    @admin.display(description="مرور")
    def browser_links(self, obj: Transcript):
        if not obj or not obj.pk:
            return "—"
        tid = str(obj.pk)
        links = (
            (
                fa_labels.BTN_VIEW_SEGMENTS,
                reverse("admin:turing_transcriptsegment_changelist")
                + f"?transcript__id__exact={tid}",
            ),
            (
                fa_labels.BTN_VIEW_WORDS,
                reverse("admin:turing_transcriptword_changelist")
                + f"?segment__transcript__id__exact={tid}",
            ),
            (
                fa_labels.BTN_VIEW_ANALYSIS,
                reverse("admin:turing_transcriptanalysis_changelist")
                + f"?transcript__id__exact={tid}",
            ),
            (
                fa_labels.BTN_VIEW_INTELLIGENCE,
                reverse("admin:turing_transcriptanalysis_changelist")
                + f"?transcript__id__exact={tid}",
            ),
        )
        buttons = format_html_join(
            " ",
            '<a class="button" href="{}" style="margin-left:6px;">{}</a>',
            ((url, label) for label, url in links),
        )
        return mark_safe(buttons)

    def save_formset(self, request, form, formset, change):
        instances = formset.save(commit=False)
        service = TranscriptService()
        transcript = form.instance

        if formset.model is Speaker:
            try:
                admin_assert_capability(
                    request.user,
                    organization=transcript.organization,
                    capability="edit_transcript",
                )
            except PermissionDeniedError as exc:
                self.message_user(request, str(exc), messages.ERROR)
                return
            for instance in instances:
                if instance.pk:
                    original = Speaker.objects.get(pk=instance.pk)
                    instance.speaker_label = original.speaker_label
                    instance.confidence = original.confidence
                    instance.save()
                    if original.speaker_name != instance.speaker_name:
                        service.rename_speaker(
                            speaker=instance,
                            speaker_name=instance.speaker_name,
                            edited_by=request.user,
                        )
                else:
                    instance.save()
            for obj in formset.deleted_objects:
                obj.delete()
            formset.save_m2m()
            return

        if formset.model is TranscriptSegment:
            self.message_user(
                request,
                "ویرایش بخش‌ها از صفحه متن غیرفعال است. از فهرست بخش‌های متن استفاده کنید.",
                messages.ERROR,
            )
            return

        super().save_formset(request, form, formset, change)

    @admin.action(description="ارسال برای بازبینی (به خودم)")
    def submit_for_self_review(self, request, queryset):
        service = TranscriptService()
        count = 0
        for transcript in queryset:
            try:
                admin_assert_capability(
                    request.user,
                    organization=transcript.organization,
                    capability="edit_transcript",
                )
                service.submit_for_review(
                    transcript=transcript,
                    assignee=request.user,
                    assigned_by=request.user,
                )
                count += 1
            except (PermissionDeniedError, ValidationError) as exc:
                self.message_user(request, f"{transcript.id}: {exc}", messages.ERROR)
        if count:
            self.message_user(
                request,
                f"{count} متن برای بازبینی ارسال شد.",
                messages.SUCCESS,
            )

    @admin.action(description="تأیید متن‌های انتخاب‌شده")
    def mark_approved(self, request, queryset):
        service = TranscriptService()
        count = 0
        for transcript in queryset:
            try:
                admin_assert_capability(
                    request.user,
                    organization=transcript.organization,
                    capability="approve_transcript",
                )
                service.approve(transcript=transcript, approved_by=request.user)
                count += 1
            except (PermissionDeniedError, ValidationError) as exc:
                self.message_user(request, f"{transcript.id}: {exc}", messages.ERROR)
        if count:
            self.message_user(request, f"{count} متن تأیید شد.", messages.SUCCESS)

    @admin.action(description="بازگشت به پیش‌نویس")
    def return_to_draft(self, request, queryset):
        service = TranscriptService()
        count = 0
        for transcript in queryset:
            try:
                admin_assert_capability(
                    request.user,
                    organization=transcript.organization,
                    capability="edit_transcript",
                )
                service.return_to_draft(transcript=transcript)
                count += 1
            except (PermissionDeniedError, ValidationError) as exc:
                self.message_user(request, f"{transcript.id}: {exc}", messages.ERROR)
        if count:
            self.message_user(request, f"{count} متن به پیش‌نویس بازگشت.", messages.SUCCESS)


@admin.register(Speaker)
class SpeakerAdmin(PersianAdminMixin, CapabilityGatedAdminMixin, admin.ModelAdmin):
    turing_view_capability = "view_transcript"
    turing_change_capability = "edit_transcript"
    turing_add_capability = "edit_transcript"
    turing_delete_capability = "edit_transcript"

    list_display = (
        "speaker_label",
        "speaker_name",
        "confidence_display",
        "transcript",
        "external_speaker_id",
        "updated_at",
    )
    list_filter = (
        ("transcript", admin.RelatedOnlyFieldListFilter),
        ("transcript__organization", admin.RelatedOnlyFieldListFilter),
        ("transcript__media", admin.RelatedOnlyFieldListFilter),
    )
    search_fields = (
        "speaker_label",
        "speaker_name",
        "external_speaker_id",
        "transcript__id",
        "transcript__media__original_filename",
    )
    ordering = ("speaker_label",)
    list_select_related = ("transcript", "transcript__media", "transcript__organization")
    list_per_page = 100
    raw_id_fields = ("transcript",)
    readonly_fields = (
        "speaker_label",
        "confidence_display",
        "confidence",
        "created_at",
        "updated_at",
    )
    fields = (
        "transcript",
        "speaker_label",
        "speaker_name",
        "confidence_display",
        "external_speaker_id",
        "created_at",
        "updated_at",
    )

    def turing_organization(self, obj):
        return obj.transcript.organization if obj and obj.transcript_id else None

    @admin.display(description=fa_labels.FIELD_LABELS["confidence"])
    def confidence_display(self, obj: Speaker) -> str:
        return format_confidence_pct(obj.confidence if obj else None)

    def get_queryset(self, request):
        return admin_scope_queryset(
            super()
            .get_queryset(request)
            .select_related("transcript", "transcript__media", "transcript__organization"),
            request.user,
            field="transcript__organization_id",
        )

    def save_model(self, request, obj, form, change):
        if not change:
            try:
                admin_assert_capability(
                    request.user,
                    organization=obj.transcript.organization,
                    capability="edit_transcript",
                )
            except PermissionDeniedError as exc:
                self.message_user(request, str(exc), messages.ERROR)
                return
            admin.ModelAdmin.save_model(self, request, obj, form, change)
            return

        try:
            admin_assert_capability(
                request.user,
                organization=obj.transcript.organization,
                capability="edit_transcript",
            )
        except PermissionDeniedError as exc:
            self.message_user(request, str(exc), messages.ERROR)
            return

        original = Speaker.objects.get(pk=obj.pk)
        obj.speaker_label = original.speaker_label
        obj.confidence = original.confidence
        name_changed = original.speaker_name != obj.speaker_name
        admin.ModelAdmin.save_model(self, request, obj, form, change)
        if name_changed:
            TranscriptService().rename_speaker(
                speaker=obj,
                speaker_name=obj.speaker_name,
                edited_by=request.user,
            )


@admin.register(TranscriptSegment)
class TranscriptSegmentAdmin(PersianAdminMixin, CapabilityGatedAdminMixin, admin.ModelAdmin):
    turing_view_capability = "view_transcript"
    turing_change_capability = "edit_transcript"
    turing_add_capability = "edit_transcript"
    turing_delete_capability = "edit_transcript"

    list_display = (
        "transcript",
        "speaker",
        "start_display",
        "end_display",
        "duration_display",
        "text_short",
    )
    list_filter = (
        ("transcript", admin.RelatedOnlyFieldListFilter),
        ("transcript__media", admin.RelatedOnlyFieldListFilter),
        ("transcript__organization", admin.RelatedOnlyFieldListFilter),
        ("speaker", admin.RelatedOnlyFieldListFilter),
        "created_at",
    )
    search_fields = (
        "transcript__id",
        "transcript__media__original_filename",
        "speaker__speaker_label",
        "speaker__speaker_name",
        "text",
    )
    ordering = ("start_ms",)
    date_hierarchy = "created_at"
    list_per_page = 100
    show_full_result_count = False
    raw_id_fields = ("transcript", "speaker")
    readonly_fields = (
        "start_display",
        "end_display",
        "duration_display",
        "confidence",
        "words",
        "provider_payload",
        "is_edited",
        "created_at",
        "updated_at",
    )
    list_select_related = (
        "transcript",
        "transcript__media",
        "transcript__organization",
        "speaker",
    )

    def turing_organization(self, obj):
        return obj.transcript.organization if obj and obj.transcript_id else None

    @admin.display(description="شروع", ordering="start_ms")
    def start_display(self, obj: TranscriptSegment) -> str:
        return format_timestamp_ms(obj.start_ms)

    @admin.display(description="پایان", ordering="end_ms")
    def end_display(self, obj: TranscriptSegment) -> str:
        return format_timestamp_ms(obj.end_ms)

    @admin.display(description="مدت زمان")
    def duration_display(self, obj: TranscriptSegment) -> str:
        return format_timestamp_ms(max(0, int(obj.end_ms or 0) - int(obj.start_ms or 0)))

    @admin.display(description="متن")
    def text_short(self, obj: TranscriptSegment) -> str:
        text = obj.text or ""
        return text if len(text) <= 80 else f"{text[:80]}…"

    def get_queryset(self, request):
        return admin_scope_queryset(
            super()
            .get_queryset(request)
            .select_related(
                "transcript",
                "transcript__media",
                "transcript__organization",
                "speaker",
            ),
            request.user,
            field="transcript__organization_id",
        )

    def save_model(self, request, obj, form, change):
        if change:
            try:
                admin_assert_capability(
                    request.user,
                    organization=obj.transcript.organization,
                    capability="edit_transcript",
                )
            except PermissionDeniedError as exc:
                self.message_user(request, str(exc), messages.ERROR)
                return
            TranscriptService().update_segment(
                segment=TranscriptSegment.objects.get(pk=obj.pk),
                text=obj.text,
                speaker=obj.speaker,
                start_ms=obj.start_ms,
                end_ms=obj.end_ms,
                edited_by=request.user,
            )
        else:
            try:
                admin_assert_capability(
                    request.user,
                    organization=obj.transcript.organization,
                    capability="edit_transcript",
                )
            except PermissionDeniedError as exc:
                self.message_user(request, str(exc), messages.ERROR)
                return
            admin.ModelAdmin.save_model(self, request, obj, form, change)


@admin.register(TranscriptWord)
class TranscriptWordAdmin(PersianAdminMixin, CapabilityGatedAdminMixin, admin.ModelAdmin):
    turing_view_capability = "view_transcript"
    turing_change_capability = "edit_transcript"
    turing_delete_capability = "edit_transcript"

    list_display = (
        "transcript_display",
        "speaker_display",
        "text",
        "start_display",
        "end_display",
        "confidence_display",
    )
    list_filter = (
        ("segment__transcript", admin.RelatedOnlyFieldListFilter),
        ("segment__transcript__media", admin.RelatedOnlyFieldListFilter),
        ("segment__transcript__organization", admin.RelatedOnlyFieldListFilter),
        ("segment__speaker", admin.RelatedOnlyFieldListFilter),
        "created_at",
    )
    search_fields = (
        "segment__transcript__id",
        "segment__transcript__media__original_filename",
        "segment__speaker__speaker_label",
        "segment__speaker__speaker_name",
        "text",
    )
    ordering = ("start_ms",)
    date_hierarchy = "created_at"
    list_per_page = 200
    show_full_result_count = False
    raw_id_fields = ("segment",)
    readonly_fields = (
        "start_display",
        "end_display",
        "confidence_display",
        "created_at",
        "updated_at",
    )
    list_select_related = (
        "segment",
        "segment__transcript",
        "segment__transcript__media",
        "segment__transcript__organization",
        "segment__speaker",
    )

    def turing_organization(self, obj):
        if not obj or not obj.segment_id:
            return None
        return obj.segment.transcript.organization

    @admin.display(description="متن", ordering="segment__transcript")
    def transcript_display(self, obj: TranscriptWord):
        return obj.segment.transcript if obj.segment_id else "—"

    @admin.display(description="گوینده", ordering="segment__speaker")
    def speaker_display(self, obj: TranscriptWord):
        if not obj.segment_id:
            return "—"
        return obj.segment.speaker or "—"

    @admin.display(description="شروع", ordering="start_ms")
    def start_display(self, obj: TranscriptWord) -> str:
        return format_timestamp_ms(obj.start_ms)

    @admin.display(description="پایان", ordering="end_ms")
    def end_display(self, obj: TranscriptWord) -> str:
        return format_timestamp_ms(obj.end_ms)

    @admin.display(description=fa_labels.FIELD_LABELS["confidence"])
    def confidence_display(self, obj: TranscriptWord) -> str:
        return format_confidence_pct(obj.confidence)

    def get_queryset(self, request):
        return admin_scope_queryset(
            super()
            .get_queryset(request)
            .select_related(
                "segment",
                "segment__transcript",
                "segment__transcript__media",
                "segment__transcript__organization",
                "segment__speaker",
            ),
            request.user,
            field="segment__transcript__organization_id",
        )


@admin.register(TranscriptRevision)
class TranscriptRevisionAdmin(
    PersianAdminMixin,
    AppendOnlyBrowseAdminMixin,
    CapabilityGatedAdminMixin,
    admin.ModelAdmin,
):
    """Transcript versions — read-only history browser."""

    turing_view_capability = "view_transcript"
    turing_change_capability = "view_transcript"
    turing_delete_capability = "view_transcript"

    list_display = (
        "transcript",
        "revision_number",
        "source",
        "change_summary",
        "created_by",
        "created_at",
    )
    list_filter = (
        "source",
        "created_at",
        ("transcript__organization", admin.RelatedOnlyFieldListFilter),
    )
    search_fields = ("change_summary", "transcript__id", "transcript__media__original_filename")
    list_select_related = ("transcript", "transcript__media", "transcript__organization", "created_by")
    list_per_page = 50
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    readonly_fields = (
        "transcript",
        "revision_number",
        "source",
        "change_summary",
        "snapshot",
        "diff",
        "created_by",
        "created_at",
        "updated_at",
    )

    def turing_organization(self, obj):
        return obj.transcript.organization if obj and obj.transcript_id else None

    def get_queryset(self, request):
        return admin_scope_queryset(
            super()
            .get_queryset(request)
            .select_related(
                "transcript",
                "transcript__media",
                "transcript__organization",
                "created_by",
            ),
            request.user,
            field="transcript__organization_id",
        )


@admin.register(ReviewAssignment)
class ReviewAssignmentAdmin(PersianAdminMixin, CapabilityGatedAdminMixin, admin.ModelAdmin):
    turing_view_capability = "view_transcript"
    turing_change_capability = "review_transcript"
    turing_add_capability = "edit_transcript"
    turing_delete_capability = "review_transcript"

    list_display = ("transcript", "assignee", "status", "due_at", "created_at")
    list_filter = ("status", "created_at")
    list_select_related = ("transcript", "assignee")
    raw_id_fields = ("transcript", "assignee", "assigned_by")
    search_fields = ("transcript__id", "assignee__username")

    def turing_organization(self, obj):
        return obj.transcript.organization if obj and obj.transcript_id else None

    def get_queryset(self, request):
        return admin_scope_queryset(
            super().get_queryset(request).select_related("transcript", "assignee"),
            request.user,
            field="transcript__organization_id",
        )
