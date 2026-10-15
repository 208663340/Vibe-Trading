"""Lazy-init service singletons shared by session, channel, and live routes."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import HTTPException

from src.interfaces.api._compat import host_attr as _host_attr, set_host_attr as _set_host_attr
from src.interfaces.api.helpers import RUNS_DIR, SESSIONS_DIR
from src.platform.config.accessor import get_env_config


# ============================================================================
# Global singletons
# ============================================================================

_runtime_services = None
_session_service = None
_channel_runtime = None
_channel_bus = None
_channel_manager = None


# ============================================================================
# Lazy initializers
# ============================================================================


def _get_session_service():
    """Lazy-init session service when ENABLE_SESSION_RUNTIME=true."""
    global _session_service

    import sys as _sys
    _host = _sys.modules.get("src.interfaces.api_server")
    if _host is not None and hasattr(_host, "_session_service"):
        host_val = getattr(_host, "_session_service")
        if host_val is not None:
            return host_val
    elif _session_service is not None:
        return _session_service

    if not get_env_config().api.enable_session_runtime:
        return None

    runtime = _get_runtime_services()
    if _host is not None and getattr(_host, "_session_service", None) is None and runtime.session_service is not None:
        # Clearing the legacy host singleton is an explicit reset seam. Preserve
        # its behavior for idle services without evading a live storage owner.
        if not runtime.retire_idle():
            from src.runtime.session.execution import SessionServiceClosingError
            raise SessionServiceClosingError("Application session runtime still owns active writes")
        runtime = _get_runtime_services()
    _session_service = runtime.get_session_service()
    _set_host_attr("_session_service", _session_service)
    _attach_delivery_listener(_session_service.event_bus)
    return _session_service


def _get_runtime_services():
    """Lazy application container; closing owners cannot be replaced by getters."""
    global _runtime_services
    from src.interfaces.api.runtime import RuntimeServices
    from src.runtime.session.execution import SessionServiceClosingError

    sessions_dir = Path(_host_attr("SESSIONS_DIR", SESSIONS_DIR)).resolve()
    runs_dir = Path(_host_attr("RUNS_DIR", RUNS_DIR))
    if _runtime_services is not None and _runtime_services.closing and not _runtime_services.closed:
        raise SessionServiceClosingError("Application session runtime is still closing")
    if _runtime_services is not None and _runtime_services.sessions_dir != sessions_dir:
        if not _runtime_services.retire_idle():
            raise SessionServiceClosingError("Application session runtime still owns active writes")
    if _runtime_services is None or _runtime_services.closed:
        _runtime_services = RuntimeServices(sessions_dir, runs_dir)
    return _runtime_services


async def _prepare_runtime_services():
    """A later lifespan may rebuild only after the old close is truly complete."""
    global _runtime_services
    from src.runtime.session.execution import SessionServiceClosingError

    if _runtime_services is not None and _runtime_services.closing:
        summary = await _runtime_services.aclose(0)
        if not summary.complete:
            raise SessionServiceClosingError("Previous application runtime still owns unfinished writes")
        _reset_closed_runtime()
    return _get_runtime_services()


def _reset_closed_runtime() -> None:
    global _runtime_services, _session_service
    _runtime_services = None
    _session_service = None
    _set_host_attr("_session_service", None)
    reset_channel_runtime()
    # The stopped scheduler closure contains its old dispatch dependencies.
    from src.interfaces.api import scheduled_routes
    scheduled_routes._scheduled_research_executor = None


async def _close_runtime_services(timeout_s: float = 5.0):
    """Close the cached service after inbound sources, retaining incomplete owners."""
    global _runtime_services
    from src.interfaces.api.runtime import RuntimeServices

    service = _host_attr("_session_service", _session_service)
    if _runtime_services is None and service is not None:
        _runtime_services = RuntimeServices(_host_attr("SESSIONS_DIR", SESSIONS_DIR),
                                            _host_attr("RUNS_DIR", RUNS_DIR))
    if _runtime_services is None:
        reset_channel_runtime()
        return None
    if service is not None and _runtime_services.session_service is None:
        # Legacy/test-injected services stay an API-boundary composition seam.
        _runtime_services.session_service = service
    summary = await _runtime_services.aclose(timeout_s)
    if summary.complete:
        _reset_closed_runtime()
    else:
        logging.getLogger(__name__).warning("Session runtime close incomplete: %s", summary)
    return summary


#: Terminal attempt events a finished scheduled briefing can follow.
_DELIVERY_TRIGGER_EVENTS = frozenset(
    {"attempt.completed", "attempt.failed", "attempt.cancelled"}
)


def _attach_delivery_listener(event_bus) -> None:
    """Let a finished run push its briefing without waiting for the next tick.

    The listener only *asks* for a sweep. Delivery correctness lives in the
    periodic sweep and the persisted outbox row; this is latency, and it is
    written so that failing to fire costs nothing but the wait.

    Args:
        event_bus: The session event bus to listen on.
    """

    def _on_event(event) -> None:
        if event.event_type not in _DELIVERY_TRIGGER_EVENTS:
            return
        from src.interfaces.api.scheduled_routes import _get_scheduled_research_executor

        _get_scheduled_research_executor().request_sweep()

    event_bus.add_listener(_on_event)


def _get_channel_runtime():
    """Lazy-init IM channel runtime without starting platform adapters."""
    global _channel_runtime, _channel_bus, _channel_manager

    import sys as _sys
    _host = _sys.modules.get("src.interfaces.api_server")
    if _host is not None and hasattr(_host, "_channel_runtime"):
        host_rt = getattr(_host, "_channel_runtime")
        if host_rt is not None:
            return host_rt
    elif _channel_runtime is not None:
        return _channel_runtime

    from src.interfaces.channels.bus.queue import MessageBus
    from src.interfaces.channels.config import load_channels_config
    from src.interfaces.channels.manager import ChannelManager
    from src.interfaces.channels.runtime import ChannelRuntime

    svc = _get_session_service()
    if not svc:
        raise HTTPException(status_code=501, detail="Session runtime not enabled")

    _channel_bus = MessageBus()
    config = load_channels_config()
    _channel_manager = ChannelManager(config, _channel_bus, session_service=svc)
    global_operators, channel_operators = ChannelRuntime.operators_from_config(config)
    _channel_runtime = ChannelRuntime(
        bus=_channel_bus,
        session_service=svc,
        manager=_channel_manager,
        reply_timeout_s=config["reply_timeout_s"],
        operators=global_operators,
        channel_operators=channel_operators,
    )
    _set_host_attr("_channel_runtime", _channel_runtime)
    _set_host_attr("_channel_bus", _channel_bus)
    _set_host_attr("_channel_manager", _channel_manager)
    return _channel_runtime


def reset_channel_runtime() -> None:
    """Drop the cached IM channel runtime so the next access rebuilds from disk.

    ``_get_channel_runtime`` caches its singletons in this module's globals
    *and* on the ``api_server`` host attrs; clearing both — with ``None``,
    which is what makes the next call rebuild — is what lets an edited channel
    config apply without a process restart.
    """
    global _channel_runtime, _channel_bus, _channel_manager

    _channel_runtime = None
    _channel_bus = None
    _channel_manager = None
    _set_host_attr("_channel_runtime", None)
    _set_host_attr("_channel_bus", None)
    _set_host_attr("_channel_manager", None)
