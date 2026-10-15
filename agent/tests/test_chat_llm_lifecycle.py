"""Regression tests for provider HTTP-client ownership."""

from __future__ import annotations

import asyncio

from src.runtime.providers.chat import ChatLLM


class _SyncClient:
    def __init__(self) -> None:
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


class _AsyncClient:
    def __init__(self) -> None:
        self.close_calls = 0

    async def aclose(self) -> None:
        self.close_calls += 1


def _wrapper(adapter: object) -> ChatLLM:
    llm = ChatLLM.__new__(ChatLLM)
    llm._llm = adapter
    return llm


def test_close_leaves_borrowed_langchain_clients_open() -> None:
    """A short-lived wrapper must not close LangChain's shared clients."""
    sync_client = _SyncClient()
    async_client = _AsyncClient()

    class _Adapter:
        _vibe_owned_http_clients: tuple[object, ...] = ()
        root_client = sync_client
        root_async_client = async_client
        client = sync_client

    _wrapper(_Adapter()).close()

    assert sync_client.close_calls == 0
    assert async_client.close_calls == 0


def test_close_releases_only_explicitly_owned_clients() -> None:
    """Proxy-free clients created by Vibe remain instance-owned."""
    sync_client = _SyncClient()
    async_client = _AsyncClient()

    class _Adapter:
        _vibe_owned_http_clients = (sync_client, async_client, sync_client)
        root_client = object()

    _wrapper(_Adapter()).close()

    assert sync_client.close_calls == 1
    assert async_client.close_calls == 1


def test_close_schedules_owned_async_client_inside_running_loop() -> None:
    """The synchronous compatibility API must consume async close methods."""
    async_client = _AsyncClient()

    class _Adapter:
        _vibe_owned_http_clients = (async_client,)

    async def scenario() -> None:
        _wrapper(_Adapter()).close()
        await asyncio.sleep(0)

    asyncio.run(scenario())

    assert async_client.close_calls == 1


def test_aclose_awaits_owned_clients() -> None:
    sync_client = _SyncClient()
    async_client = _AsyncClient()

    class _Adapter:
        _vibe_owned_http_clients = (sync_client, async_client)

    asyncio.run(_wrapper(_Adapter()).aclose())

    assert sync_client.close_calls == 1
    assert async_client.close_calls == 1


def test_native_adapter_without_marker_keeps_legacy_cleanup() -> None:
    """Non-LangChain adapters retain the previous best-effort contract."""
    client = _SyncClient()

    class _Adapter:
        pass

    adapter = _Adapter()
    adapter.root_client = client
    adapter.client = client

    _wrapper(adapter).close()

    assert client.close_calls == 1


def test_close_keeps_langchain_cached_transport_open_for_a_sibling_adapter() -> None:
    """Two default-transport adapters share LangChain's cached httpx clients.

    Closing the wrapper around one of them must leave the other usable: the
    cached client is a process resource, not this instance's. Before the
    ownership marker, ``close()`` closed ``root_client`` and every later
    adapter on the same base URL failed with "client has been closed".
    """
    from src.runtime.providers.llm import ChatOpenAIWithReasoning

    base_url = "https://lifecycle-regression.invalid/v1"
    first = ChatOpenAIWithReasoning(model="m", api_key="sk-test", base_url=base_url)
    second = ChatOpenAIWithReasoning(model="m", api_key="sk-test", base_url=base_url)

    # Premise: the default transports really are shared between instances.
    assert first.root_client._client is second.root_client._client
    assert first.root_async_client._client is second.root_async_client._client

    _wrapper(first).close()

    assert second.root_client._client.is_closed is False
    assert second.root_async_client._client.is_closed is False
