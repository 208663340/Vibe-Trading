"""Connection-test contract for the WebSocket channel.

WebSocket is a *server* channel: there are no remote credentials, so the
standalone probe validates local secret material (SSL cert/key) and address
availability (read-only TCP bind). The contract codes are
``ok | invalid_credentials | network`` plus an ``sdk_available`` flag. All
checks run against loopback only — no real network I/O — and the probe must
never write to the filesystem (no mkdir, no unlink of a stale unix socket).
"""

from __future__ import annotations

import asyncio
import json
import socket
from pathlib import Path
from typing import Any

from src.interfaces.channels.bus.queue import MessageBus
from src.interfaces.channels.websocket import WebSocketChannel
from src.interfaces.channelsui.gateway_services import build_gateway_services

WS_TOKEN = "ws-token-secret-1234567890"


def _free_port() -> int:
    """Return a loopback port the OS just handed out (free at bind time)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _listening_socket(port: int) -> socket.socket:
    """Hold *port* with a real listening socket (accepts TCP handshakes)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", port))
    sock.listen(1)
    return sock


def _bound_socket(port: int) -> socket.socket:
    """Hold *port* bound but NOT listening (connect attempts are refused)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", port))
    return sock


def _make_channel(**overrides: Any) -> WebSocketChannel:
    config: dict[str, Any] = {"host": "127.0.0.1", "port": _free_port()}
    config.update(overrides)
    return WebSocketChannel(config, MessageBus(), gateway=build_gateway_services())


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# Success
# --------------------------------------------------------------------------- #


def test_success_on_free_loopback_port() -> None:
    assert WebSocketChannel.supports_connection_test is True

    result = _run(_make_channel().test_connection())

    assert result["ok"] is True
    assert result["code"] == "ok"
    # websockets is a core dependency and the adapter module imported it.
    assert result["sdk_available"] is True
    assert "detail" not in result


# --------------------------------------------------------------------------- #
# SSL material
# --------------------------------------------------------------------------- #


def test_ssl_certfile_missing_reports_invalid_credentials(tmp_path: Path) -> None:
    channel = _make_channel(
        ssl_certfile=str(tmp_path / "missing.pem"),
        ssl_keyfile=str(tmp_path / "missing.key"),
    )

    result = _run(channel.test_connection())

    assert result["ok"] is False
    assert result["code"] == "invalid_credentials"
    assert result["detail"].startswith("ssl: ")


def test_ssl_garbage_material_reports_invalid_credentials(tmp_path: Path) -> None:
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    cert.write_bytes(b"not a cert")
    key.write_bytes(b"not a key")
    channel = _make_channel(ssl_certfile=str(cert), ssl_keyfile=str(key))

    result = _run(channel.test_connection())

    assert result["ok"] is False
    assert result["code"] == "invalid_credentials"
    assert result["detail"].startswith("ssl: ")


def test_ssl_half_configured_reports_both_or_neither(tmp_path: Path) -> None:
    cert = tmp_path / "cert.pem"
    cert.write_bytes(b"not a cert")
    channel = _make_channel(ssl_certfile=str(cert), ssl_keyfile="")

    result = _run(channel.test_connection())

    assert result["ok"] is False
    assert result["code"] == "invalid_credentials"
    assert result["detail"] == (
        "ssl: ssl_certfile and ssl_keyfile must both be set for WSS, "
        "or both left empty"
    )


def test_ssl_failure_detail_never_carries_token(tmp_path: Path) -> None:
    channel = _make_channel(
        token=WS_TOKEN,
        ssl_certfile=str(tmp_path / "missing.pem"),
        ssl_keyfile=str(tmp_path / "missing.key"),
    )

    result = _run(channel.test_connection())

    assert result["ok"] is False
    assert WS_TOKEN not in json.dumps(result)


# --------------------------------------------------------------------------- #
# TCP bind / EADDRINUSE semantics
# --------------------------------------------------------------------------- #


def test_address_in_use_by_live_listener_reports_ok() -> None:
    """The core hot-config scenario: Test clicked while the channel runs."""
    port = _free_port()
    listener = _listening_socket(port)
    try:
        result = _run(_make_channel(port=port).test_connection())
    finally:
        listener.close()

    assert result["ok"] is True
    assert result["code"] == "ok"
    assert "already in use" in result["detail"]


def test_address_in_use_without_listener_reports_network() -> None:
    port = _free_port()
    holder = _bound_socket(port)
    try:
        result = _run(_make_channel(port=port).test_connection())
    finally:
        holder.close()

    assert result["ok"] is False
    assert result["code"] == "network"
    assert "not accepting connections" in result["detail"]


def test_unresolvable_host_reports_network() -> None:
    channel = _make_channel(host="no-such-host.invalid")

    result = _run(channel.test_connection())

    assert result["ok"] is False
    assert result["code"] == "network"
    assert result["detail"].startswith("bind: ")


# --------------------------------------------------------------------------- #
# Unix socket: TCP bind skipped, no filesystem side effects
# --------------------------------------------------------------------------- #


def test_unix_socket_path_skips_tcp_bind_entirely(tmp_path: Path) -> None:
    """host/port are ignored by start() when a unix socket is configured."""
    port = _free_port()
    listener = _listening_socket(port)
    try:
        channel = _make_channel(
            unix_socket_path=str(tmp_path / "ws.sock"), host="127.0.0.1", port=port
        )
        result = _run(channel.test_connection())
    finally:
        listener.close()

    # The occupied port must not even be probed: ok with no bind note.
    assert result["ok"] is True
    assert result["code"] == "ok"
    assert "detail" not in result


def test_probe_creates_nothing_on_the_filesystem(tmp_path: Path) -> None:
    nested = tmp_path / "nested"
    socket_path = nested / "ws.sock"
    channel = _make_channel(unix_socket_path=str(socket_path))

    result = _run(channel.test_connection())

    assert result["ok"] is True
    assert not nested.exists()
    assert not socket_path.exists()


def test_result_envelope_is_json_serializable() -> None:
    result = _run(_make_channel().test_connection())

    assert json.loads(json.dumps(result)) == result
