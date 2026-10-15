"""Standalone email credential probe, split from ``email.py`` for size.

The probe validates a (possibly unsaved) IMAP + SMTP credential set without
touching the channel's polling loop or send path. Everything here is
stateless: functions take the ``EmailConfig`` explicitly so the module never
imports the adapter at runtime (only under ``TYPE_CHECKING``). Unlike the
token-endpoint probes (:mod:`src.interfaces.channels.token_probe`), email is stdlib-only
(``imaplib`` / ``smtplib``), so ``sdk_available`` is always True and the
blocking transports are driven through :func:`asyncio.to_thread`.

Classification mirrors the adapter's own transport semantics
(:meth:`EmailChannel._open_imap_client` and :meth:`EmailChannel._smtp_send`):

- IMAP: ``imap_use_ssl`` → ``IMAP4_SSL`` else ``IMAP4`` + ``starttls`` when
  ``imap_use_tls``; a connect or upgrade failure is
  ``network``, a rejected login is ``invalid_credentials``, and a mailbox
  that cannot be selected is ``invalid_credentials`` naming the mailbox.
- SMTP: ``smtp_use_ssl`` → ``SMTP_SSL`` else ``SMTP`` + ``starttls`` when
  ``smtp_use_tls``; an authentication rejection is ``invalid_credentials``,
  every other transport failure is ``network``.

Details are prefixed with the failing side (``imap:`` / ``smtp:``), bounded to
200 characters and scrubbed of both the password and the username, so a
server that echoes the credential in its rejection never leaks it into a
result envelope or a log line.
"""

from __future__ import annotations

import asyncio
import contextlib
import imaplib
import smtplib
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from src.interfaces.channels.token_probe import scrub_secrets
from src.interfaces.channels.utils import email_tls_context, send_imap_id

if TYPE_CHECKING:
    from src.interfaces.channels.email import EmailConfig

_CONNECT_TIMEOUT_S = 10
_DETAIL_LIMIT = 200

# Bound at import: ``imaplib.IMAP4.error`` is a stable exception class
# (``.abort`` is a subclass), and resolving it through the ``IMAP4`` attribute
# at call time would break whenever the class itself is replaced (tests).
_IMAP4_ERROR = imaplib.IMAP4.error

_CREDENTIAL_FIELDS = (
    "imap_host",
    "imap_username",
    "imap_password",
    "smtp_host",
    "smtp_username",
    "smtp_password",
)


def _bounded(side: str, exc: BaseException, secrets: Iterable[str]) -> str:
    """Return a scrubbed, bounded ``<side>: <error>`` detail string."""
    text = str(exc) or type(exc).__name__
    return scrub_secrets(f"{side}: {text}", secrets)[:_DETAIL_LIMIT]


def _select_reason(status: str, data: Any) -> str:
    """Extract a human-readable reason from an IMAP ``select`` response."""
    if isinstance(data, (list, tuple)):
        for item in data:
            if isinstance(item, (bytes, bytearray)) and item.strip():
                return bytes(item).decode("utf-8", errors="replace").strip()
            if isinstance(item, str) and item.strip():
                return item.strip()
    return status


def _probe_imap(config: EmailConfig) -> tuple[str, str] | None:
    """Login + mailbox-select over IMAP; return ``(code, detail)`` or None."""
    secrets = (config.imap_password, config.imap_username)
    mailbox = config.imap_mailbox or "INBOX"
    client: imaplib.IMAP4 | None = None
    try:
        try:
            if config.imap_use_ssl:
                client = imaplib.IMAP4_SSL(
                    config.imap_host,
                    config.imap_port,
                    timeout=_CONNECT_TIMEOUT_S,
                    ssl_context=email_tls_context(config.verify_tls),
                )
            else:
                client = imaplib.IMAP4(
                    config.imap_host, config.imap_port, timeout=_CONNECT_TIMEOUT_S
                )
                if config.imap_use_tls:
                    client.starttls(ssl_context=email_tls_context(config.verify_tls))
        except (OSError, _IMAP4_ERROR) as exc:
            # A transport that cannot be established or upgraded (no STARTTLS
            # offered, a failed handshake) is network-class, as for SMTP.
            return "network", _bounded("imap", exc, secrets)

        try:
            client.login(config.imap_username, config.imap_password)
        except _IMAP4_ERROR as exc:  # IMAP4.abort is a subclass
            return "invalid_credentials", _bounded("imap", exc, secrets)
        except OSError as exc:
            return "network", _bounded("imap", exc, secrets)

        # NetEase requires an IMAP ID before SELECT; harmless elsewhere.
        send_imap_id(client)

        try:
            status, data = client.select(mailbox)
        except Exception as exc:  # noqa: BLE001 - any select failure names the mailbox
            reason = str(exc) or type(exc).__name__
            detail = f"imap mailbox select failed: {mailbox} ({reason})"
            return "invalid_credentials", scrub_secrets(detail, secrets)[:_DETAIL_LIMIT]
        if status != "OK":
            detail = f"imap mailbox select failed: {mailbox} ({_select_reason(status, data)})"
            return "invalid_credentials", scrub_secrets(detail, secrets)[:_DETAIL_LIMIT]
    finally:
        if client is not None:
            with contextlib.suppress(Exception):
                client.logout()
    return None


def _probe_smtp(config: EmailConfig) -> tuple[str, str] | None:
    """Connect (+ optional STARTTLS) and login over SMTP; ``(code, detail)`` or None."""
    secrets = (config.smtp_password, config.smtp_username)
    client: smtplib.SMTP | None = None
    try:
        try:
            if config.smtp_use_ssl:
                client = smtplib.SMTP_SSL(
                    config.smtp_host,
                    config.smtp_port,
                    timeout=_CONNECT_TIMEOUT_S,
                    ssl_context=email_tls_context(config.verify_tls),
                )
            else:
                client = smtplib.SMTP(
                    config.smtp_host, config.smtp_port, timeout=_CONNECT_TIMEOUT_S
                )
                if config.smtp_use_tls:
                    client.starttls(context=email_tls_context(config.verify_tls))
        except (smtplib.SMTPException, OSError) as exc:
            # Covers SMTPConnectError / SMTPServerDisconnected / socket.timeout;
            # a transport that cannot be established or upgraded is network-class.
            return "network", _bounded("smtp", exc, secrets)

        try:
            client.login(config.smtp_username, config.smtp_password)
        except smtplib.SMTPAuthenticationError as exc:
            return "invalid_credentials", _bounded("smtp", exc, secrets)
        except (smtplib.SMTPException, OSError) as exc:
            return "network", _bounded("smtp", exc, secrets)
    finally:
        if client is not None:
            with contextlib.suppress(Exception):
                client.quit()
    return None


async def test_connection(config: EmailConfig) -> dict[str, Any]:
    """Validate the email credentials with standalone IMAP + SMTP probes.

    Uses fresh ``imaplib`` / ``smtplib`` connections rather than the channel's
    polling loop (which only exists after :meth:`EmailChannel.start`), so an
    unsaved credential set can be checked before the channel is started. The
    SMTP half runs only after the IMAP half passes, and no message is ever
    sent: the probe logs in and selects a mailbox, nothing more.

    Args:
        config: The email credential set to validate.

    Returns:
        A JSON-serializable envelope with ``ok`` and a ``code`` of ``ok`` /
        ``invalid_credentials`` / ``network``, plus ``sdk_available`` (always
        True — the probe is stdlib-only). Any ``detail`` is bounded to 200
        characters and scrubbed of credential values.
    """
    missing = [field for field in _CREDENTIAL_FIELDS if not getattr(config, field)]
    if missing:
        # Field NAMES are not secrets; naming them tells the operator exactly
        # which half (IMAP/SMTP) of the six-field credential set is incomplete.
        return {
            "ok": False,
            "code": "invalid_credentials",
            "detail": "missing credentials: " + ", ".join(missing),
            "sdk_available": True,
        }

    failure = await asyncio.to_thread(_probe_imap, config)
    if failure is None:
        failure = await asyncio.to_thread(_probe_smtp, config)
    if failure is not None:
        code, detail = failure
        return {"ok": False, "code": code, "detail": detail, "sdk_available": True}
    return {"ok": True, "code": "ok", "sdk_available": True}
