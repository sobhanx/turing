"""Admin UI visibility — hide clutter without removing models/data."""

from __future__ import annotations

import pytest
from django.contrib import admin
from django.contrib.admin.sites import AdminSite

from turing.admin.visibility import HIDDEN_FROM_ADMIN, apply_admin_visibility
from turing.models import (
    ConnectorCredential,
    ConnectorInstallation,
    ConnectorSyncJob,
    ExternalReference,
    MediaAsset,
    Meeting,
    Organization,
    ProcessingJob,
    ProcessingLog,
    Recording,
    ReviewAssignment,
    Speaker,
    SpeechProviderConfig,
    Transcript,
    TranscriptAnalysis,
    TranscriptExportSettings,
    TranscriptRevision,
    TranscriptSegment,
    TranscriptWord,
    TuringMembership,
    WebhookDelivery,
    WebhookSubscription,
)

# Import admin package so default site registrations + visibility policy apply.
import turing.admin  # noqa: F401


KEEP_VISIBLE = (
    Organization,
    MediaAsset,
    ProcessingJob,
    ProcessingLog,
    Recording,
    Transcript,
    TranscriptRevision,
    TranscriptSegment,
    TranscriptWord,
    Speaker,
    TranscriptAnalysis,
    TranscriptExportSettings,
    SpeechProviderConfig,
    ConnectorCredential,
    ConnectorInstallation,
)


@pytest.mark.django_db
def test_hidden_models_are_unregistered_from_default_admin():
    for model in HIDDEN_FROM_ADMIN:
        assert not admin.site.is_registered(model), model.__name__


@pytest.mark.django_db
def test_kept_models_remain_registered():
    for model in KEEP_VISIBLE:
        assert admin.site.is_registered(model), model.__name__


@pytest.mark.django_db
def test_explicit_hide_targets_match_policy():
    expected = {
        Meeting,
        ExternalReference,
        ConnectorSyncJob,
        WebhookDelivery,
        WebhookSubscription,
        ReviewAssignment,
        TuringMembership,
    }
    assert set(HIDDEN_FROM_ADMIN) == expected


@pytest.mark.django_db
def test_apply_admin_visibility_is_idempotent():
    apply_admin_visibility()
    apply_admin_visibility()
    assert not admin.site.is_registered(Meeting)
    assert admin.site.is_registered(Recording)


@pytest.mark.django_db
def test_hidden_admin_classes_still_usable_on_private_site():
    """Unregistering from default site must not delete Admin class definitions."""
    from turing.admin.meeting import MeetingAdmin
    from turing.admin.membership import TuringMembershipAdmin
    from turing.admin.transcript import ReviewAssignmentAdmin

    site = AdminSite(name="private-visibility-test")
    site.register(Meeting, MeetingAdmin)
    site.register(TuringMembership, TuringMembershipAdmin)
    site.register(ReviewAssignment, ReviewAssignmentAdmin)
    apply_admin_visibility(site)
    assert not site.is_registered(Meeting)
    assert not site.is_registered(TuringMembership)


@pytest.mark.django_db
def test_transcript_analysis_admin_is_read_only_for_staff():
    from django.test import RequestFactory

    from turing.admin.analysis import TranscriptAnalysisAdmin

    ma = TranscriptAnalysisAdmin(TranscriptAnalysis, admin.site)
    request = RequestFactory().get("/")
    request.user = type(
        "U",
        (),
        {"is_active": True, "is_staff": True, "is_superuser": False},
    )()
    assert ma.has_add_permission(request) is False
    assert ma.has_change_permission(request) is False
    assert ma.has_delete_permission(request) is False


@pytest.mark.django_db
def test_processing_log_and_revision_admins_are_read_only_for_staff():
    from django.test import RequestFactory

    from turing.admin.job import ProcessingLogAdmin
    from turing.admin.transcript import TranscriptRevisionAdmin

    request = RequestFactory().get("/")
    request.user = type(
        "U",
        (),
        {"is_active": True, "is_staff": True, "is_superuser": False},
    )()
    log_admin = ProcessingLogAdmin(ProcessingLog, admin.site)
    assert log_admin.has_add_permission(request) is False
    assert log_admin.has_change_permission(request) is False
    assert log_admin.has_delete_permission(request) is False

    rev_admin = TranscriptRevisionAdmin(TranscriptRevision, admin.site)
    assert rev_admin.has_add_permission(request) is False
    assert rev_admin.has_change_permission(request) is False
    assert rev_admin.has_delete_permission(request) is False
