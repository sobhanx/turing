"""URL safety helpers (SSRF hardening for outbound webhooks)."""

from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlparse

from django.core.exceptions import ValidationError as DjangoValidationError

from turing.domain.exceptions import ValidationError

logger = logging.getLogger(__name__)

_BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "metadata.google.internal",
        "metadata",
    }
)


def assert_safe_public_http_url(
    url: str,
    *,
    purpose: str = "URL",
    resolve_dns: bool = True,
) -> str:
    """
    Validate that ``url`` is an http(s) URL that does not target private,
    loopback, link-local, or cloud-metadata addresses.

    Raises ``turing.domain.exceptions.ValidationError`` on rejection.

    When ``resolve_dns`` is True (delivery path), hostnames are resolved and
    any private A/AAAA answer is rejected. When False (subscription create),
    only literal IPs and blocked hostnames are checked so create is not
    coupled to transient DNS failures.
    """
    raw = (url or "").strip()
    if not raw:
        raise ValidationError(f"{purpose} is required.")

    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    if scheme not in {"http", "https"}:
        raise ValidationError(f"{purpose} must use http or https.")
    if parsed.username or parsed.password:
        raise ValidationError(f"{purpose} must not include credentials.")
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise ValidationError(f"{purpose} must include a hostname.")
    if host in _BLOCKED_HOSTNAMES or host.endswith(".localhost"):
        raise ValidationError(f"{purpose} host is not allowed.")

    # Literal IP in the URL
    try:
        if _is_blocked_ip(ipaddress.ip_address(host)):
            raise ValidationError(f"{purpose} resolves to a private or reserved address.")
        return raw
    except ValueError:
        pass

    if not resolve_dns:
        return raw

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        # Cannot prove a private target — allow the HTTP client to fail naturally.
        # Positive private-IP detections still block when DNS succeeds.
        logger.debug("SSRF DNS resolve failed for %s; allowing request", host)
        return raw

    if not infos:
        raise ValidationError(f"{purpose} hostname could not be resolved.")

    for info in infos:
        sockaddr = info[4]
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if _is_blocked_ip(ip):
            raise ValidationError(
                f"{purpose} resolves to a private or reserved address."
            )
    return raw


def django_validate_safe_webhook_url(url: str) -> str:
    """Django forms/serializer compatible wrapper (no DNS resolve at create)."""
    try:
        return assert_safe_public_http_url(
            url, purpose="Webhook URL", resolve_dns=False
        )
    except ValidationError as exc:
        raise DjangoValidationError(str(exc)) from exc


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
        or (getattr(ip, "is_site_local", False))
    )
