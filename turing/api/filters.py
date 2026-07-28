from __future__ import annotations

import django_filters

from turing.models import MediaAsset, ProcessingJob, Transcript, TranscriptAnalysis


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
