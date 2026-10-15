"""MetaTrader 5 connector (Exness-style MT5 brokers) — ``broker_sdk`` transport.

Talks to a locally running MT5 terminal through the official ``MetaTrader5``
Python package, which is **Windows-only** and an opt-in extra
(``pip install "vibe-trading-ai[mt5]"``). Credentials live in
``~/.vibe-trading/mt5.json``.

Paper/live separation: MT5 has no separate paper API — "paper" means the
broker's DEMO account. Unlike key-scoped connectors the discriminator is
self-verifying: every session re-reads ``account_info().trade_mode`` and the
login, and hard-rejects a paper profile attached to a real-money account (and
vice versa; contest accounts are rejected everywhere, fail-closed). Live order
placement additionally routes through the mandate gate and kill switch.
"""
