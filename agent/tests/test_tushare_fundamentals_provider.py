from __future__ import annotations

import sys
from types import SimpleNamespace

import pandas as pd
import pytest

from src.backtest.loaders.tushare_fundamentals import (
    SchemaValidationError,
    TushareFundamentalProvider,
    UnknownTableError,
    _is_subdaily_index,
    enrich_price_frames_with_fundamentals,
    SubdailyPitError,
)


class _FakeTushareApi:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def income(self, **kwargs: object) -> pd.DataFrame:
        self.calls.append(("income", kwargs))
        return pd.DataFrame(
            [
                {
                    "ts_code": kwargs["ts_code"],
                    "end_date": "20231231",
                    "ann_date": "20240401",
                    "f_ann_date": "20240402",
                    "total_revenue": 100.0,
                },
                {
                    "ts_code": kwargs["ts_code"],
                    "end_date": "20240331",
                    "ann_date": "20240425",
                    "f_ann_date": "20240506",
                    "total_revenue": 120.0,
                },
            ]
        )


def test_provider_exposes_first_milestone_financial_table_metadata() -> None:
    provider = TushareFundamentalProvider(api=_FakeTushareApi())

    assert provider.list_tables() == ["balancesheet", "cashflow", "fina_indicator", "income"]

    schema = provider.describe_table("income")
    assert schema.api_name == "income"
    assert schema.point_in_time_column == "f_ann_date"
    assert {"ts_code", "end_date", "ann_date", "f_ann_date", "total_revenue"} <= {
        column.name for column in schema.columns
    }


def test_default_constructor_uses_project_tushare_token_env(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    fake_api = _FakeTushareApi()

    def pro_api(token: str = "") -> _FakeTushareApi:
        calls.append(token)
        return fake_api

    monkeypatch.setenv("TUSHARE_TOKEN", "ts-secret-token")
    monkeypatch.setitem(sys.modules, "tushare", SimpleNamespace(pro_api=pro_api))

    provider = TushareFundamentalProvider()

    assert provider.api is fake_api
    assert calls == ["ts-secret-token"]


def test_query_fundamentals_returns_pit_safe_dataframe() -> None:
    api = _FakeTushareApi()
    provider = TushareFundamentalProvider(api=api)

    result = provider.query_fundamentals(
        "income",
        ["000001.SZ", "600000.SH"],
        as_of="2024-04-30",
        periods=["20231231", "20240331"],
        fields=["total_revenue"],
    )

    assert list(result["ts_code"]) == ["000001.SZ", "600000.SH"]
    assert list(result["end_date"]) == ["20231231", "20231231"]
    assert list(result["f_ann_date"]) == ["20240402", "20240402"]
    assert list(result["total_revenue"]) == [100.0, 100.0]
    assert api.calls == [
        ("income", {"ts_code": "000001.SZ", "period": None}),
        ("income", {"ts_code": "600000.SH", "period": None}),
    ]


def test_query_fundamentals_falls_back_to_ann_date_per_row() -> None:
    class SparseDisclosureApi:
        def balancesheet(self, **kwargs: object) -> pd.DataFrame:
            return pd.DataFrame(
                [
                    {
                        "ts_code": kwargs["ts_code"],
                        "end_date": "20231231",
                        "ann_date": "20240401",
                        "f_ann_date": None,
                        "total_assets": 100.0,
                    },
                    {
                        "ts_code": kwargs["ts_code"],
                        "end_date": "20240331",
                        "ann_date": "20240420",
                        "f_ann_date": "20240506",
                        "total_assets": 110.0,
                    },
                ]
            )

    provider = TushareFundamentalProvider(api=SparseDisclosureApi())

    result = provider.query_fundamentals(
        "balancesheet",
        ["000001.SZ"],
        as_of="2024-04-30",
        fields=["total_assets"],
    )

    assert list(result["end_date"]) == ["20231231"]
    assert list(result["total_assets"]) == [100.0]


def test_query_fundamentals_rejects_unknown_tables() -> None:
    provider = TushareFundamentalProvider(api=_FakeTushareApi())

    with pytest.raises(UnknownTableError):
        provider.query_fundamentals("daily_basic", ["000001.SZ"], as_of="2024-04-30")


def test_query_fundamentals_validates_required_schema_columns() -> None:
    class BadApi:
        def fina_indicator(self, **kwargs: object) -> pd.DataFrame:
            return pd.DataFrame([{"ts_code": kwargs["ts_code"], "ann_date": "20240401"}])

    provider = TushareFundamentalProvider(api=BadApi())

    with pytest.raises(SchemaValidationError, match="end_date"):
        provider.query_fundamentals("fina_indicator", ["000001.SZ"], as_of="2024-04-30")


def test_enrich_price_frames_with_fundamentals_respects_point_in_time_dates() -> None:
    class StatementApi:
        def income(self, **kwargs: object) -> pd.DataFrame:
            return pd.DataFrame(
                [
                    {
                        "ts_code": kwargs["ts_code"],
                        "end_date": "20231231",
                        "ann_date": "20240401",
                        "f_ann_date": "20240402",
                        "total_revenue": 80.0,
                        "n_income": 8.0,
                    },
                    {
                        "ts_code": kwargs["ts_code"],
                        "end_date": "20240331",
                        "ann_date": "20240425",
                        "f_ann_date": "20240506",
                        "total_revenue": 120.0,
                        "n_income": 12.0,
                    },
                ]
            )

    dates = pd.to_datetime(["2024-04-01", "2024-04-03", "2024-05-07"])
    bars = pd.DataFrame(
        {
            "open": [10.0, 11.0, 12.0],
            "high": [10.5, 11.5, 12.5],
            "low": [9.5, 10.5, 11.5],
            "close": [10.2, 11.2, 12.2],
            "volume": [1000, 1100, 1200],
        },
        index=dates,
    )
    provider = TushareFundamentalProvider(api=StatementApi())

    enriched = enrich_price_frames_with_fundamentals(
        {"000001.SZ": bars},
        provider,
        {"income": ["total_revenue", "n_income"]},
        as_of="2024-05-31",
    )

    result = enriched["000001.SZ"]
    assert pd.isna(result.loc[pd.Timestamp("2024-04-01"), "income_total_revenue"])
    assert result.loc[pd.Timestamp("2024-04-03"), "income_total_revenue"] == 80.0
    assert result.loc[pd.Timestamp("2024-05-07"), "income_total_revenue"] == 120.0
    assert result.loc[pd.Timestamp("2024-05-07"), "income_end_date"] == "20240331"


# ---------------------------------------------------------------------------
# Issue #771 fixtures: FY2023 original + restatement + Q1 2024
# ---------------------------------------------------------------------------


def _issue771_api_data(ts_code: str) -> list[dict]:
    """Return the three-row fixture described in issue #771."""
    return [
        # FY2023 original: announced and disclosed 2024-01-30, revenue=100
        {
            "ts_code": ts_code,
            "ann_date": "20240130",
            "f_ann_date": "20240130",
            "end_date": "20231231",
            "revenue": 100.0,
        },
        # FY2023 restatement: original announcement still 2024-01-30,
        # but the actual disclosure (f_ann_date) is 2024-05-15, revenue=95
        {
            "ts_code": ts_code,
            "ann_date": "20240130",
            "f_ann_date": "20240515",
            "end_date": "20231231",
            "revenue": 95.0,
        },
        # Q1 2024: announced and disclosed 2024-04-30, revenue=30
        {
            "ts_code": ts_code,
            "ann_date": "20240430",
            "f_ann_date": "20240430",
            "end_date": "20240331",
            "revenue": 30.0,
        },
    ]


class _Issue771Api:
    """Fake Tushare API returning the three-row issue #771 fixture."""

    def income(self, **kwargs: object) -> pd.DataFrame:
        return pd.DataFrame(_issue771_api_data(str(kwargs["ts_code"])))


def test_t1_query_fundamentals_deduplicates_restated_rows() -> None:
    """T1: query_fundamentals with as_of after all disclosures keeps only the
    latest revision for each (ts_code, end_date) pair."""
    provider = TushareFundamentalProvider(api=_Issue771Api())

    result = provider.query_fundamentals(
        "income",
        ["000001.SZ"],
        as_of="20240601",
        fields=["revenue"],
    )

    # No (ts_code, end_date) duplicates
    assert not result.duplicated(subset=["ts_code", "end_date"]).any(), (
        "query_fundamentals must not return duplicate (ts_code, end_date) rows"
    )
    # FY2023 must survive but carry the restated revenue=95
    fy2023 = result[result["end_date"] == "20231231"]
    assert len(fy2023) == 1, "FY2023 should appear exactly once after dedup"
    assert fy2023.iloc[0]["revenue"] == 95.0, (
        "FY2023 must keep the restatement value (rev=95, f_ann_date=20240515)"
    )
    # Q1 must also be present
    q1 = result[result["end_date"] == "20240331"]
    assert len(q1) == 1
    assert q1.iloc[0]["revenue"] == 30.0


def test_t2_enrich_no_period_regression() -> None:
    """T2: enrich must not regress to an older period when a late restatement
    arrives for that period after a newer period's filing is already visible.

    Timeline:
      2024-01-30  FY2023 original published  (rev=100, visible from 01-30)
      2024-04-30  Q1 2024 published          (rev=30,  visible from 04-30)
      2024-05-15  FY2023 restatement         (end_date < current visible end_date
                                              => must NOT roll back to FY2023)

    Expected observations on each trade date:
      04-25..04-29  income_end_date=20231231, income_revenue=100
      04-30..05-14  income_end_date=20240331, income_revenue=30
      05-15..05-20  income_end_date=20240331, income_revenue=30  (no regression)
    """
    provider = TushareFundamentalProvider(api=_Issue771Api())
    trade_dates = pd.bdate_range("2024-04-25", "2024-05-20")
    bars = pd.DataFrame({"close": 10.0}, index=trade_dates)

    enriched = enrich_price_frames_with_fundamentals(
        {"000001.SZ": bars},
        provider,
        {"income": ["revenue"]},
        as_of="20240601",
    )
    result = enriched["000001.SZ"]

    # Before Q1 is published: see FY2023 original
    for d in pd.bdate_range("2024-04-25", "2024-04-29"):
        assert result.loc[d, "income_end_date"] == "20231231", (
            f"{d.date()}: expected FY2023 end_date, got {result.loc[d, 'income_end_date']}"
        )
        assert result.loc[d, "income_revenue"] == 100.0, (
            f"{d.date()}: expected revenue=100, got {result.loc[d, 'income_revenue']}"
        )

    # After Q1 published, before restatement: see Q1
    for d in pd.bdate_range("2024-04-30", "2024-05-14"):
        assert result.loc[d, "income_end_date"] == "20240331", (
            f"{d.date()}: expected Q1 end_date, got {result.loc[d, 'income_end_date']}"
        )
        assert result.loc[d, "income_revenue"] == 30.0, (
            f"{d.date()}: expected revenue=30, got {result.loc[d, 'income_revenue']}"
        )

    # After FY2023 restatement: must NOT regress to FY2023
    for d in pd.bdate_range("2024-05-15", "2024-05-20"):
        assert result.loc[d, "income_end_date"] == "20240331", (
            f"{d.date()}: snapshot regressed to {result.loc[d, 'income_end_date']} "
            f"(expected Q1 20240331 to remain visible)"
        )
        assert result.loc[d, "income_revenue"] == 30.0, (
            f"{d.date()}: expected revenue=30, got {result.loc[d, 'income_revenue']}"
        )


def test_t3_enrich_same_period_restatement_updates_value() -> None:
    """T3: when only same-period original+restatement exist (no newer period),
    the restatement must update the visible value from its pit_date onward
    while the original value remains visible before the restatement date."""

    class _SamePeriodApi:
        def income(self, **kwargs: object) -> pd.DataFrame:
            return pd.DataFrame(
                [
                    # FY2023 original
                    {
                        "ts_code": kwargs["ts_code"],
                        "ann_date": "20240130",
                        "f_ann_date": "20240130",
                        "end_date": "20231231",
                        "revenue": 100.0,
                    },
                    # FY2023 restatement, disclosed 2024-05-15
                    {
                        "ts_code": kwargs["ts_code"],
                        "ann_date": "20240130",
                        "f_ann_date": "20240515",
                        "end_date": "20231231",
                        "revenue": 95.0,
                    },
                ]
            )

    provider = TushareFundamentalProvider(api=_SamePeriodApi())
    trade_dates = pd.bdate_range("2024-01-30", "2024-05-20")
    bars = pd.DataFrame({"close": 10.0}, index=trade_dates)

    enriched = enrich_price_frames_with_fundamentals(
        {"000001.SZ": bars},
        provider,
        {"income": ["revenue"]},
        as_of="20240601",
    )
    result = enriched["000001.SZ"]

    # Before restatement: original value
    for d in pd.bdate_range("2024-01-30", "2024-05-14"):
        assert result.loc[d, "income_revenue"] == 100.0, (
            f"{d.date()}: expected original revenue=100, got {result.loc[d, 'income_revenue']}"
        )

    # From restatement date onward: updated value
    for d in pd.bdate_range("2024-05-15", "2024-05-20"):
        assert result.loc[d, "income_revenue"] == 95.0, (
            f"{d.date()}: expected restated revenue=95, got {result.loc[d, 'income_revenue']}"
        )


# ---------------------------------------------------------------------------
# #1387: PIT visibility on sub-daily frames
# ---------------------------------------------------------------------------


class _AnnouncementApi:
    """One filing announced on 2024-04-26, the day the intraday bars cover."""

    def income(self, **kwargs: object) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "ts_code": kwargs["ts_code"],
                    "end_date": "20240331",
                    "ann_date": "20240426",
                    "f_ann_date": "20240426",
                    "total_revenue": 120.0,
                }
            ]
        )


def _bars(index: pd.DatetimeIndex) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": 10.0,
            "high": 10.5,
            "low": 9.5,
            "close": 10.2,
            "volume": 1000,
        },
        index=index,
    )


_INTRADAY_INDEX = pd.to_datetime(
    [
        "2024-04-26 09:30",
        "2024-04-26 10:30",
        "2024-04-26 15:00",
        "2024-04-29 09:30",
        "2024-04-29 10:30",
    ]
)


def test_subdaily_frames_are_rejected_by_default() -> None:
    """An ann_date has no time of day, so a day-granular rule leaks intraday.

    Before this guard the 09:30 bar of the announcement day already carried
    the filing — a lookahead of a full session, on filings that in CN
    typically land after the close.
    """
    provider = TushareFundamentalProvider(api=_AnnouncementApi())

    with pytest.raises(ValueError, match="daily frames only"):
        enrich_price_frames_with_fundamentals(
            {"000001.SZ": _bars(_INTRADAY_INDEX)},
            provider,
            {"income": ["total_revenue"]},
            as_of="2024-05-31",
        )


def test_subdaily_next_day_policy_hides_the_announcement_day() -> None:
    provider = TushareFundamentalProvider(api=_AnnouncementApi())

    result = enrich_price_frames_with_fundamentals(
        {"000001.SZ": _bars(_INTRADAY_INDEX)},
        provider,
        {"income": ["total_revenue"]},
        as_of="2024-05-31",
        subdaily="next_day",
    )["000001.SZ"]

    announcement_day = result.loc["2024-04-26", "income_total_revenue"]
    assert announcement_day.isna().all(), announcement_day.tolist()
    # …and the very next bar, on D+1, does carry it: the guard must not turn
    # into "intraday never sees fundamentals".
    assert result.loc[pd.Timestamp("2024-04-29 09:30"), "income_total_revenue"] == 120.0


def test_daily_frames_keep_same_day_visibility() -> None:
    """The sub-daily guard must not move the daily contract.

    A daily signal on day D fills at D+1's open at the earliest, so same-day
    visibility on a daily bar is correct and stays.
    """
    provider = TushareFundamentalProvider(api=_AnnouncementApi())
    daily = pd.to_datetime(["2024-04-25", "2024-04-26", "2024-04-29"])

    result = enrich_price_frames_with_fundamentals(
        {"000001.SZ": _bars(daily)},
        provider,
        {"income": ["total_revenue"]},
        as_of="2024-05-31",
    )["000001.SZ"]

    assert pd.isna(result.loc[pd.Timestamp("2024-04-25"), "income_total_revenue"])
    assert result.loc[pd.Timestamp("2024-04-26"), "income_total_revenue"] == 120.0


def test_daily_index_written_with_midnight_times_is_not_flagged_subdaily() -> None:
    """A daily frame whose stamps carry 00:00 is still daily."""
    provider = TushareFundamentalProvider(api=_AnnouncementApi())
    midnight = pd.to_datetime(["2024-04-25 00:00", "2024-04-26 00:00"])

    result = enrich_price_frames_with_fundamentals(
        {"000001.SZ": _bars(midnight)},
        provider,
        {"income": ["total_revenue"]},
        as_of="2024-05-31",
    )["000001.SZ"]

    assert result.loc[pd.Timestamp("2024-04-26"), "income_total_revenue"] == 120.0


def test_unknown_subdaily_policy_is_rejected() -> None:
    provider = TushareFundamentalProvider(api=_AnnouncementApi())

    with pytest.raises(ValueError, match="subdaily must be one of"):
        enrich_price_frames_with_fundamentals(
            {"000001.SZ": _bars(_INTRADAY_INDEX)},
            provider,
            {"income": ["total_revenue"]},
            as_of="2024-05-31",
            subdaily="whenever",
        )


@pytest.mark.parametrize(
    ("index", "expected"),
    [
        (pd.date_range("2024-01-01", periods=3), False),
        (pd.date_range("2024-01-01", periods=3, freq="h"), True),
        (pd.DatetimeIndex([]), False),
        (pd.Index(["2024-01-01", "2024-01-02"]), False),
        (pd.Index(["2024-01-01 09:30", "2024-01-01 10:30"]), True),
        # A numeric index is not a clock. pd.to_datetime reads it as
        # nanoseconds since the epoch, which puts every row at a distinct
        # sub-second time and would reject every such frame as "intraday".
        (pd.RangeIndex(5), False),
        (pd.Index([0, 1, 2]), False),
        # An unparseable index is left to fail where it always failed (the
        # merge), not turned into a sub-daily rejection carrying a
        # date-parse message.
        (pd.Index(["a", "b"]), False),
    ],
)
def test_subdaily_detector_only_reasons_about_clocks(
    index: pd.Index, expected: bool
) -> None:
    assert _is_subdaily_index(index) is expected


def test_provider_failures_keep_the_wrapped_message(monkeypatch) -> None:
    """Only the sub-daily contract error passes through the engine verbatim.

    A stray ValueError from inside enrichment is a provider failure and must
    keep the "Tushare enrichment failed" wrapper that names it as such.
    """
    from src.backtest.engines import base as base_engine

    def boom(*args: object, **kwargs: object) -> None:
        raise ValueError("provider returned nonsense")

    monkeypatch.setattr(
        base_engine, "TushareFundamentalProvider", lambda: object(), raising=False
    )
    monkeypatch.setattr(
        base_engine, "enrich_price_frames_with_fundamentals", boom, raising=False
    )
    frame = _bars(pd.to_datetime(["2024-04-25", "2024-04-26"]))

    with pytest.raises(RuntimeError, match="Tushare enrichment failed"):
        base_engine._maybe_enrich_fundamentals(
            {"000001.SZ": frame},
            {"fundamental_fields": {"income": ["total_revenue"]}, "end_date": "2024-04-30"},
        )


def test_subdaily_contract_error_is_not_reworded(monkeypatch) -> None:
    from src.backtest.engines import base as base_engine

    monkeypatch.setattr(
        base_engine,
        "TushareFundamentalProvider",
        lambda: TushareFundamentalProvider(api=_AnnouncementApi()),
        raising=False,
    )

    with pytest.raises(SubdailyPitError, match="daily frames only"):
        base_engine._maybe_enrich_fundamentals(
            {"000001.SZ": _bars(_INTRADAY_INDEX)},
            {"fundamental_fields": {"income": ["total_revenue"]}, "end_date": "2024-04-30"},
        )
