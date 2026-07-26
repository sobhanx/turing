from __future__ import annotations

from django.contrib import admin, messages
from django.utils.html import format_html

from turing.domain.enums import TranscriptStatus
from turing.domain.exceptions import ValidationError
from turing.models import (
    ReviewAssignment,
    Speaker,
    Transcript,
    TranscriptRevision,
    TranscriptSegment,
    TranscriptWord,
)
from turing.services.transcript import TranscriptService


class SpeakerInline(admin.TabularInline):
    model = Speaker
    extra = 0
    fields = ("label", "display_name", "confidence", "external_speaker_id")


class TranscriptSegmentInline(admin.TabularInline):
    model = TranscriptSegment
    extra = 0
    fields = (
        "sequence",
        "speaker",
        "start_ms",
        "end_ms",
        "text",
        "confidence",
        "word_count_display",
        "is_edited",
    )
    readonly_fields = ("sequence", "confidence", "word_count_display", "is_edited")
    ordering = ("sequence",)
    show_change_link = True

    @admin.display(description="Words")
    def word_count_display(self, obj: TranscriptSegment):
        if not obj or not obj.pk:
            return "—"
        return obj.word_count


class TranscriptRevisionInline(admin.TabularInline):
    model = TranscriptRevision
    extra = 0
    readonly_fields = (
        "revision_number",
        "source",
        "change_summary",
        "created_by",
        "created_at",
    )
    can_delete = False
    ordering = ("-revision_number",)


@admin.register(Transcript)
class TranscriptAdmin(admin.ModelAdmin):
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
    readonly_fields = (
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
    autocomplete_fields = ("organization",)
    inlines = [SpeakerInline, TranscriptSegmentInline, TranscriptRevisionInline]
    actions = (
        "submit_for_self_review",
        "mark_approved",
        "return_to_draft",
    )

    @admin.display(description="Status")
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
            obj.get_status_display(),
        )

    @admin.display(description="Confidence")
    def confidence_display(self, obj: Transcript):
        if obj.confidence_avg is None:
            return "—"
        return f"{obj.confidence_avg:.2f}"

    def save_formset(self, request, form, formset, change):
        """Persist inline segment/speaker edits through TranscriptService for revisions."""
        instances = formset.save(commit=False)
        service = TranscriptService()

        if formset.model is TranscriptSegment:
            for instance in instances:
                if instance.pk:
                    service.update_segment(
                        segment=instance,
                        text=instance.text,
                        speaker=instance.speaker,
                        start_ms=instance.start_ms,
                        end_ms=instance.end_ms,
                        edited_by=request.user,
                        change_summary="Edited via Admin",
                    )
                else:
                    instance.save()
            for obj in formset.deleted_objects:
                obj.delete()
            formset.save_m2m()
            return

        if formset.model is Speaker:
            for instance in instances:
                if instance.pk:
                    original = Speaker.objects.get(pk=instance.pk)
                    instance.save()
                    if original.display_name != instance.display_name:
                        service.rename_speaker(
                            speaker=instance,
                            display_name=instance.display_name,
                            edited_by=request.user,
                        )
                else:
                    instance.save()
            for obj in formset.deleted_objects:
                obj.delete()
            formset.save_m2m()
            return

        super().save_formset(request, form, formset, change)

    @admin.action(description="Submit for review (assign to me)")
    def submit_for_self_review(self, request, queryset):
        service = TranscriptService()
        count = 0
        for transcript in queryset:
            try:
                service.submit_for_review(
                    transcript=transcript,
                    assignee=request.user,
                    assigned_by=request.user,
                )
                count += 1
            except ValidationError as exc:
                self.message_user(request, f"{transcript.id}: {exc}", messages.ERROR)
        if count:
            self.message_user(
                request,
                f"Submitted {count} transcript(s) for review.",
                messages.SUCCESS,
            )

    @admin.action(description="Approve selected transcripts")
    def mark_approved(self, request, queryset):
        service = TranscriptService()
        count = 0
        for transcript in queryset:
            try:
                service.approve(transcript=transcript, approved_by=request.user)
                count += 1
            except ValidationError as exc:
                self.message_user(request, f"{transcript.id}: {exc}", messages.ERROR)
        if count:
            self.message_user(request, f"Approved {count} transcript(s).", messages.SUCCESS)

    @admin.action(description="Return to draft")
    def return_to_draft(self, request, queryset):
        service = TranscriptService()
        count = 0
        for transcript in queryset:
            try:
                service.return_to_draft(transcript=transcript)
                count += 1
            except ValidationError as exc:
                self.message_user(request, f"{transcript.id}: {exc}", messages.ERROR)
        if count:
            self.message_user(request, f"Returned {count} transcript(s) to draft.", messages.SUCCESS)


@admin.register(TranscriptSegment)
class TranscriptSegmentAdmin(admin.ModelAdmin):
    list_display = (
        "transcript",
        "sequence",
        "speaker",
        "start_ms",
        "end_ms",
        "text_short",
        "confidence",
        "word_count",
        "is_edited",
    )
    list_filter = ("is_edited",)
    search_fields = ("text", "transcript__id")
    raw_id_fields = ("transcript", "speaker")
    readonly_fields = ("confidence", "words", "provider_payload", "is_edited")

    @admin.display(description="Text")
    def text_short(self, obj: TranscriptSegment):
        return obj.text[:80]

    @admin.display(description="Words")
    def word_count(self, obj: TranscriptSegment):
        return obj.word_count

    def save_model(self, request, obj, form, change):
        if change:
            TranscriptService().update_segment(
                segment=TranscriptSegment.objects.get(pk=obj.pk),
                text=obj.text,
                speaker=obj.speaker,
                start_ms=obj.start_ms,
                end_ms=obj.end_ms,
                edited_by=request.user,
            )
        else:
            super().save_model(request, obj, form, change)


@admin.register(TranscriptWord)
class TranscriptWordAdmin(admin.ModelAdmin):
    list_display = ("text", "segment", "sequence", "start_ms", "end_ms", "confidence")
    search_fields = ("text", "segment__transcript__id")
    raw_id_fields = ("segment",)
    list_filter = ("confidence",)


@admin.register(TranscriptRevision)
class TranscriptRevisionAdmin(admin.ModelAdmin):
    list_display = ("transcript", "revision_number", "source", "change_summary", "created_by", "created_at")
    list_filter = ("source", "created_at")
    search_fields = ("change_summary", "transcript__id")
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


@admin.register(ReviewAssignment)
class ReviewAssignmentAdmin(admin.ModelAdmin):
    list_display = ("transcript", "assignee", "status", "due_at", "created_at")
    list_filter = ("status", "created_at")
    raw_id_fields = ("transcript", "assignee", "assigned_by")
    search_fields = ("transcript__id", "assignee__username")
