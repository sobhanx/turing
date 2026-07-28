from __future__ import annotations

from django.contrib.auth.models import AbstractBaseUser
from django.db import IntegrityError, connection, transaction
from django.db.models import Max, Q, QuerySet
from django.utils import timezone

from turing.domain.enums import RevisionSource, ReviewDecisionType, ReviewStatus, TranscriptStatus
from turing.domain.events import transcript_created
from turing.domain.exceptions import JobStateError, NotFoundError, ValidationError
from turing.domain.policies import (
    assert_can_approve,
    assert_can_submit_for_review,
    assert_transcript_editable,
)
from turing.domain.transcript_schema import count_words_in_segments, words_to_json_list
from turing.events.bus import emit_after_commit
from turing.events.payloads import snapshot_external_references
from turing.models import (
    ProcessingJob,
    ReviewAssignment,
    ReviewDecision,
    Speaker,
    Transcript,
    TranscriptRevision,
    TranscriptSegment,
    TranscriptWord,
)
from turing.providers.types import NormalizedTranscript


class TranscriptService:
    """Persist, edit, revise, search, and review transcripts."""

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

        try:
            org = job.organization or job.media.organization
            if org is None:
                raise ValidationError(
                    "Cannot persist transcript: job/media has no organization."
                )
            transcript = Transcript.objects.create(
                job=job,
                media=job.media,
                organization=org,
                language_code=normalized.language_code or job.language_code,
                status=TranscriptStatus.DRAFT,
                full_text=normalized.full_text,
                version=1,
                is_primary=True,
                confidence_avg=normalized.confidence_avg,
                metadata=normalized.raw or {},
                word_count=0,
            )
        except IntegrityError:
            existing = Transcript.objects.filter(job=job).first()
            if existing:
                return existing
            raise

        # Demote other primaries only after this create wins the race
        Transcript.objects.filter(media=job.media, is_primary=True).exclude(
            pk=transcript.pk
        ).update(is_primary=False)
        speakers_by_label: dict[str, Speaker] = {}
        for sp in normalized.speakers:
            speakers_by_label[sp.label] = Speaker.objects.create(
                transcript=transcript,
                label=sp.label,
                display_name=sp.display_name or sp.label,
                external_speaker_id=sp.external_speaker_id,
                confidence=sp.confidence,
            )

        for seg in normalized.segments:
            if seg.speaker_label and seg.speaker_label not in speakers_by_label:
                speakers_by_label[seg.speaker_label] = Speaker.objects.create(
                    transcript=transcript,
                    label=seg.speaker_label,
                    display_name=seg.speaker_label,
                )

        created_segments: list[TranscriptSegment] = []
        for seg in normalized.segments:
            word_payloads = words_to_json_list(seg.words)
            segment = TranscriptSegment.objects.create(
                transcript=transcript,
                speaker=speakers_by_label.get(seg.speaker_label or ""),
                sequence=seg.sequence,
                start_ms=seg.start_ms,
                end_ms=seg.end_ms,
                text=seg.text,
                confidence=seg.confidence,
                words=word_payloads,
                provider_payload=seg.raw or {},
            )
            self._persist_word_rows(segment, word_payloads)
            created_segments.append(segment)

        transcript.word_count = count_words_in_segments(created_segments)
        transcript.save(update_fields=["word_count", "updated_at"])

        self._create_revision(
            transcript,
            source=source,
            change_summary="Initial provider transcript",
            created_by=created_by,
        )
        emit_after_commit(
            transcript_created(
                transcript_id=str(transcript.id),
                organization_id=transcript.organization_id,
                media_id=str(transcript.media_id) if transcript.media_id else None,
                job_id=str(transcript.job_id) if transcript.job_id else None,
                external_references=snapshot_external_references(
                    organization_id=transcript.organization_id,
                    media_id=transcript.media_id,
                )
                + snapshot_external_references(
                    organization_id=transcript.organization_id,
                    transcript_id=transcript.id,
                ),
            )
        )
        return transcript

    def get(self, transcript_id) -> Transcript:
        try:
            return Transcript.objects.prefetch_related(
                "segments",
                "segments__word_entries",
                "speakers",
            ).get(pk=transcript_id)
        except Transcript.DoesNotExist as exc:
            raise NotFoundError(f"Transcript '{transcript_id}' not found.") from exc

    def search(self, query: str, *, queryset: QuerySet | None = None) -> QuerySet:
        """
        Search transcripts by full text / segment / word content.

        Uses PostgreSQL full-text search when available; otherwise icontains
        (SQLite-friendly for local development).
        """
        qs = queryset if queryset is not None else Transcript.objects.all()
        term = (query or "").strip()
        if not term:
            return qs

        if connection.vendor == "postgresql":
            try:
                from django.contrib.postgres.search import (  # type: ignore
                    SearchQuery,
                    SearchRank,
                    SearchVector,
                )

                vector = SearchVector("full_text", config="simple")
                search_query = SearchQuery(term, config="simple")
                return (
                    qs.annotate(search=vector, rank=SearchRank(vector, search_query))
                    .filter(search=search_query)
                    .order_by("-rank", "-updated_at")
                )
            except Exception:
                pass

        return (
            qs.filter(
                Q(full_text__icontains=term)
                | Q(segments__text__icontains=term)
                | Q(segments__word_entries__text__icontains=term)
            )
            .distinct()
            .order_by("-updated_at")
        )

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
        if edited_by is not None:
            from turing.auth.tenancy import assert_organization_access

            assert_organization_access(
                edited_by,
                transcript.organization,
                capability="edit_transcript",
            )

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
        transcript.word_count = count_words_in_segments(transcript.segments.all())
        transcript.version += 1
        transcript.save(update_fields=["full_text", "word_count", "version", "updated_at"])
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
        if edited_by is not None:
            from turing.auth.tenancy import assert_organization_access

            assert_organization_access(
                edited_by,
                transcript.organization,
                capability="edit_transcript",
            )
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
        try:
            assert_can_submit_for_review(transcript.status)
        except JobStateError as exc:
            raise ValidationError(str(exc)) from exc
        if assigned_by is not None:
            from turing.auth.tenancy import assert_organization_access

            assert_organization_access(
                assigned_by,
                transcript.organization,
                capability="edit_transcript",
            )
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
    def approve(
        self,
        *,
        transcript: Transcript,
        approved_by: AbstractBaseUser | None = None,
    ) -> Transcript:
        try:
            assert_can_approve(transcript.status)
        except JobStateError as exc:
            raise ValidationError(str(exc)) from exc
        if approved_by is not None:
            from turing.auth.tenancy import assert_organization_access

            assert_organization_access(
                approved_by,
                transcript.organization,
                capability="approve_transcript",
            )
        transcript.status = TranscriptStatus.APPROVED
        transcript.approved_at = timezone.now()
        transcript.approved_by = approved_by
        transcript.save(
            update_fields=["status", "approved_at", "approved_by", "updated_at"]
        )
        return transcript

    @transaction.atomic
    def return_to_draft(
        self,
        *,
        transcript: Transcript,
    ) -> Transcript:
        if transcript.status == TranscriptStatus.ARCHIVED:
            raise ValidationError("Archived transcripts cannot return to draft.")
        transcript.status = TranscriptStatus.DRAFT
        transcript.approved_at = None
        transcript.approved_by = None
        transcript.save(
            update_fields=["status", "approved_at", "approved_by", "updated_at"]
        )
        return transcript

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

        transcript = assignment.transcript
        if decided_by is not None:
            from turing.auth.tenancy import assert_organization_access

            capability = (
                "approve_transcript"
                if decision == ReviewDecisionType.APPROVE
                else "review_transcript"
            )
            assert_organization_access(
                decided_by,
                transcript.organization,
                capability=capability,
            )

        record = ReviewDecision.objects.create(
            assignment=assignment,
            decision=decision,
            comment=comment,
            decided_by=decided_by,
        )
        transcript = assignment.transcript

        if decision == ReviewDecisionType.APPROVE:
            assignment.status = ReviewStatus.APPROVED
            self.approve(transcript=transcript, approved_by=decided_by)
        elif decision == ReviewDecisionType.REQUEST_CHANGES:
            assignment.status = ReviewStatus.CHANGES_REQUESTED
            self.return_to_draft(transcript=transcript)
        else:
            assignment.status = ReviewStatus.REJECTED
            transcript.status = TranscriptStatus.ARCHIVED
            transcript.save(update_fields=["status", "updated_at"])

        assignment.save(update_fields=["status", "updated_at"])
        return record

    def _persist_word_rows(
        self,
        segment: TranscriptSegment,
        word_payloads: list[dict],
    ) -> None:
        TranscriptWord.objects.filter(segment=segment).delete()
        rows = []
        for index, word in enumerate(word_payloads):
            extras = {
                k: v
                for k, v in word.items()
                if k not in {"text", "start_ms", "end_ms", "confidence"}
            }
            rows.append(
                TranscriptWord(
                    segment=segment,
                    sequence=index,
                    text=str(word.get("text") or ""),
                    start_ms=int(word.get("start_ms") or 0),
                    end_ms=int(word.get("end_ms") or 0),
                    confidence=word.get("confidence"),
                    metadata=extras,
                )
            )
        if rows:
            TranscriptWord.objects.bulk_create(rows)

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
            "status": transcript.status,
            "confidence_avg": transcript.confidence_avg,
            "word_count": transcript.word_count,
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
                    "word_count": len(seg.words or []) or len((seg.text or "").split()),
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
