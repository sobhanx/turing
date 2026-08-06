"""Local / development settings (default for manage.py and pytest)."""

from __future__ import annotations

from .base import *  # noqa: F403
from .base import env_bool, env_list, env_secret_key, database_config

SECRET_KEY = env_secret_key(allow_insecure_default=True)
DEBUG = env_bool("DJANGO_DEBUG", default=True)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")

DATABASES = {"default": database_config()}

# Relaxed security for local HTTP development
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SECURE_SSL_REDIRECT = False
SECURE_HSTS_SECONDS = 0

# Microsoft Redis for Windows is stuck on 3.0.x and does not support RESP3 HELLO.
# redis-py 5+ defaults to protocol 3; force RESP2 for local Celery/broker use.
if env_bool("CELERY_REDIS_FORCE_RESP2", default=True):
    try:
        from redis.connection import Connection as _RedisConnection
        from redis.maint_notifications import MaintNotificationsConfig
    except ImportError:
        pass
    else:
        if not getattr(_RedisConnection, "_turing_resp2_patched", False):
            _orig_redis_conn_init = _RedisConnection.__init__
            _resp2_maint = MaintNotificationsConfig(enabled=False)

            def _redis_conn_init(self, *args, **kwargs):
                # Force (do not setdefault): ConnectionPool may already inject
                # an enabled MaintNotificationsConfig that breaks RESP2.
                kwargs["protocol"] = 2
                kwargs["maint_notifications_config"] = _resp2_maint
                return _orig_redis_conn_init(self, *args, **kwargs)

            _RedisConnection.__init__ = _redis_conn_init  # type: ignore[method-assign]
            _RedisConnection._turing_resp2_patched = True
