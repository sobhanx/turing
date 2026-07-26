from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _enable_turing_apps(settings):
    # Ensure settings cache is clear between tests
    from turing.conf import clear_settings_cache

    clear_settings_cache()
    yield
    clear_settings_cache()
