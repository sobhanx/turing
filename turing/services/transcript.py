from __future__ import annotations

from django.contrib.auth.models import AbstractBaseUser
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from turing.domain.enums import RevisionSource, ReviewDecisionType, ReviewStatus, TranscriptStatus
from turing.domain.exceptions import NotFoundError, ValidationError
from turing.domain.policies import assert_transcript_editable
from turing.models import (
    ProcessingJob,
    ReviewAssignment,
    ReviewDecision,
    Speaker,
    Transcript,
    TranscriptRevision,
    TranscriptSegment,
)
from turing.providers.types import NormalizedTranscript


class TranscriptService:
    """Persist, edit, revise, and review transcripts."""

    @transaction.atomic
    def persist_from_provider(
        self,
        *,
        job: ProcessingJob,
        normalized: NormalizedTranscript,
        source: str = RevisionSource.PROVIDER,
        created_by: AbstractBaseUser | None = None,
    ) -> Transcript:
        # Idempotent: never create a second transcript for the same job
        existing = Transcript.objects.filter(job=job).first()
        if existing:
            return existing

        # Demote previous primary transcripts for this media
        Transcript.objects.filter(media=job.media, is_primary=True).update(is_primary=False)

        transcript = Transcript.objects.create(
            job=job,
            media=job.media,
            language_code=normalized.language_code or job.language_code,
            status=TranscriptStatus.DRAFT,
            full_text=normalized.full_text,
            version=1,
            is_primary=True,
            confidence_avg=normalized.confidence_avg,
            metadata=normalized.raw or {},
        )

        speakers_by_label: dict[str, Speaker] = {}
        for sp in normalized.speakers:
            speakers_by_label[sp.label] = Speaker.objects.create(
                transcript=transcript,
                label=sp.label,
                display_name=sp.display_name or sp.label,
                external_speaker_id=sp.external_speaker_id,
                confidence=sp.confidence,
            )

        # Ensure speakers referenced only on segments still exist
        for seg in normalized.segments:
            if seg.speaker_label and seg.speaker_label not in speakers_by_label:
                speakers_by_label[seg.speaker_label] = Speaker.objects.create(
                    transcript=transcript,
                    label=seg.speaker_label,
                    display_name=seg.speaker_label,
                )

        for seg in normalized.segments:
            TranscriptSegment.objects.create(
                transcript=transcript,
                speaker=speakers_by_label.get(seg.speaker_label or ""),
                sequence=seg.sequence,
                start_ms=seg.start_ms,
                end_ms=seg.end_ms,
                text=seg.text,
                confidence=seg.confidence,
                words=[
                    {
                        "text": w.text,
                        "start_ms": w.start_ms,
                        "end_ms": w.end_ms,
                        "confidence": w.confidence,
                        "speaker_label": w.speaker_label,
                    }
                    for w in seg.words
                ],
                provider_payload=seg.raw or {},
            )

        self._create_revision(
            transcript,
            source=source,
            change_summary="Initial provider transcript",
            created_by=created_by,
        )
        return transcript

    def get(self, transcript_id) -> Transcript:
        try:
            return Transcript.objects.prefetch_related("segments", "speakers").get(
                pk=transcript_id
            )
        except Transcript.DoesNotExist as exc:
            raise NotFoundError(f"Transcript '{transcript_id}' not found.") from exc

    @transaction.atomic
    def update_segment(
        self,
        *,
        segment: TranscriptSegment,
        text: str | None = None,
        speaker: Speaker | None = None,
        start_ms: int | None = None,
        end_ms: int | None = None,
        edited_by: AbstractBaseUser | None = None,
        change_summary: str = "Segment edited",
    ) -> TranscriptSegment:
        transcript = segment.transcript
        assert_transcript_editable(transcript.status)

        if text is not None:
            segment.text = text
            segment.is_edited = True
        if speaker is not None:
            if speaker.transcript_id != transcript.id:
                raise ValidationError("Speaker does not belong to this transcript.")
            segment.speaker = speaker
            segment.is_edited = True
        if start_ms is not None:
            segment.start_ms = start_ms
            segment.is_edited = True
        if end_ms is not None:
            segment.end_ms = end_ms
            segment.is_edited = True
        segment.save()

        self.recompute_full_text(transcript)
        transcript.version += 1
        transcript.save(update_fields=["full_text", "version", "updated_at"])
        self._create_revision(
            transcript,
            source=RevisionSource.HUMAN,
            change_summary=change_summary,
            created_by=edited_by,
            diff={"segment_id": str(segment.id), "text": segment.text},
        )
        return segment

    @transaction.atomic
    def rename_speaker(
        self,
        *,
        speaker: Speaker,
        display_name: str,
        edited_by: AbstractBaseUser | None = None,
    ) -> Speaker:
        transcript = speaker.transcript
        assert_transcript_editable(transcript.status)
        speaker.display_name = display_name
        speaker.save(update_fields=["display_name", "updated_at"])
        self.recompute_full_text(transcript)
        transcript.version += 1
        transcript.save(update_fields=["full_text", "version", "updated_at"])
        self._create_revision(
            transcript,
            source=RevisionSource.HUMAN,
            change_summary=f"Renamed speaker {speaker.label} → {display_name}",
            created_by=edited_by,
            diff={"speaker_id": str(speaker.id), "display_name": display_name},
        )
        return speaker

    def recompute_full_text(self, transcript: Transcript) -> str:
        lines: list[str] = []
        for seg in transcript.segments.select_related("speaker").order_by("sequence"):
            name = ""
            if seg.speaker_id:
                name = seg.speaker.resolved_name
            prefix = f"{name}: " if name else ""
            lines.append(f"{prefix}{seg.text}".strip())
        transcript.full_text = "\n".join(lines)
        return transcript.full_text

    @transaction.atomic
    def submit_for_review(
        self,
        *,
        transcript: Transcript,
        assignee: AbstractBaseUser,
        assigned_by: AbstractBaseUser | None = None,
        notes: str = "",
    ) -> ReviewAssignment:
        if transcript.status == TranscriptStatus.APPROVED:
            raise ValidationError("Approved transcripts cannot be submitted for review.")
        transcript.status = TranscriptStatus.IN_REVIEW
        transcript.save(update_fields=["status", "updated_at"])
        return ReviewAssignment.objects.create(
            transcript=transcript,
            assignee=assignee,
            assigned_by=assigned_by,
            status=ReviewStatus.PENDING,
            notes=notes,
        )

    @transaction.atomic
    def decide_review(
        self,
        *,
        assignment: ReviewAssignment,
        decision: str,
        decided_by: AbstractBaseUser | None = None,
        comment: str = "",
    ) -> ReviewDecision:
        if decision not in ReviewDecisionType.values:
            raise ValidationError(f"Invalid decision '{decision}'.")

        record = ReviewDecision.objects.create(
            assignment=assignment,
            decision=decision,
            comment=comment,
            decided_by=decided_by,
        )
        transcript = assignment.transcript

        if decision == ReviewDecisionType.APPROVE:
            assignment.status = ReviewStatus.APPROVED
            transcript.status = TranscriptStatus.APPROVED
            transcript.approved_at = timezone.now()
            transcript.approved_by = decided_by
            transcript.save(
                update_fields=["status", "approved_at", "approved_by", "updated_at"]
            )
        elif decision == ReviewDecisionType.REQUEST_CHANGES:
            assignment.status = ReviewStatus.CHANGES_REQUESTED
            transcript.status = TranscriptStatus.DRAFT
            transcript.save(update_fields=["status", "updated_at"])
        else:
            assignment.status = ReviewStatus.REJECTED
            transcript.status = TranscriptStatus.ARCHIVED
            transcript.save(update_fields=["status", "updated_at"])

        assignment.save(update_fields=["status", "updated_at"])
        return record

    def _create_revision(
        self,
        transcript: Transcript,
        *,
        source: str,
        change_summary: str,
        created_by: AbstractBaseUser | None = None,
        diff: dict | None = None,
    ) -> TranscriptRevision:
        last = (
            TranscriptRevision.objects.filter(transcript=transcript).aggregate(
                m=Max("revision_number")
            )["m"]
            or 0
        )
        snapshot = {
            "full_text": transcript.full_text,
            "version": transcript.version,
            "language_code": transcript.language_code,
            "speakers": [
                {
                    "id": str(s.id),
                    "label": s.label,
                    "display_name": s.display_name,
                }
                for s in transcript.speakers.all()
            ],
            "segments": [
                {
                    "id": str(seg.id),
                    "sequence": seg.sequence,
                    "speaker_id": str(seg.speaker_id) if seg.speaker_id else None,
                    "start_ms": seg.start_ms,
                    "end_ms": seg.end_ms,
                    "text": seg.text,
                    "confidence": seg.confidence,
                }
                for seg in transcript.segments.order_by("sequence")
            ],
        }
        return TranscriptRevision.objects.create(
            transcript=transcript,
            revision_number=last + 1,
            source=source,
            change_summary=change_summary,
            snapshot=snapshot,
            diff=diff or {},
            created_by=created_by,
        )
