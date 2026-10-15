"""Connection-test contract for the Email channel.

Mirrors ``test_qq_connection_test.py``: Email implements the standalone
credential probe (IMAP login + mailbox select, then SMTP connect + login)
without ever starting the polling loop or sending a message. The contract
codes the frontend dispatches on are ``ok | invalid_credentials | network``
plus an ``sdk_available`` flag (always True — the probe is stdlib-only), and
no password or username value may ever leak into the result or logs. All
``imaplib`` / ``smtplib`` transports are replaced with recording fakes, so no
test performs real network I/O.
"""

from __future__ import annotations

import asyncio
import imaplib
import json
import logging
import smtplib
import socket
import ssl
from email.message import EmailMessage
from typing import Any

import pytest

from src.interfaces.channels.bus.queue import MessageBus
from src.interfaces.channels.email import EmailChannel
from src.interfaces.channels.utils import email_tls_context, send_imap_id

IMAP_HOST = "imap.example.com"
SMTP_HOST = "smtp.example.com"
IMAP_USER = "bot@example.com"
IMAP_PASS = "imap-password-secret-1234"
SMTP_USER = "sender@example.com"
SMTP_PASS = "smtp-password-secret-5678"

_CREDENTIAL_FIELDS = (
    "imap_host",
    "imap_username",
    "imap_password",
    "smtp_host",
    "smtp_username",
    "smtp_password",
)


def _config(**overrides: Any) -> dict[str, Any]:
    section: dict[str, Any] = {
        "imap_host": IMAP_HOST,
        "imap_username": IMAP_USER,
        "imap_password": IMAP_PASS,
        "smtp_host": SMTP_HOST,
        "smtp_username": SMTP_USER,
        "smtp_password": SMTP_PASS,
    }
    section.update(overrides)
    return section


def _make_channel(**overrides: Any) -> EmailChannel:
    """Build an Email channel that has NOT been started (no polling loop)."""
    return EmailChannel(_config(**overrides), MessageBus())


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _install_imap_fakes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    connect_error: BaseException | None = None,
    login_error: BaseException | None = None,
    select_result: tuple[str, list[Any]] = ("OK", [b"1"]),
    select_error: BaseException | None = None,
    starttls_error: BaseException | None = None,
) -> dict[str, Any]:
    """Replace ``imaplib.IMAP4``/``IMAP4_SSL`` with recording fakes."""
    calls: dict[str, Any] = {
        "constructed": [],
        "ssl_contexts": [],
        "starttls": [],
        "order": [],
        "logins": [],
        "ids": [],
        "selects": [],
        "logouts": 0,
    }

    class FakeIMAP4:
        kind = "IMAP4"

        def __init__(
            self,
            host: str,
            port: int,
            timeout: float | None = None,
            ssl_context: Any = None,
        ) -> None:
            calls["constructed"].append((self.kind, host, port, timeout))
            calls["ssl_contexts"].append(ssl_context)
            if connect_error is not None:
                raise connect_error

        def starttls(self, ssl_context: Any = None) -> tuple[str, list[bytes]]:
            calls["starttls"].append(ssl_context)
            calls["order"].append("starttls")
            if starttls_error is not None:
                raise starttls_error
            return ("OK", [b"Begin TLS negotiation now"])

        def login(self, user: str, password: str) -> tuple[str, list[bytes]]:
            calls["logins"].append((user, password))
            calls["order"].append("login")
            if login_error is not None:
                raise login_error
            return ("OK", [b"LOGIN completed"])

        def xatom(self, name: str, *args: Any) -> tuple[str, list[Any]]:
            calls["ids"].append((name, *args))
            return ("OK", [b"ID completed"])

        def select(self, mailbox: str = "INBOX") -> tuple[str, list[Any]]:
            calls["selects"].append(mailbox)
            if select_error is not None:
                raise select_error
            return select_result

        def logout(self) -> tuple[str, list[bytes]]:
            calls["logouts"] += 1
            return ("BYE", [b"bye"])

    class FakeIMAP4_SSL(FakeIMAP4):
        kind = "IMAP4_SSL"

    monkeypatch.setattr(imaplib, "IMAP4", FakeIMAP4)
    monkeypatch.setattr(imaplib, "IMAP4_SSL", FakeIMAP4_SSL)
    return calls


def _install_smtp_fakes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    connect_error: BaseException | None = None,
    starttls_error: BaseException | None = None,
    login_error: BaseException | None = None,
) -> dict[str, Any]:
    """Replace ``smtplib.SMTP``/``SMTP_SSL`` with recording fakes."""
    calls: dict[str, Any] = {
        "constructed": [],
        "ssl_contexts": [],
        "starttls": [],
        "logins": [],
        "sent": [],
        "quits": 0,
    }

    class FakeSMTP:
        kind = "SMTP"

        def __init__(
            self,
            host: str,
            port: int,
            timeout: float | None = None,
            ssl_context: Any = None,
        ) -> None:
            calls["constructed"].append((self.kind, host, port, timeout))
            calls["ssl_contexts"].append(ssl_context)
            if connect_error is not None:
                raise connect_error

        def __enter__(self) -> "FakeSMTP":
            return self

        def __exit__(self, *exc_info: Any) -> None:
            return None

        def send_message(self, msg: Any) -> dict[Any, Any]:
            calls["sent"].append(msg)
            return {}

        def starttls(self, context: Any = None) -> tuple[int, bytes]:
            calls["starttls"].append(context is not None)
            if starttls_error is not None:
                raise starttls_error
            return (220, b"Ready to start TLS")

        def login(self, user: str, password: str) -> tuple[int, bytes]:
            calls["logins"].append((user, password))
            if login_error is not None:
                raise login_error
            return (235, b"Authentication successful")

        def quit(self) -> tuple[int, bytes]:
            calls["quits"] += 1
            return (221, b"Bye")

    class FakeSMTP_SSL(FakeSMTP):
        kind = "SMTP_SSL"

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP_SSL)
    return calls


def _install_forbidden_fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail the test if any IMAP/SMTP transport is constructed at all."""

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("no IMAP/SMTP connection may happen without credentials")

    monkeypatch.setattr(imaplib, "IMAP4", forbidden)
    monkeypatch.setattr(imaplib, "IMAP4_SSL", forbidden)
    monkeypatch.setattr(smtplib, "SMTP", forbidden)
    monkeypatch.setattr(smtplib, "SMTP_SSL", forbidden)


# --------------------------------------------------------------------------- #
# Success
# --------------------------------------------------------------------------- #


def test_success_probes_imap_then_smtp(monkeypatch: pytest.MonkeyPatch) -> None:
    assert EmailChannel.supports_connection_test is True
    imap_calls = _install_imap_fakes(monkeypatch)
    smtp_calls = _install_smtp_fakes(monkeypatch)

    result = _run(_make_channel().test_connection())

    assert result["ok"] is True
    assert result["code"] == "ok"
    # Email is stdlib-only: sdk_available is unconditionally True.
    assert result["sdk_available"] is True
    assert imap_calls["constructed"] == [("IMAP4_SSL", IMAP_HOST, 993, 10)]
    assert imap_calls["logins"] == [(IMAP_USER, IMAP_PASS)]
    assert imap_calls["selects"] == ["INBOX"]
    assert imap_calls["logouts"] == 1
    # Defaults: smtp_use_ssl=False, smtp_use_tls=True → SMTP + starttls(context).
    assert smtp_calls["constructed"] == [("SMTP", SMTP_HOST, 587, 10)]
    assert smtp_calls["starttls"] == [True]
    assert smtp_calls["logins"] == [(SMTP_USER, SMTP_PASS)]
    assert smtp_calls["quits"] == 1
    serialized = json.dumps(result)
    assert IMAP_PASS not in serialized
    assert SMTP_PASS not in serialized


def test_custom_mailbox_is_selected(monkeypatch: pytest.MonkeyPatch) -> None:
    imap_calls = _install_imap_fakes(monkeypatch)
    _install_smtp_fakes(monkeypatch)

    result = _run(_make_channel(imap_mailbox="Archive").test_connection())

    assert result["ok"] is True
    assert imap_calls["selects"] == ["Archive"]


# --------------------------------------------------------------------------- #
# Missing-credentials short circuit
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("field", _CREDENTIAL_FIELDS)
def test_missing_credential_short_circuits_without_io(
    monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    _install_forbidden_fakes(monkeypatch)

    result = _run(_make_channel(**{field: ""}).test_connection())

    assert result["ok"] is False
    assert result["code"] == "invalid_credentials"
    assert result["detail"] == f"missing credentials: {field}"
    assert result["sdk_available"] is True


def test_multiple_missing_credentials_are_all_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_forbidden_fakes(monkeypatch)

    result = _run(_make_channel(imap_host="", smtp_password="").test_connection())

    assert result["ok"] is False
    assert result["code"] == "invalid_credentials"
    assert result["detail"] == "missing credentials: imap_host, smtp_password"


# --------------------------------------------------------------------------- #
# IMAP classification
# --------------------------------------------------------------------------- #


def test_imap_login_rejection_reports_invalid_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_imap_fakes(
        monkeypatch, login_error=imaplib.IMAP4.error("LOGIN failed: bad password")
    )
    smtp_calls = _install_smtp_fakes(monkeypatch)

    result = _run(_make_channel().test_connection())

    assert result["ok"] is False
    assert result["code"] == "invalid_credentials"
    assert result["detail"].startswith("imap: ")
    assert result["sdk_available"] is True
    # The SMTP half never runs after an IMAP failure.
    assert smtp_calls["constructed"] == []


def test_imap_abort_on_login_reports_invalid_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_imap_fakes(
        monkeypatch, login_error=imaplib.IMAP4.abort("connection aborted mid-login")
    )
    _install_smtp_fakes(monkeypatch)

    result = _run(_make_channel().test_connection())

    assert result["ok"] is False
    assert result["code"] == "invalid_credentials"


@pytest.mark.parametrize(
    "connect_error",
    [OSError("connection refused"), socket.timeout("timed out")],
)
def test_imap_connect_failure_reports_network(
    monkeypatch: pytest.MonkeyPatch, connect_error: BaseException
) -> None:
    _install_imap_fakes(monkeypatch, connect_error=connect_error)
    _install_smtp_fakes(monkeypatch)

    result = _run(_make_channel().test_connection())

    assert result["ok"] is False
    assert result["code"] == "network"
    assert result["detail"].startswith("imap: ")


def test_imap_select_non_ok_names_mailbox(monkeypatch: pytest.MonkeyPatch) -> None:
    imap_calls = _install_imap_fakes(
        monkeypatch, select_result=("NO", [b"Mailbox does not exist"])
    )
    _install_smtp_fakes(monkeypatch)

    result = _run(_make_channel(imap_mailbox="Archive").test_connection())

    assert result["ok"] is False
    assert result["code"] == "invalid_credentials"
    assert "Archive" in result["detail"]
    assert "select failed" in result["detail"]
    # The connection is still logged out after a select failure.
    assert imap_calls["logouts"] == 1


def test_imap_select_exception_names_mailbox(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_imap_fakes(
        monkeypatch, select_error=imaplib.IMAP4.error("SELECT exploded")
    )
    _install_smtp_fakes(monkeypatch)

    result = _run(_make_channel(imap_mailbox="Archive").test_connection())

    assert result["ok"] is False
    assert result["code"] == "invalid_credentials"
    assert "Archive" in result["detail"]
    assert "SELECT exploded" in result["detail"]


def test_plain_imap_transport_when_ssl_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    imap_calls = _install_imap_fakes(monkeypatch)
    _install_smtp_fakes(monkeypatch)

    result = _run(_make_channel(imap_use_ssl=False).test_connection())

    assert result["ok"] is True
    assert imap_calls["constructed"] == [("IMAP4", IMAP_HOST, 993, 10)]
    # Plain IMAP is upgraded before the password goes out (imap_use_tls default).
    assert imap_calls["order"] == ["starttls", "login"]


def test_plain_imap_login_needs_an_explicit_opt_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``imap_use_tls=false`` is the one way to log in without TLS, as smtp_use_tls is."""
    imap_calls = _install_imap_fakes(monkeypatch)
    _install_smtp_fakes(monkeypatch)

    result = _run(_make_channel(imap_use_ssl=False, imap_use_tls=False).test_connection())

    assert result["ok"] is True
    assert imap_calls["starttls"] == []
    assert imap_calls["order"] == ["login"]


def test_a_server_without_starttls_never_receives_the_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refused upgrade is a transport failure, reported before any LOGIN."""
    imap_calls = _install_imap_fakes(
        monkeypatch,
        starttls_error=imaplib.IMAP4.error("STARTTLS extension not supported by server."),
    )
    _install_smtp_fakes(monkeypatch)

    result = _run(_make_channel(imap_use_ssl=False).test_connection())

    assert result["ok"] is False
    assert result["code"] == "network"
    assert result["detail"].startswith("imap: ")
    assert imap_calls["logins"] == []


# --------------------------------------------------------------------------- #
# SMTP classification
# --------------------------------------------------------------------------- #


def test_smtp_auth_error_reports_invalid_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_imap_fakes(monkeypatch)
    _install_smtp_fakes(
        monkeypatch,
        login_error=smtplib.SMTPAuthenticationError(535, b"authentication failed"),
    )

    result = _run(_make_channel().test_connection())

    assert result["ok"] is False
    assert result["code"] == "invalid_credentials"
    assert result["detail"].startswith("smtp: ")
    assert "535" in result["detail"]


def test_smtp_connect_error_reports_network(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_imap_fakes(monkeypatch)
    _install_smtp_fakes(
        monkeypatch, connect_error=smtplib.SMTPConnectError(421, b"try later")
    )

    result = _run(_make_channel().test_connection())

    assert result["ok"] is False
    assert result["code"] == "network"
    assert result["detail"].startswith("smtp: ")


def test_smtp_starttls_failure_reports_network(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_imap_fakes(monkeypatch)
    _install_smtp_fakes(
        monkeypatch,
        starttls_error=smtplib.SMTPResponseException(502, b"STARTTLS not supported"),
    )

    result = _run(_make_channel().test_connection())

    assert result["ok"] is False
    assert result["code"] == "network"


def test_smtp_login_disconnect_reports_network(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_imap_fakes(monkeypatch)
    _install_smtp_fakes(
        monkeypatch,
        login_error=smtplib.SMTPServerDisconnected("connection lost during login"),
    )

    result = _run(_make_channel().test_connection())

    assert result["ok"] is False
    assert result["code"] == "network"


def test_smtp_ssl_transport_skips_starttls(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_imap_fakes(monkeypatch)
    smtp_calls = _install_smtp_fakes(monkeypatch)

    result = _run(_make_channel(smtp_use_ssl=True, smtp_port=465).test_connection())

    assert result["ok"] is True
    assert smtp_calls["constructed"] == [("SMTP_SSL", SMTP_HOST, 465, 10)]
    assert smtp_calls["starttls"] == []


# --------------------------------------------------------------------------- #
# TLS certificate verification (verify_tls)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("verify", [True, False])
def test_email_tls_context_verify_modes(verify: bool) -> None:
    ctx = email_tls_context(verify)

    if verify:
        assert ctx.verify_mode == ssl.CERT_REQUIRED
        assert ctx.check_hostname is True
    else:
        assert ctx.verify_mode == ssl.CERT_NONE
        assert ctx.check_hostname is False


@pytest.mark.parametrize("verify_tls", [True, False])
def test_probe_imap_ssl_context_follows_verify_tls(
    monkeypatch: pytest.MonkeyPatch, verify_tls: bool
) -> None:
    """The probe must never send the IMAP password over an unverified socket
    unless the operator explicitly opted out with ``verify_tls=False``."""
    imap_calls = _install_imap_fakes(monkeypatch)
    _install_smtp_fakes(monkeypatch)

    result = _run(_make_channel(verify_tls=verify_tls).test_connection())

    assert result["ok"] is True
    ctx = imap_calls["ssl_contexts"][0]
    assert isinstance(ctx, ssl.SSLContext)
    if verify_tls:
        assert ctx.verify_mode == ssl.CERT_REQUIRED
        assert ctx.check_hostname is True
    else:
        assert ctx.verify_mode == ssl.CERT_NONE
        assert ctx.check_hostname is False


@pytest.mark.parametrize("verify_tls", [True, False])
def test_probe_smtp_ssl_context_follows_verify_tls(
    monkeypatch: pytest.MonkeyPatch, verify_tls: bool
) -> None:
    imap_calls = _install_imap_fakes(monkeypatch)
    smtp_calls = _install_smtp_fakes(monkeypatch)

    result = _run(
        _make_channel(
            smtp_use_ssl=True, smtp_port=465, verify_tls=verify_tls
        ).test_connection()
    )

    assert result["ok"] is True
    ctx = smtp_calls["ssl_contexts"][0]
    assert isinstance(ctx, ssl.SSLContext)
    if verify_tls:
        assert ctx.verify_mode == ssl.CERT_REQUIRED
        assert ctx.check_hostname is True
    else:
        assert ctx.verify_mode == ssl.CERT_NONE
        assert ctx.check_hostname is False
    # The plain IMAP4_SSL probe half shares the same verify_tls switch.
    assert isinstance(imap_calls["ssl_contexts"][0], ssl.SSLContext)


def test_probe_plain_transports_pass_no_ssl_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-SSL transports take no constructor context; both upgrade with STARTTLS."""
    imap_calls = _install_imap_fakes(monkeypatch)
    smtp_calls = _install_smtp_fakes(monkeypatch)

    result = _run(
        _make_channel(imap_use_ssl=False, smtp_use_ssl=False).test_connection()
    )

    assert result["ok"] is True
    assert imap_calls["ssl_contexts"] == [None]
    assert smtp_calls["ssl_contexts"] == [None]
    assert len(imap_calls["starttls"]) == 1
    assert smtp_calls["starttls"] == [True]


@pytest.mark.parametrize("verify_tls", [True, False])
def test_starttls_follows_verify_tls_on_both_sides(
    monkeypatch: pytest.MonkeyPatch, verify_tls: bool
) -> None:
    """``verify_tls`` is one switch for every TLS path, STARTTLS included."""
    imap_calls = _install_imap_fakes(monkeypatch)
    smtp_contexts: list[Any] = []
    _install_smtp_fakes(monkeypatch)
    real_starttls = smtplib.SMTP.starttls

    def record(self: Any, context: Any = None) -> Any:
        smtp_contexts.append(context)
        return real_starttls(self, context=context)

    monkeypatch.setattr(smtplib.SMTP, "starttls", record)

    result = _run(
        _make_channel(
            imap_use_ssl=False, smtp_use_ssl=False, verify_tls=verify_tls
        ).test_connection()
    )

    assert result["ok"] is True
    expected = ssl.CERT_REQUIRED if verify_tls else ssl.CERT_NONE
    for ctx in (imap_calls["starttls"][0], smtp_contexts[0]):
        assert ctx.verify_mode == expected
        assert ctx.check_hostname is verify_tls


@pytest.mark.parametrize("verify_tls", [True, False])
def test_adapter_imap_ssl_context_follows_verify_tls(
    monkeypatch: pytest.MonkeyPatch, verify_tls: bool
) -> None:
    """The adapter's polling connection uses the same helper as the probe."""
    imap_calls = _install_imap_fakes(monkeypatch)
    channel = _make_channel(verify_tls=verify_tls)

    client = channel._open_imap_client("INBOX")

    assert client is not None
    ctx = imap_calls["ssl_contexts"][0]
    expected = ssl.CERT_REQUIRED if verify_tls else ssl.CERT_NONE
    assert ctx.verify_mode == expected
    assert ctx.check_hostname is verify_tls


@pytest.mark.parametrize("verify_tls", [True, False])
def test_adapter_smtp_ssl_context_follows_verify_tls(
    monkeypatch: pytest.MonkeyPatch, verify_tls: bool
) -> None:
    smtp_calls = _install_smtp_fakes(monkeypatch)
    channel = _make_channel(smtp_use_ssl=True, smtp_port=465, verify_tls=verify_tls)

    channel._smtp_send(EmailMessage())

    ctx = smtp_calls["ssl_contexts"][0]
    expected = ssl.CERT_REQUIRED if verify_tls else ssl.CERT_NONE
    assert ctx.verify_mode == expected
    assert ctx.check_hostname is verify_tls
    assert len(smtp_calls["sent"]) == 1


def test_verify_tls_defaults_to_true() -> None:
    """Secure by default: certificate verification is on unless opted out."""
    channel = _make_channel()

    assert channel.config.verify_tls is True


# --------------------------------------------------------------------------- #
# Secret hygiene
# --------------------------------------------------------------------------- #


def test_server_error_echoing_credentials_is_scrubbed(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _install_imap_fakes(
        monkeypatch,
        login_error=imaplib.IMAP4.error(
            f"LOGIN failed: user {IMAP_USER} password {IMAP_PASS} rejected"
        ),
    )
    _install_smtp_fakes(monkeypatch)

    with caplog.at_level(logging.DEBUG):
        result = _run(_make_channel().test_connection())

    assert result["code"] == "invalid_credentials"
    serialized = json.dumps(result)
    assert IMAP_PASS not in serialized
    assert IMAP_USER not in serialized
    assert "***" in result["detail"]
    assert IMAP_PASS not in caplog.text
    assert IMAP_USER not in caplog.text


def test_smtp_error_echoing_credentials_is_scrubbed(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _install_imap_fakes(monkeypatch)
    _install_smtp_fakes(
        monkeypatch,
        login_error=smtplib.SMTPAuthenticationError(
            535, f"bad login for {SMTP_USER} with {SMTP_PASS}".encode()
        ),
    )

    with caplog.at_level(logging.DEBUG):
        result = _run(_make_channel().test_connection())

    assert result["code"] == "invalid_credentials"
    serialized = json.dumps(result)
    assert SMTP_PASS not in serialized
    assert SMTP_PASS not in caplog.text


def test_detail_is_bounded_to_200_chars(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_imap_fakes(monkeypatch, login_error=imaplib.IMAP4.error("x" * 500))
    _install_smtp_fakes(monkeypatch)

    result = _run(_make_channel().test_connection())

    assert result["code"] == "invalid_credentials"
    assert len(result["detail"]) <= 200


def test_result_envelope_is_json_serializable(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_imap_fakes(monkeypatch)
    _install_smtp_fakes(monkeypatch)

    result = _run(_make_channel().test_connection())

    assert json.loads(json.dumps(result)) == result


def test_probe_module_never_imports_the_adapter_at_runtime() -> None:
    """The probe keeps the adapter import TYPE_CHECKING-only (no cycle)."""
    import inspect as _inspect

    from src.interfaces.channels import email_probe

    source = _inspect.getsource(email_probe)
    assert "from src.interfaces.channels.email import EmailConfig" in source
    assert "if TYPE_CHECKING:" in source


# --------------------------------------------------------------------------- #
# start()/stop() interaction with destructive post-actions
# --------------------------------------------------------------------------- #


def test_stop_during_fetch_delivers_batch_but_skips_post_actions() -> None:
    """stop() is flag-only; a fetch already in flight still delivers.

    The already-fetched batch must reach the bus (those messages were marked
    seen during fetch — dropping them would lose mail), but the destructive
    delete/move post-actions of the stale config must not run after stop.
    """
    channel = _make_channel(consent_granted=True, post_action="delete")
    delivered: list[str] = []
    applied: list[list[str]] = []

    def fake_fetch() -> tuple[list[dict[str, Any]], set[str]]:
        channel._running = False
        item = {
            "sender": "user@example.com",
            "subject": "hello",
            "message_id": "<m1@example.com>",
            "content": "in-flight body",
            "metadata": {"uid": "42"},
            "media": [],
        }
        return [item], set()

    async def fake_handle(**kwargs: Any) -> None:
        delivered.append(kwargs["content"])

    def fake_post_actions(uids: list[str]) -> None:
        applied.append(uids)

    channel._fetch_new_messages = fake_fetch  # type: ignore[method-assign]
    channel._handle_message = fake_handle  # type: ignore[method-assign]
    channel._apply_post_actions_batch = fake_post_actions  # type: ignore[method-assign]

    _run(channel.start())

    assert delivered == ["in-flight body"]
    assert applied == []


# --------------------------------------------------------------------------- #
# IMAP ID (RFC 2971) — NetEase "Unsafe Login" interop
# --------------------------------------------------------------------------- #


def test_send_imap_id_sends_static_identification() -> None:
    sent: list[tuple[Any, ...]] = []

    class _Client:
        def xatom(self, name: str, *args: Any) -> tuple[str, list[bytes]]:
            sent.append((name, *args))
            return ("OK", [b"ID completed"])

    send_imap_id(_Client())

    assert len(sent) == 1
    name, payload = sent[0]
    assert name == "ID"
    assert "vibe-trading" in payload


def test_send_imap_id_swallows_errors() -> None:
    class _Broken:
        def xatom(self, *args: Any) -> Any:
            raise OSError("connection reset")

    send_imap_id(_Broken())


def test_send_imap_id_tolerates_client_without_xatom() -> None:
    send_imap_id(object())


def test_probe_sends_imap_id_before_select(monkeypatch: pytest.MonkeyPatch) -> None:
    """NetEase regression: ID must be sent between LOGIN and SELECT."""
    order: list[str] = []

    class FakeIMAP4_SSL:
        def __init__(
            self,
            host: str,
            port: int,
            timeout: float | None = None,
            ssl_context: Any = None,
        ) -> None:
            order.append("connect")

        def login(self, user: str, password: str) -> tuple[str, list[bytes]]:
            order.append("login")
            return ("OK", [b"LOGIN completed"])

        def xatom(self, name: str, *args: Any) -> tuple[str, list[Any]]:
            order.append(f"id:{name}")
            return ("OK", [b"ID completed"])

        def select(self, mailbox: str = "INBOX") -> tuple[str, list[Any]]:
            order.append("select")
            return ("OK", [b"3"])

        def logout(self) -> tuple[str, list[bytes]]:
            order.append("logout")
            return ("BYE", [b"bye"])

    monkeypatch.setattr(imaplib, "IMAP4_SSL", FakeIMAP4_SSL)
    _install_smtp_fakes(monkeypatch)

    result = _run(_make_channel(imap_use_ssl=True).test_connection())

    assert result["ok"] is True
    assert order[:4] == ["connect", "login", "id:ID", "select"]


def test_adapter_upgrades_plain_imap_before_login(monkeypatch: pytest.MonkeyPatch) -> None:
    """The polling connection follows the probe: STARTTLS, then LOGIN."""
    imap_calls = _install_imap_fakes(monkeypatch)
    channel = _make_channel(imap_use_ssl=False)

    client = channel._open_imap_client("INBOX")

    assert client is not None
    assert imap_calls["order"] == ["starttls", "login"]
    assert imap_calls["starttls"][0].verify_mode == ssl.CERT_REQUIRED


@pytest.mark.parametrize("verify_tls", [True, False])
def test_adapter_smtp_starttls_follows_verify_tls(
    monkeypatch: pytest.MonkeyPatch, verify_tls: bool
) -> None:
    """The send path's STARTTLS takes the same context as implicit SSL."""
    _install_smtp_fakes(monkeypatch)
    contexts: list[Any] = []
    real_starttls = smtplib.SMTP.starttls

    def record(self: Any, context: Any = None) -> Any:
        contexts.append(context)
        return real_starttls(self, context=context)

    monkeypatch.setattr(smtplib.SMTP, "starttls", record)
    channel = _make_channel(smtp_use_ssl=False, verify_tls=verify_tls)

    channel._smtp_send(EmailMessage())

    expected = ssl.CERT_REQUIRED if verify_tls else ssl.CERT_NONE
    assert contexts[0].verify_mode == expected
    assert contexts[0].check_hostname is verify_tls
