"""Empirical cross-source volume consistency guard (HKUDS/Vibe-Trading#1062).

Fetches the same settled A-share trading day from every reachable loader and
asserts the reported volumes agree within tolerance. This is the runtime lock
the #1062 audit called for: unit drift between fallback sources (the original
bug: baostock shares vs tencent/eastmoney lots, exactly 100x) fails loudly
here instead of silently corrupting volume analysis.

Network-guarded by design — sources unreachable from the test environment are
skipped individually, and the test skips entirely when fewer than two sources
are reachable, so CI without market access stays green.
"""

from __future__ import annotations

import pytest

CODE = "600519.SH"
TRADE_DATE = "2026-07-31"
TOLERANCE = 0.01


def _volume_from(loader_cls) -> float | None:
    try:
        data = loader_cls().fetch([CODE], TRADE_DATE, TRADE_DATE)
    except Exception:  # noqa: BLE001 — unreachable source = not participating
        return None
    df = data.get(CODE) if data else None
    if df is None or df.empty or "volume" not in df.columns:
        return None
    value = df["volume"].iloc[-1]
    if value is None or value != value:  # NaN guard
        return None
    return float(value)


def test_a_share_volume_consistent_across_sources():
    from src.backtest.loaders.baostock_loader import DataLoader as BaostockLoader
    from src.backtest.loaders.eastmoney_loader import DataLoader as EastmoneyLoader
    from src.backtest.loaders.mootdx_loader import DataLoader as MootdxLoader
    from src.backtest.loaders.tencent_loader import DataLoader as TencentLoader

    candidates = [
        ("tencent", TencentLoader),
        ("eastmoney", EastmoneyLoader),
        ("baostock", BaostockLoader),
        ("mootdx", MootdxLoader),
    ]
    volumes: dict[str, float] = {}
    for name, loader_cls in candidates:
        value = _volume_from(loader_cls)
        if value is not None:
            volumes[name] = value

    if len(volumes) < 2:
        pytest.skip(f"fewer than two reachable A-share sources: {sorted(volumes)}")

    baseline_name = next(iter(volumes))
    baseline = volumes[baseline_name]
    for name, value in volumes.items():
        ratio = value / baseline
        assert abs(ratio - 1.0) <= TOLERANCE, (
            f"{name} volume {value:,.0f} disagrees with {baseline_name} "
            f"{baseline:,.0f} (ratio {ratio:.2f}) — unit drift? see #1062"
        )
