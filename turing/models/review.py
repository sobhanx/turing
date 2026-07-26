from __future__ import annotations

from django.conf import settings
from django.db import models

from turing.domain.enums import ReviewDecisionType, ReviewStatus
from turing.models.media import UUIDModel


class ReviewAssignment(UUIDModel):
    """Assign a reviewer/editor to a transcript for the human workflow."""

    transcript = models.ForeignKey(
        "turing.Transcript",
        on_delete=models.CASCADE,
        related_name="review_assignments",
    )
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="turing_review_assignments",
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="turing_reviews_assigned",
    )
    status = models.CharField(
        max_length=32,
        choices=ReviewStatus.choices,
        default=ReviewStatus.PENDING,
        db_index=True,
    )
    due_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["assignee", "status"]),
            models.Index(fields=["transcript", "status"]),
        ]
        verbose_name = "Review assignment"
        verbose_name_plural = "Review assignments"

    def __str__(self) -> str:
        return f"Review({self.transcript_id} → {self.assignee_id} {self.status})"


class ReviewDecision(UUIDModel):
    """Immutable decision recorded against a review assignment."""

    assignment = models.ForeignKey(
        ReviewAssignment,
        on_delete=models.CASCADE,
        related_name="decisions",
    )
    decision = models.CharField(max_length=32, choices=ReviewDecisionType.choices)
    comment = models.TextField(blank=True, default="")
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="turing_review_decisions",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Review decision"
        verbose_name_plural = "Review decisions"

    def __str__(self) -> str:
        return f"Decision({self.decision})"
