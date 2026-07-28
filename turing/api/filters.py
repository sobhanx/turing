from __future__ import annotations

import django_filters
from django.db.models import F, OuterRef, Q, Subquery

from turing.domain.enums import ConnectorInstallationStatus, ConnectorSyncJobStatus
from turing.models import (
    ConnectorInstallation,
    ConnectorSyncJob,
    MediaAsset,
    ProcessingJob,
    Transcript,
    TranscriptAnalysis,
)


class MediaAssetFilter(django_filters.FilterSet):
    external_system = django_filters.CharFilter(
        field_name="external_references__external_system",
        lookup_expr="exact",
    )
    external_type = django_filters.CharFilter(
        field_name="external_references__external_type",
        lookup_expr="exact",
    )
    external_id = django_filters.CharFilter(
        field_name="external_references__external_id",
        lookup_expr="exact",
    )

    class Meta:
        model = MediaAsset
        fields = {
            "use_case": ["exact"],
            "source_type": ["exact"],
            "tenant_key": ["exact"],
            "organization": ["exact"],
            "created_at": ["gte", "lte"],
        }


class ProcessingJobFilter(django_filters.FilterSet):
    class Meta:
        model = ProcessingJob
        fields = {
            "status": ["exact", "in"],
            "capability": ["exact"],
            "provider_code": ["exact"],
            "media": ["exact"],
            "tenant_key": ["exact"],
            "organization": ["exact"],
            "created_at": ["gte", "lte"],
        }


class TranscriptFilter(django_filters.FilterSet):
    q = django_filters.CharFilter(method="filter_search", label="Search")
    external_system = django_filters.CharFilter(
        field_name="external_references__external_system",
        lookup_expr="exact",
    )
    external_type = django_filters.CharFilter(
        field_name="external_references__external_type",
        lookup_expr="exact",
    )
    external_id = django_filters.CharFilter(
        field_name="external_references__external_id",
        lookup_expr="exact",
    )

    class Meta:
        model = Transcript
        fields = {
            "status": ["exact", "in"],
            "media": ["exact"],
            "language_code": ["exact"],
            "is_primary": ["exact"],
            "organization": ["exact"],
        }

    def filter_search(self, queryset, name, value):
        from turing.services.transcript import TranscriptService

        return TranscriptService().search(value, queryset=queryset)


class TranscriptAnalysisFilter(django_filters.FilterSet):
    class Meta:
        model = TranscriptAnalysis
        fields = {
            "analysis_type": ["exact", "in"],
            "transcript": ["exact"],
            "organization": ["exact"],
            "provider": ["exact"],
            "created_at": ["gte", "lte"],
        }


class ConnectorInstallationFilter(django_filters.FilterSet):
    """List filters for UX-ready installation browsing (Phase 4.4.1)."""

    health = django_filters.CharFilter(method="filter_health")

    class Meta:
        model = ConnectorInstallation
        fields = {
            "connector_type": ["exact"],
            "status": ["exact"],
            "created_at": ["gte", "lte", "date"],
        }

    def filter_health(self, queryset, name, value):
        """
        Filter by derived ``current_health`` labels.

        Status-driven labels use SQL status filters; healthy/degraded compare
        latest completed vs failed sync finished_at timestamps.
        """
        label = (value or "").strip().lower()
        if not label:
            return queryset

        if label == "pending":
            return queryset.filter(status=ConnectorInstallationStatus.PENDING)
        if label == "expired":
            return queryset.filter(status=ConnectorInstallationStatus.EXPIRED)
        if label == "revoked":
            return queryset.filter(status=ConnectorInstallationStatus.REVOKED)
        if label == "unhealthy":
            return queryset.filter(status=ConnectorInstallationStatus.ERROR)
        if label not in {"healthy", "degraded"}:
            return queryset.none()

        # Align with ConnectorInstallation.current_health() for active installs.
        qs = queryset.filter(status=ConnectorInstallationStatus.ACTIVE)
        last_ok = (
            ConnectorSyncJob.objects.filter(
                installation_id=OuterRef("pk"),
                status=ConnectorSyncJobStatus.COMPLETED,
            )
            .order_by("-finished_at", "-created_at")
            .values("finished_at")[:1]
        )
        last_fail = (
            ConnectorSyncJob.objects.filter(
                installation_id=OuterRef("pk"),
                status=ConnectorSyncJobStatus.FAILED,
            )
            .order_by("-finished_at", "-created_at")
            .values("finished_at")[:1]
        )
        qs = qs.annotate(
            _last_ok_at=Subquery(last_ok),
            _last_fail_at=Subquery(last_fail),
        )
        degraded_q = Q(_last_fail_at__isnull=False) & (
            Q(_last_ok_at__isnull=True) | Q(_last_fail_at__gte=F("_last_ok_at"))
        )
        if label == "degraded":
            return qs.filter(degraded_q)
        return qs.exclude(degraded_q)
