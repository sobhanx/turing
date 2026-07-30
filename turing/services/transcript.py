from __future__ import annotations

import logging
import re

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

logger = logging.getLogger(__name__)

_EDITOR_BLOCK_SPLIT = re.compile(r"\n\s*\n")


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
                speaker_label=sp.label,
                speaker_name=(sp.display_name or "").strip(),
                external_speaker_id=sp.external_speaker_id,
                confidence=sp.confidence,
            )

        for seg in normalized.segments:
            if seg.speaker_label and seg.speaker_label not in speakers_by_label:
                speakers_by_label[seg.speaker_label] = Speaker.objects.create(
                    transcript=transcript,
                    speaker_label=seg.speaker_label,
                    speaker_name="",
                )

        created_segments: list[TranscriptSegment] = []
        for seg in normalized.segments:
            speaker = speakers_by_label.get(seg.speaker_label or "")
            word_payloads = words_to_json_list(seg.words)
            if speaker is not None:
                word_payloads = self._stamp_speaker_identity(word_payloads, speaker)
            segment = TranscriptSegment.objects.create(
                transcript=transcript,
                speaker=speaker,
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
        self._reindex_after_speaker_rename(transcript)
        return segment

    @transaction.atomic
    def rename_speaker(
        self,
        *,
        speaker: Speaker,
        speaker_name: str | None = None,
        display_name: str | None = None,
        edited_by: AbstractBaseUser | None = None,
    ) -> Speaker:
        """
        Set editable ``speaker_name`` while preserving immutable ``speaker_label``.

        Propagates the name into segment word JSON, TranscriptWord metadata, and
        the semantic search index.
        """
        transcript = speaker.transcript
        assert_transcript_editable(transcript.status)
        if edited_by is not None:
            from turing.auth.tenancy import assert_organization_access

            assert_organization_access(
                edited_by,
                transcript.organization,
                capability="edit_transcript",
            )
        # Prefer speaker_name; display_name kept as API/admin alias.
        new_name = speaker_name if speaker_name is not None else display_name
        if new_name is None:
            new_name = ""
        new_name = str(new_name).strip()
        speaker.speaker_name = new_name
        speaker.save(update_fields=["speaker_name", "updated_at"])
        self._propagate_speaker_name(speaker)
        self.recompute_full_text(transcript)
        transcript.version += 1
        transcript.save(update_fields=["full_text", "version", "updated_at"])
        self._create_revision(
            transcript,
            source=RevisionSource.HUMAN,
            change_summary=(
                f"Renamed speaker {speaker.speaker_label} → {speaker.resolved_name}"
            ),
            created_by=edited_by,
            diff={
                "speaker_id": str(speaker.id),
                "speaker_label": speaker.speaker_label,
                "speaker_name": speaker.speaker_name,
            },
        )
        self._reindex_after_speaker_rename(transcript)
        return speaker

    def _stamp_speaker_identity(
        self, words: list[dict], speaker: Speaker
    ) -> list[dict]:
        """Ensure word payloads carry speaker_label + current speaker_name."""
        stamped: list[dict] = []
        for word in words:
            if not isinstance(word, dict):
                continue
            row = dict(word)
            row["speaker_label"] = speaker.speaker_label
            if speaker.speaker_name:
                row["speaker_name"] = speaker.speaker_name
            else:
                row.pop("speaker_name", None)
            stamped.append(row)
        return stamped

    def _propagate_speaker_name(self, speaker: Speaker) -> None:
        """Update denormalized speaker_name on segments/words; keep speaker_label."""
        segments = list(TranscriptSegment.objects.filter(speaker=speaker))
        for segment in segments:
            words = segment.words if isinstance(segment.words, list) else []
            dict_words = [w for w in words if isinstance(w, dict)]
            if not dict_words and not words:
                continue
            segment.words = self._stamp_speaker_identity(dict_words, speaker)
            segment.save(update_fields=["words", "updated_at"])

        for tw in TranscriptWord.objects.filter(segment__speaker=speaker).iterator():
            meta = dict(tw.metadata or {})
            meta["speaker_label"] = speaker.speaker_label
            if speaker.speaker_name:
                meta["speaker_name"] = speaker.speaker_name
            else:
                meta.pop("speaker_name", None)
            tw.metadata = meta
            tw.save(update_fields=["metadata", "updated_at"])

    def _reindex_after_speaker_rename(self, transcript: Transcript) -> None:
        try:
            from turing.services.search_index import SearchIndexService

            SearchIndexService().index_transcript(transcript)
        except Exception:  # noqa: BLE001
            logger.exception(
                "Search reindex after speaker rename failed transcript_id=%s",
                transcript.id,
            )

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

    @staticmethod
    def _format_editor_timestamp(start_ms: int | None) -> str:
        if start_ms is None:
            return "00:00"
        total_seconds = max(0, int(start_ms)) // 1000
        minutes, seconds = divmod(total_seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    def format_editor_body(self, transcript: Transcript) -> str:
        """Serialize transcript segments for Speech Center full-text editing."""
        segments = list(
            transcript.segments.select_related("speaker").order_by("sequence")
        )
        if not segments:
            return transcript.full_text or ""

        blocks: list[str] = []
        for seg in segments:
            timestamp = self._format_editor_timestamp(seg.start_ms)
            speaker = seg.speaker
            if speaker is not None:
                label = speaker.speaker_label
                display = speaker.resolved_name
                if display and display != label:
                    header = f"[{timestamp}] {display} ({label})"
                else:
                    header = f"[{timestamp}] {label}"
            else:
                header = f"[{timestamp}]"
            text = seg.text or ""
            blocks.append(f"{header}\n{text}" if text else header)
        return "\n\n".join(blocks)

    @staticmethod
    def _parse_editor_blocks(body: str) -> list[str]:
        """Parse editor body into segment text blocks (headers are not applied)."""
        normalized = (body or "").strip("\n")
        if not normalized.strip():
            return []

        texts: list[str] = []
        for raw_block in _EDITOR_BLOCK_SPLIT.split(normalized):
            block = raw_block.strip("\n")
            if not block:
                continue
            lines = block.split("\n")
            header = lines[0].strip()
            if not header.startswith("["):
                raise ValidationError(
                    "Each segment must start with a timestamp line like [00:01]."
                )
            text = "\n".join(lines[1:]).strip("\n") if len(lines) > 1 else ""
            texts.append(text)
        return texts

    @transaction.atomic
    def update_editor_body(
        self,
        transcript: Transcript,
        body: str,
        *,
        edited_by: AbstractBaseUser | None = None,
    ) -> Transcript:
        """Apply a Speech Center full-text edit (one revision, bulk segment update)."""
        assert_transcript_editable(transcript.status)
        if edited_by is not None:
            from turing.auth.tenancy import assert_organization_access

            assert_organization_access(
                edited_by,
                transcript.organization,
                capability="edit_transcript",
            )

        segments = list(
            transcript.segments.select_related("speaker").order_by("sequence")
        )
        if not segments:
            transcript.full_text = body
            transcript.word_count = len((body or "").split())
            transcript.version += 1
            transcript.save(
                update_fields=["full_text", "word_count", "version", "updated_at"]
            )
            self._create_revision(
                transcript,
                source=RevisionSource.HUMAN,
                change_summary="Transcript edited",
                created_by=edited_by,
                diff={"full_text": body},
            )
            self._reindex_after_speaker_rename(transcript)
            return transcript

        parsed_texts = self._parse_editor_blocks(body)
        if len(parsed_texts) != len(segments):
            raise ValidationError(
                f"Expected {len(segments)} segment blocks separated by blank lines, "
                f"got {len(parsed_texts)}."
            )

        changes: list[dict[str, str]] = []
        for segment, new_text in zip(segments, parsed_texts, strict=True):
            if segment.text != new_text:
                changes.append(
                    {"segment_id": str(segment.id), "text": new_text}
                )
            segment.text = new_text
            segment.is_edited = True
            segment.save(update_fields=["text", "is_edited", "updated_at"])

        self.recompute_full_text(transcript)
        transcript.word_count = count_words_in_segments(transcript.segments.all())
        transcript.version += 1
        transcript.save(
            update_fields=["full_text", "word_count", "version", "updated_at"]
        )
        self._create_revision(
            transcript,
            source=RevisionSource.HUMAN,
            change_summary="Transcript edited",
            created_by=edited_by,
            diff={"segments": changes} if changes else {"segments": "unchanged"},
        )
        self._reindex_after_speaker_rename(transcript)
        return transcript

    def iter_speaker_turns(
        self,
        transcript: Transcript,
        *,
        merge_consecutive: bool = True,
    ):
        """
        Yield ``(speaker_name, text)`` turns in segment order.

        Used by export renderers so PDF/DOCX/TXT share one dialogue model.
        Consecutive segments with the same speaker are merged by default.
        """
        current_name: str | None = None
        current_parts: list[str] = []
        has_turn = False

        def flush():
            nonlocal has_turn, current_parts, current_name
            if not has_turn:
                return
            text = " ".join(p for p in current_parts if p).strip()
            yield_name = current_name or ""
            current_parts = []
            has_turn = False
            return yield_name, text

        pending: list[tuple[str, str]] = []
        for seg in transcript.segments.select_related("speaker").order_by("sequence"):
            name = ""
            if seg.speaker_id:
                name = seg.speaker.resolved_name
            text = (seg.text or "").strip()
            if not text:
                continue
            if (
                merge_consecutive
                and has_turn
                and name == current_name
            ):
                current_parts.append(text)
                continue
            if has_turn:
                flushed = flush()
                if flushed is not None:
                    pending.append(flushed)
            current_name = name
            current_parts = [text]
            has_turn = True
        if has_turn:
            flushed = flush()
            if flushed is not None:
                pending.append(flushed)
        yield from pending

    def format_export_body(
        self,
        transcript: Transcript,
        *,
        merge_consecutive: bool = True,
    ) -> str:
        """
        Export-friendly dialogue body:

            Speaker Name

            Text...

            Next Speaker

            Text...
        """
        blocks: list[str] = []
        for speaker_name, text in self.iter_speaker_turns(
            transcript, merge_consecutive=merge_consecutive
        ):
            if not text:
                continue
            if speaker_name:
                blocks.append(f"{speaker_name}\n\n{text}")
            else:
                blocks.append(text)
        return "\n\n".join(blocks)
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
                    "speaker_label": s.speaker_label,
                    "speaker_name": s.speaker_name,
                    "resolved_name": s.resolved_name,
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
