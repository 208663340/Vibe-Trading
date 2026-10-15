"""Standalone WebSocket channel probe — local validation, no remote endpoint.

WebSocket is a *server* channel: vibe-trading listens and clients connect, so
there are no remote credentials to validate against a token endpoint (cf.
:mod:`src.interfaces.channels.token_probe`). Instead this probe checks the two things
that make :meth:`WebSocketChannel.start` fail in practice:

1. **SSL material** — when ``ssl_certfile`` / ``ssl_keyfile`` are set, the
   cert/key pair must load into a server ``SSLContext`` (same construction as
   the adapter's ``_build_ssl_context``). A missing or malformed file is
   ``invalid_credentials``: it is bad local secret material, not a network
   problem.
2. **Address availability** — a read-only TCP bind of ``host:port`` that
    mirrors what ``serve()`` (via ``loop.create_server``) will do: every
    unique resolved address is bound and held simultaneously (e.g.
    ``localhost`` → ``::1`` + ``127.0.0.1``), with ``SO_REUSEADDR`` set only
    where ``create_server`` sets it (POSIX, non-cygwin — on Windows the flag
    would let the probe bind an occupied port and report a false ``ok``).
    The honest semantics of ``EADDRINUSE``: while this channel is *running*
    the address is legitimately occupied by us, so a plain bind would report
    a false negative on exactly the hot-config Test click the probe exists
    for. The probe therefore follows an ``EADDRINUSE`` with a plain TCP
    connect: a listening server answers (ok, with an explanatory detail),
    while a held but non-listening address does not (``network``).

When ``unix_socket_path`` is set, ``start()`` ignores host/port entirely and
mkdirs the parent + unlinks a stale socket before binding — side effects a
read-only probe must never perform — so the bind check is skipped and only
the SSL material is validated.

The probe performs no filesystem writes, creates no directories and unlinks
nothing. Details are scrubbed of ``token`` / ``token_issue_secret``
defensively (cert/key *paths* may appear: they are not secrets) and bounded
to 200 characters.
"""

from __future__ import annotations

import asyncio
import errno
import os
import socket
import ssl
import sys
from typing import TYPE_CHECKING, Any

from src.interfaces.channels.token_probe import scrub_secrets

if TYPE_CHECKING:
    from src.interfaces.channels.websocket import WebSocketConfig

_DETAIL_LIMIT = 200
_CONNECT_TIMEOUT_S = 3

_ADDRESS_IN_USE_DETAIL = (
    "address already in use by a listening server (expected while this "
    "channel is running; otherwise another process holds host:port)"
)


def _websockets_available() -> bool:
    """Return whether the ``websockets`` core dependency imports."""
    try:
        import websockets  # noqa: F401
    except ImportError:  # pragma: no cover - websockets is a core dependency
        return False
    return True


def _check_ssl_material(config: WebSocketConfig) -> dict[str, Any] | None:
    """Load the configured cert/key pair; return a failure envelope or None."""
    cert = config.ssl_certfile.strip()
    key = config.ssl_keyfile.strip()
    if not cert and not key:
        return None
    if not cert or not key:
        # Same both-or-neither rule and message as the adapter's
        # _build_ssl_context, surfaced before load_cert_chain can obscure it.
        return {
            "ok": False,
            "code": "invalid_credentials",
            "detail": (
                "ssl: ssl_certfile and ssl_keyfile must both be set for WSS, "
                "or both left empty"
            ),
        }
    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile=cert, keyfile=key)
    except (OSError, ssl.SSLError, ValueError) as exc:
        return {"ok": False, "code": "invalid_credentials", "detail": f"ssl: {exc}"}
    return None


def _check_listening(addr: Any) -> dict[str, Any]:
    """Disambiguate ``EADDRINUSE``: a live listener is expected, not a failure."""
    try:
        with socket.create_connection(addr, timeout=_CONNECT_TIMEOUT_S):
            pass
    except OSError as exc:
        return {
            "ok": False,
            "code": "network",
            "detail": f"bind: address in use but not accepting connections: {exc}",
        }
    return {"ok": True, "code": "ok", "detail": _ADDRESS_IN_USE_DETAIL}


def _check_tcp_bind(config: WebSocketConfig) -> dict[str, Any] | None:
    """Resolve + bind ``host:port`` read-only; return an envelope or None."""
    if config.unix_socket_path:
        # start() ignores host/port when a unix socket is configured, and
        # preparing one (mkdir + unlink of a stale socket) is a write side
        # effect this probe must not perform — nothing meaningful to check.
        return None
    try:
        infos = socket.getaddrinfo(config.host, config.port, type=socket.SOCK_STREAM)
    except OSError as exc:  # includes socket.gaierror for unresolvable hosts
        return {"ok": False, "code": "network", "detail": f"bind: {exc}"}

    # create_server's reuse_address default: POSIX only. On Windows the flag
    # would let this bind succeed against an occupied port — a false ok while
    # serve() itself fails with EADDRINUSE.
    reuse_address = os.name == "posix" and sys.platform != "cygwin"
    bound: list[socket.socket] = []
    try:
        # Mirror create_server: dedupe the resolution and bind EVERY unique
        # address simultaneously ("localhost" → ::1 + 127.0.0.1); serve()
        # needs all of them, so the probe must not pass on a partial bind.
        for family, _, _, _, addr in set(infos):
            sock = socket.socket(family, socket.SOCK_STREAM)
            bound.append(sock)
            try:
                if reuse_address:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(addr)
            except OSError as exc:
                if exc.errno == errno.EADDRINUSE:
                    return _check_listening(addr)
                return {"ok": False, "code": "network", "detail": f"bind: {exc}"}
    finally:
        for sock in bound:
            sock.close()
    return None


async def test_connection(config: WebSocketConfig) -> dict[str, Any]:
    """Validate the WebSocket channel's local configuration.

    Runs the SSL-material and TCP-bind checks in a worker thread (both are
    blocking) and never starts a server. A success may carry a ``detail``
    when the address is already held by a listening server — the expected
    state while this channel is running.

    Args:
        config: The WebSocket configuration to validate.

    Returns:
        A JSON-serializable envelope with ``ok`` and a ``code`` of ``ok`` /
        ``invalid_credentials`` / ``network``, plus an ``sdk_available`` flag
        echoing whether the ``websockets`` core dependency imports. Any
        ``detail`` is bounded to 200 characters and scrubbed of token values.
    """
    sdk_available = _websockets_available()
    secrets = (config.token, config.token_issue_secret)

    note = ""
    for check in (_check_ssl_material, _check_tcp_bind):
        outcome = await asyncio.to_thread(check, config)
        if outcome is None:
            continue
        detail = scrub_secrets(str(outcome.get("detail", "")), secrets)[:_DETAIL_LIMIT]
        if not outcome.get("ok"):
            return {
                "ok": False,
                "code": outcome["code"],
                "detail": detail,
                "sdk_available": sdk_available,
            }
        note = detail or note

    envelope: dict[str, Any] = {
        "ok": True,
        "code": "ok",
        "sdk_available": sdk_available,
    }
    if note:
        envelope["detail"] = note
    return envelope
