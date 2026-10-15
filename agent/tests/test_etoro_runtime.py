"""Tests for eToro connector runtime status surfaced through /live/status."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import src.interfaces.api_server as api_server

pytestmark = pytest.mark.unit


def _client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path), raising=False)
    monkeypatch.setattr(api_server, "_runner_tasks", {}, raising=False)
    monkeypatch.setattr(api_server, "_runner_factory", None, raising=False)
    api_server._connector_verify_cache.clear()
    return TestClient(api_server.app, client=("127.0.0.1", 50001))


def test_live_status_includes_etoro_sdk_profile(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    response = client.get("/live/status")
    assert response.status_code == 200
    brokers = {b["auth"]["broker"]: b for b in response.json()["brokers"]}
    assert "etoro" in brokers
    etoro = brokers["etoro"]
    assert etoro["auth"]["transport"] == "broker_sdk"
    assert etoro["auth"]["is_live_broker"] is False
    assert etoro["mandate"] is None


def test_etoro_missing_credentials_maps_not_configured(
    tmp_path: Path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        api_server,
        "_check_connector_status",
        lambda profile_id, force=False: {
            "status": "error",
            "configured": False,
            "connection_state": "not_configured",
            "error_code": "credentials_missing",
            "error": "eToro connector not configured: missing api_key, user_key.",
            "connector": "etoro",
            "transport": "broker_sdk",
            "profile_id": "etoro-live-sdk-readonly",
        },
    )
    response = client.get("/live/status", params={"broker": "etoro"})
    assert response.status_code == 200
    auth = response.json()["brokers"][0]["auth"]
    assert auth["broker"] == "etoro"
    assert auth["connection_state"] == "not_configured"
    assert auth["configured"] is False


def test_etoro_valid_check_maps_connected(tmp_path: Path, monkeypatch) -> None:
    client = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        api_server,
        "_check_connector_status",
        lambda profile_id, force=False: {
            "status": "ok",
            "configured": True,
            "connection_state": "connected",
            "error": None,
            "connector": "etoro",
            "transport": "broker_sdk",
            "profile_id": "etoro-live-sdk-readonly",
            "sdk": {"package": "requests", "installed": True},
            "paper_guard": "path_separated_key_bound",
            "credential_source": "runtime_file",
            "last_checked_at": "2026-08-09T12:00:00+00:00",
        },
    )
    response = client.get("/live/status", params={"broker": "etoro"})
    assert response.status_code == 200
    auth = response.json()["brokers"][0]["auth"]
    assert auth["connection_state"] == "connected"
    assert auth["profile_id"] == "etoro-live-sdk-readonly"
    assert auth["sdk_installed"] is True
    assert auth["environment_identity"] == "path_separated_key_bound"
    assert auth["capabilities"] == [
        "account.read",
        "positions.read",
        "orders.read",
        "quotes.read",
        "history.read",
    ]
    assert auth["readonly"] is True


def test_live_status_propagates_only_sanitized_etoro_metadata(
    tmp_path: Path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    sentinels = {
        "api_key": "SENTINEL_API_KEY",
        "user_key": "SENTINEL_USER_KEY",
    }
    monkeypatch.setattr(
        api_server,
        "_check_connector_status",
        lambda profile_id, force=False: {
            "status": "ok",
            "profile_id": profile_id,
            "configured": True,
            "credential_source": "runtime_file",
            "sdk_installed": True,
            "sdk": {"package": "requests", "installed": True},
            "environment_identity": "path_separated_key_bound",
            "paper_guard": "path_separated_key_bound",
            "capabilities": ["account.read"],
            "readonly": True,
            "last_checked_at": "2026-08-09T12:00:00+00:00",
            "error_code": None,
            "connection_state": "connected",
            "config": sentinels,
            "error": f"raw failure: {sentinels['user_key']}",
        },
    )
    response = client.get("/live/status", params={"broker": "etoro"})
    assert response.status_code == 200
    auth = response.json()["brokers"][0]["auth"]
    assert "config" not in auth
    assert all(secret not in response.text for secret in sentinels.values())


def test_verify_endpoint_for_etoro_readonly_profile(
    tmp_path: Path, monkeypatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        api_server,
        "_check_connector_status",
        lambda profile_id, force=False: {
            "status": "ok",
            "configured": True,
            "connection_state": "connected",
            "error": None,
            "connector": "etoro",
            "transport": "broker_sdk",
            "profile_id": profile_id,
        },
    )
    response = client.post("/live/connectors/etoro-live-sdk-readonly/verify")
    assert response.status_code == 200
    assert response.json()["connection_state"] == "connected"
