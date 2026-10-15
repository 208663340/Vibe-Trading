"""Built-in Scalable Capital connector profiles.

Scalable Capital's Agentic Investing MCP endpoint is registered here as
read-only. The broker has no paper environment at all (their own help docs say
so), so there is no paper/live discriminator to verify and no paper order
placement to expose; the tier that follows is bounded-live, and the read-only
profile below is the first cut of it pending the argument schemas a live
``tools/list`` would settle (see :mod:`mcp`). No built-in profile in this
connector places orders.
"""

from __future__ import annotations

from src.markets.trading.types import TradingProfile

#: Read capabilities a Scalable Agentic MCP profile exposes. Deliberately no
#: ``orders.read``: the published catalog has no open-order read tool, only
#: ``list_portfolio_transactions`` (history) and the order path itself.
#:
#: Also deliberately no ``account.read`` / ``positions.read``, for now. Those two
#: are exactly what :func:`src.markets.trading.connections.is_portfolio_connection_profile`
#: requires, so declaring them makes this profile back a portfolio connection —
#: and nothing maps Scalable's holdings reply into the shared ``positions``
#: shape (``_normalize_remote_result`` has no Scalable branch), so a refresh
#: stores a *complete* snapshot holding zero positions. Scalable publishes no
#: tool argument schemas yet, so there is nothing to map against; a source that
#: can only error is no better. Add the pair back together with the
#: normalisation once a live ``tools/list`` settles the reply shape (#1367).
SCALABLE_READ_CAPABILITIES = ("quotes.read",)

SCALABLE_PROFILES: tuple[TradingProfile, ...] = (
    TradingProfile(
        id="scalable-live-mcp-readonly",
        connector="scalable",
        label="Scalable Capital Live · Agentic MCP Read-Only",
        environment="live",
        transport="remote_mcp",
        capabilities=SCALABLE_READ_CAPABILITIES,
        readonly=True,
        config={"server": "scalable"},
        notes=(
            "Reads a Scalable Capital account through their Agentic Investing MCP "
            "(desktop OAuth, enabled under Profile > Security). Scalable has no "
            "paper account, so this connector ships read-only: no built-in profile "
            "places orders, and the order-path tools (preview_* / submit_* / "
            "cancel_order) are pinned WRITE and stay out of the enabled allowlist."
        ),
    ),
)
