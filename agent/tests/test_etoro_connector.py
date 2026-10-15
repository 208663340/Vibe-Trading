"""Tests for the eToro Public API connector."""

from __future__ import annotations

import json
from typing import Any

import pytest

from src.markets.live import registry
from src.markets.live.classification import ToolClass, classify_tool
from src.markets.trading import profiles, service
from src.markets.trading.connectors.etoro import sdk as etoro_sdk
from src.markets.trading.connectors.etoro.client import (
    EtoroClient,
    EtoroConfig,
    EtoroConfigError,
    build_config,
    aggregate_portfolio_path,
    copy_root,
    execution_root,
    execution_v1_root,
    info_root,
    positions_root,
    set_client_factory,
)
from src.markets.trading.connectors.etoro.classification import ETORO_TOOL_CLASS
from src.markets.trading.connectors.etoro.profiles import ETORO_EXTENDED_CAPABILITIES, ETORO_PAPER_TRADE_CAPABILITIES

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _reset_etoro_client_factory() -> None:
    from src.markets.trading.connectors.etoro.instruments import reset_instrument_type_cache

    reset_instrument_type_cache()
    yield
    reset_instrument_type_cache()
    set_client_factory(None)


class _FakeResponse:
    def __init__(self, status_code: int, payload: Any = None, headers: dict[str, str] | None = None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}
        self.content = b"{}" if payload is not None else b""

    def json(self) -> Any:
        return self._payload


def _transport_factory(responses: dict[tuple[str, str], _FakeResponse]):
    def _transport(method: str, url: str, **kwargs: Any) -> _FakeResponse:
        key = (method.upper(), url.split("public-api.etoro.com", 1)[-1])
        if key not in responses:
            raise AssertionError(f"unexpected request {key}")
        return responses[key]

    return _transport


def test_etoro_profiles_registered() -> None:
    ids = {profile.id for profile in profiles.list_profiles()}
    assert {
        "etoro-paper-sdk",
        "etoro-paper-trade",
        "etoro-live-sdk-readonly",
        "etoro-live-trade",
    } <= ids

    paper = profiles.profile_by_id("etoro-paper-trade")
    live = profiles.profile_by_id("etoro-live-trade")
    assert paper.connector == "etoro"
    assert live.connector == "etoro"
    assert paper.environment == "paper"
    assert live.environment == "live"
    assert "orders.place" in paper.capabilities
    assert "orders.cancel" in paper.capabilities
    assert "orders.place.requires_mandate" in live.capabilities


def test_trade_profile_lists_capabilities_by_environment() -> None:
    paper = profiles.profile_by_id("etoro-paper-trade")
    live = profiles.profile_by_id("etoro-live-trade")
    assert set(ETORO_PAPER_TRADE_CAPABILITIES) <= set(paper.capabilities)
    assert "copy.start" not in paper.capabilities
    assert "orders.place" in paper.capabilities
    assert "copy.start" in live.capabilities
    assert "orders.place.requires_mandate" in live.capabilities


def test_demo_and_real_paths_separated() -> None:
    paper = EtoroConfig(profile="paper", api_key="k", user_key="u")
    live = EtoroConfig(profile="live", api_key="k", user_key="u")
    assert execution_root(paper) == "/api/v2/trading/execution/demo"
    assert execution_root(live) == "/api/v2/trading/execution"
    assert execution_v1_root(paper).endswith("/demo")
    assert "/demo" not in execution_v1_root(live)
    assert info_root(paper).endswith("/demo")
    assert aggregate_portfolio_path(paper).endswith("/demo/aggregate-portfolio")
    assert aggregate_portfolio_path(live) == "/api/v1/trading/info/aggregate-portfolio"
    assert copy_root(live) == "/api/v2/trading/copy"
    assert positions_root(paper).endswith("/demo")
    assert paper.api_key == live.api_key


def test_copy_root_refuses_a_paper_config() -> None:
    """Copy has no demo path, so a paper config must get a refusal, not the real one.

    Every other root encodes paper/live in the path. If ``copy_root`` simply
    returned the real route for a paper config, the only thing keeping demo
    credentials off the live copy endpoint would be each call site remembering
    to check first.
    """
    paper = EtoroConfig(profile="paper", api_key="k", user_key="u")
    with pytest.raises(EtoroConfigError) as excinfo:
        copy_root(paper)
    assert "demo (paper)" in str(excinfo.value)
    assert copy_root(EtoroConfig(profile="live-readonly", api_key="k", user_key="u"))
    assert copy_root(EtoroConfig(profile="live", api_key="k", user_key="u"))


def test_shared_credentials_across_paper_and_live_profiles(monkeypatch, tmp_path) -> None:
    cfg_path = tmp_path / "etoro.json"
    cfg_path.write_text(
        json.dumps({"api_key": "shared-key", "user_key": "shared-user", "profile": "paper"}),
        encoding="utf-8",
    )
    monkeypatch.setattr("src.markets.trading.connectors.etoro.client.config_path", lambda: cfg_path)
    paper_cfg = build_config({"profile": "paper"}, None)
    live_cfg = build_config({"profile": "live"}, None)
    assert paper_cfg.api_key == "shared-key"
    assert live_cfg.api_key == "shared-key"
    assert live_cfg.user_key == "shared-user"
    assert paper_cfg.is_paper is True
    assert live_cfg.is_paper is False


def test_every_request_has_unique_request_id() -> None:
    seen: list[str] = []
    cfg = EtoroConfig(profile="paper", api_key="k", user_key="u")

    def _transport(method: str, url: str, **kwargs: Any) -> _FakeResponse:
        seen.append(kwargs["headers"]["x-request-id"])
        return _FakeResponse(200, {})

    client = EtoroClient(cfg, transport=_transport)
    client.request("GET", "/api/v1/market-data/search", params={"search": "AAPL"}, allow_retry=True)
    client.request("GET", "/api/v1/market-data/search", params={"search": "MSFT"}, allow_retry=True)
    assert len(seen) == 2
    assert seen[0] != seen[1]


def test_credentials_redacted_in_public_config() -> None:
    cfg = EtoroConfig(profile="paper", api_key="secret-key", user_key="secret-user")
    from src.markets.trading.connectors.etoro.client import public_config

    payload = public_config(cfg)
    assert "secret-key" not in json.dumps(payload)
    assert payload["api_key_configured"] is True


def test_check_status_not_configured() -> None:
    cfg = EtoroConfig(profile="live-readonly", api_key="", user_key="")
    result = etoro_sdk.check_status(cfg)
    assert result["status"] == "error"
    assert result["configured"] is False
    assert result["connection_state"] == "not_configured"
    assert result["error_code"] == "credentials_missing"


def test_check_status_partial_credentials() -> None:
    cfg = EtoroConfig(profile="live-readonly", api_key="k", user_key="")
    result = etoro_sdk.check_status(cfg)
    assert result["error_code"] == "credentials_partial"
    assert result["connection_state"] == "not_configured"


def test_check_status_connected(monkeypatch) -> None:
    cfg = EtoroConfig(profile="live-readonly", api_key="k", user_key="u")

    def _transport(method: str, url: str, **kwargs: Any) -> _FakeResponse:
        if url.endswith("/api/v1/me"):
            return _FakeResponse(200, {"scopes": ["etoro-public:trade.real:read"], "realCid": 1})
        if "/aggregate-portfolio" in url:
            return _FakeResponse(200, {"accountTotals": {"accountCurrentPnl": 1.0}})
        if url.endswith("/portfolio"):
            return _FakeResponse(200, {"clientPortfolio": {"credit": 1.0}})
        raise AssertionError(f"unexpected {method} {url}")

    set_client_factory(lambda c: EtoroClient(cfg, transport=_transport))
    result = etoro_sdk.check_status(cfg)
    assert result["status"] == "ok"
    assert result["configured"] is True
    assert result["connection_state"] == "connected"
    assert result["error_code"] is None
    assert result["last_checked_at"]
    assert "etoro-public:trade.real:read" in result["account"]["scopes"]


def test_check_status_auth_failure_keeps_configured(monkeypatch) -> None:
    cfg = EtoroConfig(profile="live-readonly", api_key="k", user_key="u")

    def _transport(method: str, url: str, **kwargs: Any) -> _FakeResponse:
        return _FakeResponse(401, {"error": "unauthorized"})

    set_client_factory(lambda c: EtoroClient(cfg, transport=_transport))
    result = etoro_sdk.check_status(cfg)
    assert result["status"] == "error"
    assert result["configured"] is True
    assert result["connection_state"] == "error"
    assert result["error_code"] == "authentication_failed"


def test_get_account_snapshot_uses_aggregate_portfolio_for_pnl(monkeypatch) -> None:
    cfg = EtoroConfig(profile="live", api_key="k", user_key="u")
    captured_urls: list[str] = []

    def _transport(method: str, url: str, **kwargs: Any) -> _FakeResponse:
        captured_urls.append(url)
        if "/portfolio" in url and "aggregate" not in url:
            return _FakeResponse(200, {"clientPortfolio": {"credit": 1.25, "positions": []}})
        if "/aggregate-portfolio" in url:
            return _FakeResponse(
                200,
                {
                    "accountCurrency": "USD",
                    "accountTotals": {
                        "accountCurrentPnl": -26.94,
                        "accountTotalValue": 615.48,
                        "accountBalance": 1.25,
                    },
                },
            )
        raise AssertionError(f"unexpected {method} {url}")

    set_client_factory(lambda c: EtoroClient(cfg, transport=_transport))
    result = etoro_sdk.get_account_snapshot(cfg)
    assert result["status"] == "ok"
    pnl = result["account"]["pnl"]
    assert pnl["source"] == "aggregate-portfolio"
    assert pnl["account_current_pnl"] == -26.94
    assert "pnl_error" not in result["account"]
    assert any("/aggregate-portfolio" in url for url in captured_urls)
    assert not any(url.endswith("/pnl") for url in captured_urls)


def test_place_order_uses_demo_execution_path(monkeypatch) -> None:
    cfg = EtoroConfig(profile="paper", api_key="k", user_key="u", readonly=False)
    captured: dict[str, Any] = {}

    def _transport(method: str, url: str, **kwargs: Any) -> _FakeResponse:
        captured["method"] = method
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        return _FakeResponse(200, {"orderID": 42})

    monkeypatch.setattr("src.markets.trading.connectors.etoro.trading.resolve_instrument_id", lambda symbol, c: 1001)
    client = EtoroClient(cfg, transport=_transport)
    set_client_factory(lambda c: client)

    result = etoro_sdk.place_order(
        cfg,
        symbol="AAPL",
        side="buy",
        notional=100.0,
        request_id="fixed-request-id",
    )
    assert result["status"] == "ok"
    assert "/execution/demo/orders" in captured["url"]
    assert captured["json"]["symbol"] == "AAPL"
    assert captured["method"] == "POST"


def test_limit_place_order_sets_trigger_rate(monkeypatch) -> None:
    cfg = EtoroConfig(profile="paper", api_key="k", user_key="u")
    captured: dict[str, Any] = {}

    def _transport(method: str, url: str, **kwargs: Any) -> _FakeResponse:
        captured["json"] = kwargs.get("json")
        return _FakeResponse(200, {"orderID": 7})

    monkeypatch.setattr("src.markets.trading.connectors.etoro.trading.resolve_instrument_id", lambda symbol, c: 100000)
    set_client_factory(lambda c: EtoroClient(cfg, transport=_transport))

    result = etoro_sdk.place_order(
        cfg,
        symbol="BTC",
        side="buy",
        notional=50.0,
        order_type="limit",
        limit_price=10000.0,
    )
    assert result["status"] == "ok"
    assert captured["json"]["orderType"] == "mit"
    assert captured["json"]["TriggerRate"] == 10000.0


def test_limit_without_limit_price_errors_before_sending(monkeypatch) -> None:
    # A limit order with no limit_price would go out as an MIT with no
    # TriggerRate and rest untriggered while looking accepted.
    cfg = EtoroConfig(profile="paper", api_key="k", user_key="u")
    sent: list[str] = []

    def _transport(method: str, url: str, **kwargs: Any) -> _FakeResponse:
        sent.append(url)
        return _FakeResponse(200, {"orderID": 7})

    monkeypatch.setattr("src.markets.trading.connectors.etoro.trading.resolve_instrument_id", lambda symbol, c: 100000)
    set_client_factory(lambda c: EtoroClient(cfg, transport=_transport))

    result = etoro_sdk.place_order(
        cfg,
        symbol="BTC",
        side="buy",
        notional=50.0,
        order_type="limit",
    )
    assert result["status"] == "error"
    assert "limit_price" in result["error"]
    assert sent == []


def test_cancel_order_uses_demo_delete_path(monkeypatch) -> None:
    cfg = EtoroConfig(profile="paper", api_key="k", user_key="u")
    captured: dict[str, Any] = {}

    def _transport(method: str, url: str, **kwargs: Any) -> _FakeResponse:
        captured["method"] = method
        captured["url"] = url
        return _FakeResponse(200, {})

    set_client_factory(lambda c: EtoroClient(cfg, transport=_transport))
    result = etoro_sdk.cancel_order(cfg, "12345", symbol="BTC")
    assert result["status"] == "ok"
    assert captured["method"] == "DELETE"
    assert captured["url"].endswith("/orders/12345")
    assert "/execution/demo/" in captured["url"]


def test_classification_registered() -> None:
    curated = registry._BROKER_CURATED_MAPS["etoro"]
    assert classify_tool("get_positions", None, curated) is ToolClass.READ
    assert classify_tool("place_order", None, curated) is ToolClass.WRITE
    assert classify_tool("cancel_order", None, curated) is ToolClass.WRITE
    assert classify_tool("unknown_etoro_op", None, curated) is ToolClass.UNKNOWN
    assert ETORO_TOOL_CLASS["copy_close"] is ToolClass.WRITE
    assert ETORO_TOOL_CLASS["get_instrument_metadata"] is ToolClass.READ
    assert ETORO_TOOL_CLASS["get_instrument_types"] is ToolClass.READ
    assert ETORO_TOOL_CLASS["list_instruments_by_type"] is ToolClass.READ


def test_resolve_btc_uses_internal_symbol_full(monkeypatch) -> None:
    cfg = EtoroConfig(profile="paper", api_key="k", user_key="u")
    captured_params: list[dict[str, Any]] = []

    def _transport(method: str, url: str, **kwargs: Any) -> _FakeResponse:
        if "/market-data/search" in url:
            captured_params.append(dict(kwargs.get("params") or {}))
            if kwargs.get("params", {}).get("internalSymbolFull") == "BTC":
                return _FakeResponse(
                    200,
                    {
                        "items": [
                            {"instrumentId": 100134, "internalSymbolFull": "BTCA"},
                            {
                                "instrumentId": 100000,
                                "internalSymbolFull": "BTC",
                                "internalInstrumentDisplayName": "Bitcoin",
                            },
                        ],
                    },
                )
        raise AssertionError(f"unexpected request {method} {url}")

    set_client_factory(lambda c: EtoroClient(cfg, transport=_transport))
    from src.markets.trading.connectors.etoro.instruments import resolve_instrument_id

    assert resolve_instrument_id("BTC", cfg) == 100000
    assert resolve_instrument_id("Bitcoin", cfg) == 100000
    assert any(p.get("internalSymbolFull") == "BTC" for p in captured_params)


def test_fuzzy_search_filters_sentinel_ids(monkeypatch) -> None:
    cfg = EtoroConfig(profile="paper", api_key="k", user_key="u")

    def _transport(method: str, url: str, **kwargs: Any) -> _FakeResponse:
        if kwargs.get("params", {}).get("search"):
            return _FakeResponse(
                200,
                {"items": [{"instrumentId": -100000}, {"instrumentId": 100000, "internalSymbolFull": "BTC"}]},
            )
        return _FakeResponse(200, {"items": []})

    set_client_factory(lambda c: EtoroClient(cfg, transport=_transport))
    from src.markets.trading.connectors.etoro.instruments import search_instruments

    result = search_instruments("bitcoin", cfg, limit=5, mode="discover")
    assert result["status"] == "ok"
    assert result["instruments"][0]["instrument_id"] == 100000


def test_crypto_query_browses_instrument_type_catalog(monkeypatch) -> None:
    cfg = EtoroConfig(profile="paper", api_key="k", user_key="u")
    captured: list[tuple[str, dict[str, Any]]] = []

    def _transport(method: str, url: str, **kwargs: Any) -> _FakeResponse:
        captured.append((url, dict(kwargs.get("params") or {})))
        if "/instrument-types" in url:
            return _FakeResponse(
                200,
                {"instrumentTypes": [{"instrumentTypeID": 10, "instrumentTypeDescription": "Crypto"}]},
            )
        if "/market-data/search" in url:
            return _FakeResponse(200, {"items": []})
        if "/market-data/instruments" in url and kwargs.get("params", {}).get("instrumentTypeIds") == 10:
            return _FakeResponse(
                200,
                {
                    "instrumentDisplayDatas": [
                        {
                            "instrumentID": 100000,
                            "symbolFull": "BTC",
                            "instrumentDisplayName": "Bitcoin",
                            "instrumentTypeID": 10,
                        },
                        {
                            "instrumentID": 5,
                            "symbolFull": "00001.HK",
                            "instrumentDisplayName": "CK Hutchison",
                            "instrumentTypeID": 5,
                        },
                    ],
                },
            )
        raise AssertionError(f"unexpected request {method} {url} {kwargs.get('params')}")

    set_client_factory(lambda c: EtoroClient(cfg, transport=_transport))
    from src.markets.trading.connectors.etoro.instruments import search_instruments

    result = search_instruments("crypto", cfg, limit=5)
    assert result["status"] == "ok"
    assert result["mode"] == "type"
    assert result["instrument_type_id"] == 10
    assert result["instruments"][0]["symbol"] == "BTC"
    assert all(row.get("instrument_type_id") == 10 for row in result["instruments"])
    assert any(params.get("instrumentTypeIds") == 10 for _, params in captured)


def test_stocks_query_uses_dynamic_type_catalog(monkeypatch) -> None:
    cfg = EtoroConfig(profile="paper", api_key="k", user_key="u")

    def _transport(method: str, url: str, **kwargs: Any) -> _FakeResponse:
        if "/instrument-types" in url:
            return _FakeResponse(
                200,
                {
                    "instrumentTypes": [
                        {"instrumentTypeID": 5, "instrumentTypeDescription": "Stocks"},
                        {"instrumentTypeID": 10, "instrumentTypeDescription": "Crypto"},
                    ],
                },
            )
        if "/market-data/search" in url:
            return _FakeResponse(200, {"items": []})
        if kwargs.get("params", {}).get("instrumentTypeIds") == 5:
            return _FakeResponse(
                200,
                {
                    "instrumentDisplayDatas": [
                        {
                            "instrumentID": 1111,
                            "symbolFull": "AAPL",
                            "instrumentTypeID": 5,
                        },
                    ],
                },
            )
        raise AssertionError(f"unexpected request {method} {url}")

    set_client_factory(lambda c: EtoroClient(cfg, transport=_transport))
    from src.markets.trading.connectors.etoro.instruments import search_instruments

    result = search_instruments("equities", cfg, limit=5)
    assert result["status"] == "ok"
    assert result["instrument_type_id"] == 5
    assert result["instruments"][0]["symbol"] == "AAPL"


def test_get_instrument_types_reads_catalog(monkeypatch) -> None:
    cfg = EtoroConfig(profile="paper", api_key="k", user_key="u")

    def _transport(method: str, url: str, **kwargs: Any) -> _FakeResponse:
        if "/instrument-types" in url:
            return _FakeResponse(
                200,
                {
                    "instrumentTypes": [
                        {"instrumentTypeID": 1, "instrumentTypeDescription": "Forex"},
                        {"instrumentTypeID": 10, "instrumentTypeDescription": "Crypto"},
                    ],
                },
            )
        raise AssertionError(f"unexpected request {method} {url}")

    set_client_factory(lambda c: EtoroClient(cfg, transport=_transport))
    from src.markets.trading.connectors.etoro.instruments import get_instrument_types

    result = get_instrument_types(cfg)
    assert result["status"] == "ok"
    assert {row["instrument_type_id"] for row in result["instrument_types"]} == {1, 10}
    assert result["instrument_types"][0]["label"] == "forex"


def test_crypto_browse_can_attach_flat_market_data_rates(monkeypatch) -> None:
    cfg = EtoroConfig(profile="live-readonly", api_key="k", user_key="u")
    captured_urls: list[str] = []

    def _transport(method: str, url: str, **kwargs: Any) -> _FakeResponse:
        captured_urls.append(url)
        if "/instrument-types" in url:
            return _FakeResponse(
                200,
                {"instrumentTypes": [{"instrumentTypeID": 10, "instrumentTypeDescription": "Crypto"}]},
            )
        if "/market-data/search" in url:
            return _FakeResponse(200, {"items": []})
        if "/market-data/instruments" in url and "rates" not in url:
            return _FakeResponse(
                200,
                {
                    "instrumentDisplayDatas": [
                        {
                            "instrumentID": 100000,
                            "symbolFull": "BTC",
                            "instrumentTypeID": 10,
                        },
                    ],
                },
            )
        if "/market-data/instruments/rates" in url:
            return _FakeResponse(
                200,
                {"rates": [{"instrumentID": 100000, "bid": 1.0, "ask": 2.0, "lastExecution": 1.5}]},
            )
        raise AssertionError(f"unexpected request {method} {url}")

    set_client_factory(lambda c: EtoroClient(cfg, transport=_transport))
    from src.markets.trading.connectors.etoro.instruments import search_instruments

    result = search_instruments("crypto", cfg, limit=5, include_rates=True)
    assert result["instruments"][0]["quote"] == {"bid": 1.0, "ask": 2.0, "last": 1.5}
    assert any("/market-data/instruments/rates" in url for url in captured_urls)
    assert not any(url.endswith("/api/v1/instruments") for url in captured_urls)


def test_get_quote_uses_flat_rates_on_readonly_profile(monkeypatch) -> None:
    cfg = EtoroConfig(profile="live-readonly", api_key="k", user_key="u")

    def _transport(method: str, url: str, **kwargs: Any) -> _FakeResponse:
        if "/market-data/search" in url:
            return _FakeResponse(200, {"items": [{"instrumentId": 100000, "internalSymbolFull": "BTC"}]})
        if "/market-data/instruments/rates" in url:
            return _FakeResponse(200, {"rates": [{"instrumentID": 100000, "bid": 1.0, "ask": 2.0}]})
        raise AssertionError(f"unexpected request {method} {url}")

    set_client_factory(lambda c: EtoroClient(cfg, transport=_transport))
    result = etoro_sdk.get_quote("BTC", config=cfg)
    assert result["status"] == "ok"
    assert result["quote"]["bid"] == 1.0

    service_result = service.get_quote("BTC", "etoro-live-sdk-readonly", api_key="k", user_key="u")
    assert service_result["status"] == "ok"
    assert service_result["profile_id"] == "etoro-live-sdk-readonly"
    assert service_result["quote"]["ask"] == 2.0


def test_get_open_orders_reads_portfolio_orders(monkeypatch) -> None:
    cfg = EtoroConfig(profile="paper", api_key="k", user_key="u")

    def _transport(method: str, url: str, **kwargs: Any) -> _FakeResponse:
        assert method == "GET"
        assert info_root(cfg) + "/portfolio" in url
        return _FakeResponse(
            200,
            {
                "clientPortfolio": {
                    "ordersForOpen": [{"orderID": 99, "instrumentID": 100000}],
                    "ordersForClose": [],
                }
            },
        )

    set_client_factory(lambda c: EtoroClient(cfg, transport=_transport))
    result = etoro_sdk.get_open_orders(cfg)
    assert result["status"] == "ok"
    assert result["orders"][0]["order_id"] == 99
    assert result["orders"][0]["order_kind"] == "ordersForOpen"


def test_get_open_orders_trade_history_path(monkeypatch) -> None:
    cfg = EtoroConfig(profile="paper", api_key="k", user_key="u")
    captured_urls: list[str] = []

    def _transport(method: str, url: str, **kwargs: Any) -> _FakeResponse:
        captured_urls.append(url)
        if "/portfolio" in url:
            return _FakeResponse(200, {"clientPortfolio": {"ordersForOpen": []}})
        if "/trade/demo/history" in url:
            return _FakeResponse(200, {"items": []})
        raise AssertionError(f"unexpected {method} {url}")

    set_client_factory(lambda c: EtoroClient(cfg, transport=_transport))
    result = etoro_sdk.get_open_orders(cfg, include_executions=True)
    assert result["status"] == "ok"
    assert "history" in result
    assert any("/trade/demo/history" in u for u in captured_urls)


def test_get_quote_reads_rates(monkeypatch) -> None:
    cfg = EtoroConfig(profile="paper", api_key="k", user_key="u")

    def _transport(method: str, url: str, **kwargs: Any) -> _FakeResponse:
        if "/market-data/search" in url:
            return _FakeResponse(200, {"items": [{"instrumentId": 100000, "internalSymbolFull": "BTC"}]})
        if "/instruments/rates" in url:
            return _FakeResponse(200, {"items": [{"instrumentID": 100000, "bid": 1.0, "ask": 2.0}]})
        raise AssertionError(f"unexpected {method} {url}")

    set_client_factory(lambda c: EtoroClient(cfg, transport=_transport))
    result = etoro_sdk.get_quote("BTC", config=cfg)
    assert result["status"] == "ok"
    assert result["quote"]["bid"] == 1.0
    assert result["quote"]["ask"] == 2.0


def test_get_historical_bars_requests_candles_path(monkeypatch) -> None:
    cfg = EtoroConfig(profile="paper", api_key="k", user_key="u")
    captured_url = ""

    def _transport(method: str, url: str, **kwargs: Any) -> _FakeResponse:
        nonlocal captured_url
        captured_url = url
        if "/market-data/search" in url:
            return _FakeResponse(200, {"items": [{"instrumentId": 100000, "internalSymbolFull": "BTC"}]})
        return _FakeResponse(
            200,
            {
                "candles": [
                    {
                        "instrumentId": 100000,
                        "candles": [{"fromDate": "2026-08-05T00:00:00Z", "open": 1.0, "close": 2.0}],
                    }
                ]
            },
        )

    set_client_factory(lambda c: EtoroClient(cfg, transport=_transport))
    result = etoro_sdk.get_historical_bars("BTC", config=cfg, period="1d", limit=5)
    assert result["status"] == "ok"
    assert "/history/candles/desc/OneDay/5" in captured_url
    assert result["bars"][0]["open"] == 1.0


def test_close_position_uses_market_close_path(monkeypatch) -> None:
    cfg = EtoroConfig(profile="paper", api_key="k", user_key="u")
    captured: dict[str, Any] = {}

    def _transport(method: str, url: str, **kwargs: Any) -> _FakeResponse:
        captured["method"] = method
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        return _FakeResponse(200, {"orderID": 55})

    set_client_factory(lambda c: EtoroClient(cfg, transport=_transport))
    result = etoro_sdk.close_position(cfg, position_id=10, instrument_id=100000)
    assert result["status"] == "ok"
    assert captured["method"] == "POST"
    assert "/market-close-orders/positions/10" in captured["url"]
    assert "/execution/demo/" in captured["url"]
    assert captured["json"] == {"InstrumentId": 100000}


def test_partial_close_uses_required_wire_contract(monkeypatch) -> None:
    cfg = EtoroConfig(profile="paper", api_key="k", user_key="u")
    captured: dict[str, Any] = {}

    def _transport(method: str, url: str, **kwargs: Any) -> _FakeResponse:
        captured["json"] = kwargs.get("json")
        return _FakeResponse(200, {"orderForClose": {"orderID": 56}})

    set_client_factory(lambda c: EtoroClient(cfg, transport=_transport))

    result = etoro_sdk.close_position(
        cfg,
        position_id=10,
        instrument_id=100000,
        units_to_close=2.5,
    )

    assert result["status"] == "ok"
    assert captured["json"] == {
        "InstrumentId": 100000,
        "UnitsToDeduct": 2.5,
    }


def test_close_position_refuses_missing_instrument_id() -> None:
    cfg = EtoroConfig(profile="paper", api_key="k", user_key="u")

    result = etoro_sdk.close_position(cfg, position_id=10)

    assert result["status"] == "error"
    assert "instrument_id is required" in result["error"]


def test_cancel_close_order_uses_delete_path(monkeypatch) -> None:
    cfg = EtoroConfig(profile="paper", api_key="k", user_key="u")
    captured: dict[str, Any] = {}

    def _transport(method: str, url: str, **kwargs: Any) -> _FakeResponse:
        captured["method"] = method
        captured["url"] = url
        return _FakeResponse(200, {})

    set_client_factory(lambda c: EtoroClient(cfg, transport=_transport))
    result = etoro_sdk.cancel_close_order(cfg, order_id="77")
    assert result["status"] == "ok"
    assert captured["method"] == "DELETE"
    assert captured["url"].endswith("/market-close-orders/77")


def test_edit_position_stops_patches_position(monkeypatch) -> None:
    cfg = EtoroConfig(profile="paper", api_key="k", user_key="u")
    captured: dict[str, Any] = {}

    def _transport(method: str, url: str, **kwargs: Any) -> _FakeResponse:
        captured["method"] = method
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        return _FakeResponse(200, {})

    set_client_factory(lambda c: EtoroClient(cfg, transport=_transport))
    result = etoro_sdk.edit_position_stops(
        cfg,
        position_id=5,
        stop_loss=90.0,
        take_profit=110.0,
        trailing_stop_loss=True,
    )
    assert result["status"] == "ok"
    assert captured["method"] == "PATCH"
    assert "/positions/5" in captured["url"]
    assert captured["json"]["stopLossRate"] == 90.0
    assert captured["json"]["takeProfitRate"] == 110.0
    assert captured["json"]["stopLossType"] == "trailing"
    assert "isTslEnabled" not in captured["json"]


def test_edit_position_stops_supports_clear_contract(monkeypatch) -> None:
    cfg = EtoroConfig(profile="paper", api_key="k", user_key="u")
    captured: dict[str, Any] = {}

    def _transport(method: str, url: str, **kwargs: Any) -> _FakeResponse:
        captured["json"] = kwargs.get("json")
        return _FakeResponse(202, {"operationId": "op-1"})

    set_client_factory(lambda c: EtoroClient(cfg, transport=_transport))

    result = etoro_sdk.edit_position_stops(
        cfg,
        position_id=5,
        clear_stop_loss=True,
        clear_take_profit=True,
    )

    assert result["status"] == "ok"
    assert captured["json"] == {
        "clearStopLoss": True,
        "clearTakeProfit": True,
    }


def test_copy_rejected_on_paper_account() -> None:
    cfg = EtoroConfig(profile="paper", api_key="k", user_key="u")
    from src.markets.trading.connectors.etoro.copy_trading import COPY_TRADING_PAPER_UNSUPPORTED

    result = etoro_sdk.copy_precheck(cfg, parent_cid=123, amount=100.0)
    assert result["status"] == "error"
    assert result["error_code"] == "copy_unavailable_on_paper"
    assert COPY_TRADING_PAPER_UNSUPPORTED in result["error"]


def test_copy_precheck_posts_eligibility(monkeypatch) -> None:
    cfg = EtoroConfig(profile="live", api_key="k", user_key="u")
    captured: dict[str, Any] = {}

    def _transport(method: str, url: str, **kwargs: Any) -> _FakeResponse:
        captured["method"] = method
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        return _FakeResponse(200, {"eligible": True})

    set_client_factory(lambda c: EtoroClient(cfg, transport=_transport))
    result = etoro_sdk.copy_precheck(cfg, parent_cid=123, amount=100.0)
    assert result["status"] == "ok"
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/eligibility")
    assert captured["json"]["parentCID"] == 123


def test_copy_start_posts_copy_root(monkeypatch) -> None:
    cfg = EtoroConfig(profile="live", api_key="k", user_key="u")
    captured: dict[str, Any] = {}

    def _transport(method: str, url: str, **kwargs: Any) -> _FakeResponse:
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        return _FakeResponse(200, {"token": "ack-token"})

    set_client_factory(lambda c: EtoroClient(cfg, transport=_transport))
    result = etoro_sdk.copy_start_or_adjust(
        cfg,
        parent_cid=123,
        amount=200.0,
        reference_id="ref-1",
    )
    assert result["status"] == "ok"
    assert result["reference_id"] == "ref-1"
    assert result["token"] == "ack-token"
    assert copy_root(cfg) in captured["url"]
    assert captured["json"]["amount"] == 200.0
    assert captured["json"]["referenceID"] == "ref-1"


@pytest.mark.parametrize("reference_id", ["contains/slash", "x" * 36, ""])
def test_copy_start_rejects_invalid_reference_id(reference_id: str) -> None:
    cfg = EtoroConfig(profile="live", api_key="k", user_key="u")

    result = etoro_sdk.copy_start_or_adjust(
        cfg,
        parent_cid=123,
        amount=200.0,
        reference_id=reference_id,
    )

    assert result["status"] == "error"
    assert "reference_id" in result["error"]


def test_copy_poll_gets_reference(monkeypatch) -> None:
    cfg = EtoroConfig(profile="live", api_key="k", user_key="u")
    captured_url = ""

    def _transport(method: str, url: str, **kwargs: Any) -> _FakeResponse:
        nonlocal captured_url
        captured_url = url
        return _FakeResponse(200, {"isCompleted": True})

    set_client_factory(lambda c: EtoroClient(cfg, transport=_transport))
    result = etoro_sdk.copy_poll(cfg, reference_id="ref-1")
    assert result["status"] == "ok"
    assert captured_url.endswith("/ref-1")


def test_copy_close_posts_close(monkeypatch) -> None:
    cfg = EtoroConfig(profile="live", api_key="k", user_key="u")
    captured: dict[str, Any] = {}

    def _transport(method: str, url: str, **kwargs: Any) -> _FakeResponse:
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        return _FakeResponse(200, {"token": "close-token"})

    set_client_factory(lambda c: EtoroClient(cfg, transport=_transport))
    result = etoro_sdk.copy_close(cfg, mirror_id=99, unregister_type="Close")
    assert result["status"] == "ok"
    assert result["token"] == "close-token"
    assert captured["url"].endswith("/close")
    assert captured["json"]["mirrorID"] == 99


def test_normalize_candles_flattens_nested_instrument_buckets() -> None:
    from src.markets.trading.connectors.etoro.sdk import _normalize_candles

    payload = {
        "interval": "OneDay",
        "candles": [
            {
                "instrumentId": 100000,
                "candles": [
                    {
                        "fromDate": "2026-08-05T00:00:00Z",
                        "open": 64297.8,
                        "high": 64499.9,
                        "low": 63828.87,
                        "close": 63855.8,
                    }
                ],
            }
        ],
    }
    rows = _normalize_candles(payload)
    assert len(rows) == 1
    assert rows[0]["open"] == 64297.8
    assert rows[0]["close"] == 63855.8


def test_service_dispatches_positions(monkeypatch) -> None:
    cfg = EtoroConfig(profile="paper", api_key="k", user_key="u")

    def _transport(method: str, url: str, **kwargs: Any) -> _FakeResponse:
        assert method == "GET"
        assert info_root(cfg) + "/portfolio" in url
        return _FakeResponse(200, {"positions": [{"positionID": 1, "instrumentDisplayName": "AAPL"}]})

    set_client_factory(lambda c: EtoroClient(cfg, transport=_transport))
    result = service.get_positions("etoro-paper-sdk", api_key="k", user_key="u")
    assert result["status"] == "ok"
    assert result["connector"] == "etoro"
    assert result["positions"][0]["symbol"] == "AAPL"


def test_service_search_instruments(monkeypatch) -> None:
    cfg = EtoroConfig(profile="paper", api_key="k", user_key="u")

    def _transport(method: str, url: str, **kwargs: Any) -> _FakeResponse:
        params = kwargs.get("params") or {}
        if params.get("internalSymbolFull") == "BTC":
            return _FakeResponse(200, {"items": [{"instrumentId": 100000, "internalSymbolFull": "BTC"}]})
        if params.get("search"):
            return _FakeResponse(200, {"items": [{"instrumentId": 100000, "internalSymbolFull": "BTC"}]})
        return _FakeResponse(200, {"items": []})

    set_client_factory(lambda c: EtoroClient(cfg, transport=_transport))
    result = service.search_instruments("BTC", "etoro-paper-sdk", api_key="k", user_key="u")
    assert result["status"] == "ok"
    assert result["instruments"][0]["instrument_id"] == 100000


def test_service_cancel_order_paper(monkeypatch) -> None:
    cfg = EtoroConfig(profile="paper", api_key="k", user_key="u")

    def _transport(method: str, url: str, **kwargs: Any) -> _FakeResponse:
        if method == "DELETE":
            return _FakeResponse(200, {})
        raise AssertionError(f"unexpected {method} {url}")

    set_client_factory(lambda c: EtoroClient(cfg, transport=_transport))
    result = service.cancel_order("42", "etoro-paper-trade", symbol="BTC", api_key="k", user_key="u")
    assert result["status"] == "ok"
    assert result["profile_id"] == "etoro-paper-trade"
