from __future__ import annotations

import django_filters

from turing.models import MediaAsset, ProcessingJob, Transcript


class MediaAssetFilter(django_filters.FilterSet):
    class Meta:
        model = MediaAsset
        fields = {
            "use_case": ["exact"],
            "source_type": ["exact"],
            "tenant_key": ["exact"],
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
            "created_at": ["gte", "lte"],
        }


class TranscriptFilter(django_filters.FilterSet):
    class Meta:
        model = Transcript
        fields = {
            "status": ["exact", "in"],
            "media": ["exact"],
            "language_code": ["exact"],
            "is_primary": ["exact"],
        }
