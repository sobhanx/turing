"""
Default settings module: local development.

``DJANGO_SETTINGS_MODULE=config.settings`` keeps the existing workflow.
For production, set ``DJANGO_SETTINGS_MODULE=config.settings.production``.
"""

from .local import *  # noqa: F401,F403
