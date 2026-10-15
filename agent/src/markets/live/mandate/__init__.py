"""Bounded-autonomy mandate for the live trading channel.

The mandate is the user-defined safety boundary: quantitative hard caps
(:class:`~src.markets.live.mandate.model.HardCaps`) plus a discovery universe
(:class:`~src.markets.live.mandate.model.UniverseConstraint`) plus consent provenance
(:class:`~src.markets.live.mandate.model.ConsentMeta`). It is loaded read-only at boot
by :func:`src.markets.live.mandate.store.load_mandate`; there is no agent-reachable
write path (see the live-trading SPEC §3 trust invariant).
"""
