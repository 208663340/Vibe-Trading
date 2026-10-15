"""Read-only Binance USD-M connector coverage for Shadow Account observations."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.runtime.tools import trading_connector_tool
from src.markets.trading.connectors.binance import sdk as bn
from src.markets.trading.connectors.binance.classification import BINANCE_TOOL_CLASS
from src.markets.live.classification import ToolClass


def _account_payload() -> dict[str, object]:
    return {
        "multiAssetsMargin": False,
        "totalWalletBalance": "1000",
        "totalMarginBalance": "1050",
        "availableBalance": "700",
        "totalUnrealizedProfit": "50",
        "totalPositionInitialMargin": "180",
        "totalMaintMargin": "9",
        "totalOpenOrderInitialMargin": "0",
        "assets": [
            {
                "asset": "USDT",
                "walletBalance": "1000",
                "marginBalance": "1050",
                "availableBalance": "700",
                "initialMargin": "180",
                "positionInitialMargin": "180",
                "openOrderInitialMargin": "0",
                "maintMargin": "9",
                "unrealizedProfit": "50",
            }
        ],
        "positions": [
            {
                "symbol": "BTCUSDT",
                "positionSide": "BOTH",
                "positionAmt": "0.01",
                "entryPrice": "60000",
                "leverage": "10",
                "isolated": False,
                "positionInitialMargin": "60",
                "maintMargin": "3",
                "unrealizedProfit": "20",
                "openOrderInitialMargin": "0",
            },
            {
                "symbol": "ETHUSDT",
                "positionSide": "BOTH",
                "positionAmt": "-0.2",
                "entryPrice": "3000",
                "leverage": "5",
                "isolated": True,
                "positionInitialMargin": "120",
                "maintMargin": "6",
                "unrealizedProfit": "30",
                "openOrderInitialMargin": "0",
            },
        ],
    }


def _position_risk_payload() -> list[dict[str, object]]:
    return [
        {
            "symbol": "BTCUSDT",
            "positionSide": "BOTH",
            "positionAmt": "0.01",
            "entryPrice": "60000",
            "isolatedMargin": "0",
            "marginAsset": "USDT",
            "unRealizedProfit": "20",
            "positionInitialMargin": "60",
            "maintMargin": "3",
            "openOrderInitialMargin": "0",
            "updateTime": 1_787_664_600_000,
        },
        {
            "symbol": "ETHUSDT",
            "positionSide": "BOTH",
            "positionAmt": "-0.2",
            "entryPrice": "3000",
            "isolatedMargin": "130",
            "marginAsset": "USDT",
            "unRealizedProfit": "30",
            "positionInitialMargin": "120",
            "maintMargin": "6",
            "openOrderInitialMargin": "0",
            "updateTime": 1_787_664_601_000,
        },
    ]


class _FakeUsdMReads:
    def __init__(
        self,
        account: dict[str, object] | None = None,
        positions: list[dict[str, object]] | None = None,
    ) -> None:
        self.account = account if account is not None else _account_payload()
        self.positions = positions if positions is not None else _position_risk_payload()
        self.calls: list[str] = []

    def fapiprivatev2_get_account(self) -> dict[str, object]:
        self.calls.append("account-v2")
        return self.account

    def fapiprivatev3_get_positionrisk(self) -> list[dict[str, object]]:
        self.calls.append("position-risk-v3")
        return self.positions


def _usdm_config(**changes: object) -> bn.BinanceConfig:
    payload = {
        "api_key": "key",
        "api_secret": "secret",
        "profile": "live-readonly",
        "market_type": "usdm",
    }
    payload.update(changes)
    return bn.BinanceConfig.from_mapping(payload)


def test_usdm_config_reuses_live_readonly_profile_and_futures_host() -> None:
    config = bn.BinanceConfig.from_mapping({"profile": "live-readonly", "market_type": "usdm"})

    assert config.market_type == "usdm"
    assert config.host == "https://fapi.binance.com"
    assert config.is_testnet is False

    with pytest.raises(bn.BinanceConfigError, match="live-readonly"):
        bn.BinanceConfig.from_mapping({"profile": "live", "market_type": "usdm"})
    with pytest.raises(bn.BinanceConfigError, match="market_type"):
        bn.BinanceConfig.from_mapping({"profile": "live-readonly", "market_type": "coinm"})
    with pytest.raises(bn.BinanceConfigError, match="observation_absolute_tolerance"):
        _usdm_config(observation_absolute_tolerance=-1)


def test_usdm_exchange_uses_binanceusdm_and_validates_private_hosts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeUsdM:
        def __init__(self, config: dict[str, object]) -> None:
            captured["config"] = config
            self.urls = {
                "api": {
                    "fapiPrivateV2": "https://fapi.binance.com/fapi/v2",
                    "fapiPrivateV3": "https://fapi.binance.com/fapi/v3",
                }
            }

        def set_sandbox_mode(self, enabled: bool) -> None:
            captured["sandbox"] = enabled

    class UnexpectedSpot:
        def __init__(self, _config: dict[str, object]) -> None:
            raise AssertionError("USD-M read must not build the spot client")

    monkeypatch.setattr(
        bn,
        "_require_ccxt",
        lambda: SimpleNamespace(binance=UnexpectedSpot, binanceusdm=FakeUsdM),
    )
    monkeypatch.setattr(bn, "getproxies", lambda: {})

    exchange = bn._exchange(
        bn.BinanceConfig.from_mapping(
            {
                "api_key": "key",
                "api_secret": "secret",
                "profile": "live-readonly",
                "market_type": "usdm",
            }
        )
    )

    assert isinstance(exchange, FakeUsdM)
    assert captured["sandbox"] is False
    assert captured["config"]["options"] == {
        "adjustForTimeDifference": True,
        "recvWindow": 10_000,
    }


@pytest.mark.parametrize(
    "bad_url",
    [
        "https://example.invalid/fapi/v2",
        "http://fapi.binance.com/fapi/v2",
        "https://fapi.binance.com:8443/fapi/v2",
        "https://fapi.binance.com/fapi/v1",
        "https://fapi.binance.com/fapi/v2?redirect=1",
        "https://fapi.binance.com/fapi/v2#fragment",
    ],
)
def test_usdm_exchange_rejects_unapproved_private_endpoint(
    monkeypatch: pytest.MonkeyPatch,
    bad_url: str,
) -> None:
    class RedirectedUsdM:
        def __init__(self, _config: dict[str, object]) -> None:
            self.urls = {
                "api": {
                    "fapiPrivateV2": bad_url,
                    "fapiPrivateV3": "https://fapi.binance.com/fapi/v3",
                }
            }

        def set_sandbox_mode(self, _enabled: bool) -> None:
            return None

    monkeypatch.setattr(
        bn,
        "_require_ccxt",
        lambda: SimpleNamespace(binanceusdm=RedirectedUsdM),
    )
    monkeypatch.setattr(bn, "getproxies", lambda: {})

    with pytest.raises(bn.BinanceConfigError, match="unapproved host"):
        bn._exchange(bn.BinanceConfig.from_mapping({"profile": "live-readonly", "market_type": "usdm"}))


def test_trading_tools_forward_explicit_market_type_override() -> None:
    assert "market_type" in trading_connector_tool.TRADING_COMMON_PARAMETERS
    overrides = trading_connector_tool._overrides({"market_type": "usdm", "observation_absolute_tolerance": 0.1})
    assert overrides["market_type"] == "usdm"
    assert overrides["observation_absolute_tolerance"] == 0.1


def test_usdm_config_cannot_reach_spot_order_methods(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = bn.BinanceConfig.from_mapping({"profile": "live-readonly", "market_type": "usdm"})
    exchange_calls = 0

    def unexpected_exchange(_config: bn.BinanceConfig) -> None:
        nonlocal exchange_calls
        exchange_calls += 1
        raise AssertionError("write path reached an exchange client")

    monkeypatch.setattr(bn, "_exchange", unexpected_exchange)

    placed = bn.place_order(
        config,
        symbol="BTC/USDT:USDT",
        side="buy",
        quantity=0.001,
    )
    cancelled = bn.cancel_order(config, "order-1", symbol="BTC/USDT:USDT")

    expected = "Binance USD-M Shadow Account is read-only"
    assert placed == {"status": "error", "error": expected}
    assert cancelled == {"status": "error", "error": expected}
    assert exchange_calls == 0


def test_usdm_account_snapshot_combines_signed_account_and_position_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exchange = _FakeUsdMReads()
    times = iter(
        (
            datetime(2026, 8, 26, 10, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 8, 26, 10, 0, 2, tzinfo=timezone.utc),
        )
    )
    monkeypatch.setattr(bn, "_exchange", lambda _config: exchange)
    monkeypatch.setattr(bn, "_utc_now", lambda: next(times), raising=False)

    result = bn.get_account_snapshot(_usdm_config())

    assert exchange.calls == ["account-v2", "position-risk-v3"]
    assert result["status"] == "ok"
    assert result["source"] == "binance-usdm"
    assert result["source_profile"] == "binance-live-sdk-readonly"
    assert result["market_type"] == "usdm"
    assert result["schema_version"] == "binance-usdm-account-observation-v1"
    assert result["observed_at"] == "2026-08-26T10:00:02+00:00"
    assert result["observation_span_seconds"] == 2.0
    assert len(result["configuration_hash"]) == 64
    assert "key" not in str(result)
    assert "secret" not in str(result)
    assert result["account"] == {
        "wallet_balance": 1000.0,
        "margin_balance": 1050.0,
        "available_balance": 700.0,
        "total_unrealized_pnl": 50.0,
        "total_initial_margin": 180.0,
        "total_maintenance_margin": 9.0,
        "open_order_initial_margin": 0.0,
    }
    assert result["positions"] == [
        {
            "symbol": "BTC-USDT-PERP",
            "quantity": 0.01,
            "entry_price": 60000.0,
            "leverage": 10.0,
            "margin_mode": "cross",
            "isolated_margin": None,
            "unrealized_pnl": 20.0,
            "initial_margin": 60.0,
            "maintenance_margin": 3.0,
            "update_time": 1_787_664_600_000,
        },
        {
            "symbol": "ETH-USDT-PERP",
            "quantity": -0.2,
            "entry_price": 3000.0,
            "leverage": 5.0,
            "margin_mode": "isolated",
            "isolated_margin": 130.0,
            "unrealized_pnl": 30.0,
            "initial_margin": 120.0,
            "maintenance_margin": 6.0,
            "update_time": 1_787_664_601_000,
        },
    ]
    assert result["fidelity_flags"] == [
        "client_observation_time",
        "sequential_signed_reads",
    ]


def test_usdm_positions_reuse_the_same_strict_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exchange = _FakeUsdMReads()
    monkeypatch.setattr(bn, "_exchange", lambda _config: exchange)

    result = bn.get_positions(_usdm_config())

    assert exchange.calls == ["account-v2", "position-risk-v3"]
    assert [position["symbol"] for position in result["positions"]] == [
        "BTC-USDT-PERP",
        "ETH-USDT-PERP",
    ]
    assert result["source"] == "binance-usdm"


def test_usdm_dynamic_tolerance_is_explicit_and_configurable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    positions = _position_risk_payload()
    positions[0]["unRealizedProfit"] = "20.01"
    monkeypatch.setattr(
        bn,
        "_exchange",
        lambda _config: _FakeUsdMReads(_account_payload(), positions),
    )

    with pytest.raises(bn.BinanceConfigError, match="incoherent"):
        bn.get_account_snapshot(_usdm_config())

    result = bn.get_account_snapshot(_usdm_config(observation_absolute_tolerance=0.1))

    assert result["positions"][0]["unrealized_pnl"] == 20.01


def test_usdm_status_counts_positions_not_spot_balances(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bn, "ccxt_available", lambda: True)
    monkeypatch.setattr(bn, "_exchange", lambda _config: _FakeUsdMReads())

    result = bn.check_status(_usdm_config())

    assert result["status"] == "ok"
    assert result["account"] == {
        "profile": "live-readonly",
        "is_testnet": False,
        "positions": 2,
    }


def test_usdm_allows_only_the_two_curated_private_read_methods() -> None:
    assert BINANCE_TOOL_CLASS["fapiprivatev2_get_account"] is ToolClass.READ
    assert BINANCE_TOOL_CLASS["fapiprivatev3_get_positionrisk"] is ToolClass.READ


def test_usdm_rejects_non_shadow_read_surfaces_before_client_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exchange_calls = 0

    def unexpected_exchange(_config: bn.BinanceConfig) -> None:
        nonlocal exchange_calls
        exchange_calls += 1
        raise AssertionError("unsupported USD-M surface reached the client")

    monkeypatch.setattr(bn, "_exchange", unexpected_exchange)
    config = _usdm_config()

    for call in (
        lambda: bn.get_open_orders(config),
        lambda: bn.get_quote("BTC-USDT-PERP", config=config),
        lambda: bn.get_historical_bars("BTC-USDT-PERP", config=config),
    ):
        with pytest.raises(bn.BinanceConfigError, match="account and position reads"):
            call()
    assert exchange_calls == 0


def test_usdm_endpoint_failure_propagates_without_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailedRead(_FakeUsdMReads):
        def fapiprivatev2_get_account(self) -> dict[str, object]:
            raise RuntimeError("synthetic endpoint failure")

    monkeypatch.setattr(bn, "_exchange", lambda _config: FailedRead())

    with pytest.raises(RuntimeError, match="synthetic endpoint failure"):
        bn.get_account_snapshot(_usdm_config())


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda account, _positions: account.__setitem__("multiAssetsMargin", True),
            "multi-asset margin",
        ),
        (
            lambda account, _positions: account.__setitem__("totalOpenOrderInitialMargin", "1"),
            "open-order margin",
        ),
        (
            lambda account, _positions: account["positions"][0].__setitem__("positionSide", "LONG"),
            "one-way",
        ),
        (
            lambda _account, positions: positions[0].__setitem__("marginAsset", "USDC"),
            "USDT collateral",
        ),
        (
            lambda _account, positions: positions[0].__setitem__("positionAmt", "0.02"),
            "incoherent",
        ),
        (
            lambda _account, positions: positions[0].__setitem__("openOrderInitialMargin", "1"),
            "open-order margin",
        ),
        (
            lambda _account, positions: positions[0].__setitem__("positionSide", "LONG"),
            "one-way",
        ),
        (
            lambda _account, positions: positions[0].__setitem__("isolatedMargin", "10"),
            "cross position",
        ),
        (
            lambda account, _positions: account["positions"][0].__setitem__("positionInitialMargin", "999"),
            "incoherent",
        ),
        (
            lambda account, _positions: account.__setitem__("totalPositionInitialMargin", "999"),
            "account totals",
        ),
        (lambda account, _positions: account.pop("assets"), "assets"),
        (
            lambda account, _positions: account["assets"].append(
                {
                    "asset": "USDC",
                    "walletBalance": "1",
                    "marginBalance": "1",
                    "availableBalance": "1",
                    "initialMargin": "0",
                    "positionInitialMargin": "0",
                    "openOrderInitialMargin": "0",
                    "maintMargin": "0",
                    "unrealizedProfit": "0",
                }
            ),
            "USDT asset",
        ),
        (
            lambda account, _positions: account["assets"][0].__setitem__("walletBalance", "999"),
            "asset totals",
        ),
        (
            lambda account, _positions: account["assets"].append(deepcopy(account["assets"][0])),
            "exactly one USDT",
        ),
    ],
)
def test_usdm_observation_fails_closed_for_unsupported_or_incoherent_state(
    monkeypatch: pytest.MonkeyPatch,
    mutate,
    message: str,
) -> None:
    account = deepcopy(_account_payload())
    positions = deepcopy(_position_risk_payload())
    mutate(account, positions)
    monkeypatch.setattr(
        bn,
        "_exchange",
        lambda _config: _FakeUsdMReads(account, positions),
    )

    with pytest.raises(bn.BinanceConfigError, match=message):
        bn.get_account_snapshot(_usdm_config())
