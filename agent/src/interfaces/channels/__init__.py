"""Chat channels module — multi-IM adapter layer.

Provides a plugin-based channel architecture for connecting Vibe-Trading
to chat platforms (Telegram, Discord, Slack, Feishu, WhatsApp, etc.).

Architecture::

    IM Message → ChannelAdapter.start()
        → _handle_message()
        → MessageBus.inbound
        → Agent Loop
        → MessageBus.outbound
        → ChannelManager._dispatch_outbound()
        → ChannelAdapter.send()

Core components:
    - :class:`BaseChannel` — abstract interface for channel adapters
    - :class:`ChannelManager` — coordinates all channels + outbound routing
    - :mod:`src.interfaces.channels.bus` — async message bus (InboundMessage, OutboundMessage)
    - :mod:`src.interfaces.channels.pairing` — DM sender authorization via pairing codes
    - :mod:`src.interfaces.channels.registry` — auto-discovery of built-in and plugin channels
    - :mod:`src.interfaces.channels.utils` — shared helpers (split_message, safe_filename, etc.)

Built-in channels are discovered via ``pkgutil.iter_modules`` on this package.
External plugins can register via the ``vibe_trading.channels`` entry_point group.

Usage::

    from src.interfaces.channels.manager import ChannelManager
    from src.interfaces.channels.bus.queue import MessageBus

    bus = MessageBus()
    manager = ChannelManager(config, bus, session_service=session_svc)
    await manager.start_all()
"""

from src.interfaces.channels.base import BaseChannel
from src.interfaces.channels.bus.events import InboundMessage, OutboundMessage
from src.interfaces.channels.bus.queue import MessageBus
from src.interfaces.channels.manager import ChannelManager

__all__ = [
    "BaseChannel",
    "ChannelManager",
    "InboundMessage",
    "MessageBus",
    "OutboundMessage",
]
