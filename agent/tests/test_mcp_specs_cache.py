"""Unit tests for MCP tool discovery specs cache.

Tests verify that :func:`build_mcp_tool_wrappers` caches tool specs to
avoid redundant ``list_tools`` RPC calls across Swarm workers.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

import pytest

from src.runtime.tools.mcp import (
    MCPRemoteToolSpec,
    MCPServerAdapter,
    _MCP_SPECS_CACHE,
    _make_cache_key,
    build_mcp_tool_wrappers,
    invalidate_mcp_specs_cache,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    """Ensure each test starts with a clean cache."""
    invalidate_mcp_specs_cache()
    yield
    invalidate_mcp_specs_cache()


def _make_server_config(
    command="mcp-server",
    args=None,
    env=None,
    enabled_tools=None,
    type_=None,
    url=None,
    headers=None,
    auth=None,
):
    """Create a minimal MCPServerConfig-like object for testing."""
    config = MagicMock()
    config.command = command
    config.args = args or ["--port", "8080"]
    config.env = env or {}
    config.enabled_tools = enabled_tools or ["*"]
    config.type = type_
    config.url = url
    config.tool_timeout = 30.0
    config.init_timeout = None
    config.headers = headers or {}
    config.auth = auth
    return config


def _make_oauth_config(
    client_name="Vibe-Trading",
    scopes=None,
    cache_dir="~/.vibe-trading/oauth",
    callback_port=None,
    client_id=None,
    client_secret=None,
    client_metadata_url=None,
):
    """Create a minimal MCPOAuthConfig-like object for testing."""
    auth = MagicMock()
    auth.type = "oauth"
    auth.scopes = scopes or []
    auth.client_name = client_name
    auth.cache_dir = cache_dir
    auth.callback_port = callback_port
    auth.client_id = client_id
    auth.client_secret = client_secret
    auth.client_metadata_url = client_metadata_url
    return auth


def _make_specs(server_name: str, tool_names: list[str]) -> list[MCPRemoteToolSpec]:
    """Build a list of fake MCPRemoteToolSpec for testing."""
    return [
        MCPRemoteToolSpec(
            server_name=server_name,
            remote_name=name,
            local_name=f"mcp_{server_name}_{name}",
            description=f"Tool {name}",
            parameters={"type": "object", "properties": {}, "required": []},
            annotations=None,
        )
        for name in tool_names
    ]


class TestMakeCacheKey:
    """Tests for _make_cache_key determinism and isolation."""

    def test_same_config_produces_same_key(self):
        """Identical server_name and config should yield the same cache key."""
        config = _make_server_config()
        key1 = _make_cache_key("srv", config)
        key2 = _make_cache_key("srv", config)
        assert key1 == key2

    def test_different_server_name_produces_different_key(self):
        """Different server_name should produce different cache keys."""
        config = _make_server_config()
        key1 = _make_cache_key("srv1", config)
        key2 = _make_cache_key("srv2", config)
        assert key1 != key2

    def test_different_command_produces_different_key(self):
        """Different command should produce different cache keys."""
        config1 = _make_server_config(command="cmd-a")
        config2 = _make_server_config(command="cmd-b")
        key1 = _make_cache_key("srv", config1)
        key2 = _make_cache_key("srv", config2)
        assert key1 != key2

    def test_different_args_produces_different_key(self):
        """Different args should produce different cache keys."""
        config1 = _make_server_config(args=["--port", "8080"])
        config2 = _make_server_config(args=["--port", "9090"])
        key1 = _make_cache_key("srv", config1)
        key2 = _make_cache_key("srv", config2)
        assert key1 != key2

    def test_different_env_produces_different_key(self):
        """Different env should produce different cache keys."""
        config1 = _make_server_config(env={"KEY": "val1"})
        config2 = _make_server_config(env={"KEY": "val2"})
        key1 = _make_cache_key("srv", config1)
        key2 = _make_cache_key("srv", config2)
        assert key1 != key2

    def test_different_url_produces_different_key(self):
        """Two HTTP configs differing only in url must not share a cache entry."""
        config1 = _make_server_config(
            type_="sse", url="https://staging.example.com/mcp"
        )
        config2 = _make_server_config(type_="sse", url="https://prod.example.com/mcp")
        key1 = _make_cache_key("srv", config1)
        key2 = _make_cache_key("srv", config2)
        assert key1 != key2

    def test_different_headers_produces_different_key(self):
        """Two HTTP configs differing only in a header value must not share a cache entry."""
        config1 = _make_server_config(
            type_="sse",
            url="https://example.com/mcp",
            headers={"Authorization": "Bearer token-a"},
        )
        config2 = _make_server_config(
            type_="sse",
            url="https://example.com/mcp",
            headers={"Authorization": "Bearer token-b"},
        )
        key1 = _make_cache_key("srv", config1)
        key2 = _make_cache_key("srv", config2)
        assert key1 != key2

    def test_raw_header_secret_not_present_in_key(self):
        """A secret header value must never appear as plaintext in the cache key."""
        secret = "super-secret-bearer-token"
        config = _make_server_config(
            type_="sse",
            url="https://example.com/mcp",
            headers={"Authorization": f"Bearer {secret}"},
        )
        key = _make_cache_key("srv", config)
        assert all(secret not in str(part) for part in key)

    def test_headers_in_different_insertion_order_produce_same_key(self):
        """Header fingerprinting must canonicalize order, not hash insertion order."""
        config1 = _make_server_config(
            type_="sse",
            url="https://example.com/mcp",
            headers={"Authorization": "x", "X-Tenant": "a"},
        )
        config2 = _make_server_config(
            type_="sse",
            url="https://example.com/mcp",
            headers={"X-Tenant": "a", "Authorization": "x"},
        )
        key1 = _make_cache_key("srv", config1)
        key2 = _make_cache_key("srv", config2)
        assert key1 == key2

    def test_different_oauth_config_produces_different_key(self):
        """Two configs differing only in OAuth client secret must not share a cache entry."""
        config1 = _make_server_config(
            type_="sse",
            url="https://example.com/mcp",
            auth=_make_oauth_config(client_secret="secret-a"),
        )
        config2 = _make_server_config(
            type_="sse",
            url="https://example.com/mcp",
            auth=_make_oauth_config(client_secret="secret-b"),
        )
        key1 = _make_cache_key("srv", config1)
        key2 = _make_cache_key("srv", config2)
        assert key1 != key2

    def test_raw_oauth_client_secret_not_present_in_key(self):
        """An OAuth client secret must never appear as plaintext in the cache key."""
        secret = "super-secret-oauth-client-secret"
        config = _make_server_config(
            type_="sse",
            url="https://example.com/mcp",
            auth=_make_oauth_config(client_secret=secret),
        )
        key = _make_cache_key("srv", config)
        assert all(secret not in str(part) for part in key)

    def test_different_transport_type_produces_different_key(self):
        """Same url on a different transport type must not share a cache entry."""
        config1 = _make_server_config(type_="sse", url="https://example.com/mcp")
        config2 = _make_server_config(
            type_="streamableHttp", url="https://example.com/mcp"
        )
        key1 = _make_cache_key("srv", config1)
        key2 = _make_cache_key("srv", config2)
        assert key1 != key2

    def test_identical_http_config_produces_identical_key(self):
        """Two separately-built but equal HTTP configs must produce the same key."""
        config1 = _make_server_config(
            type_="sse",
            url="https://example.com/mcp",
            headers={"X-Tenant": "a"},
            auth=_make_oauth_config(client_id="abc"),
        )
        config2 = _make_server_config(
            type_="sse",
            url="https://example.com/mcp",
            headers={"X-Tenant": "a"},
            auth=_make_oauth_config(client_id="abc"),
        )
        key1 = _make_cache_key("srv", config1)
        key2 = _make_cache_key("srv", config2)
        assert key1 == key2


class TestMCPSpecsCache:
    """Tests for the MCP tool discovery specs cache."""

    def test_cache_hit_avoids_repeated_rpc(self):
        """Second call to build_mcp_tool_wrappers should use cached specs."""
        config = _make_server_config()
        fake_specs = _make_specs("srv1", ["tool_a", "tool_b"])

        with patch.object(MCPServerAdapter, "discover_tools", return_value=fake_specs) as mock_discover:
            # First call — cache miss, triggers discover_tools
            tools1 = build_mcp_tool_wrappers("srv1", config, client_factory=None)
            assert mock_discover.call_count == 1
            assert len(tools1) == 2

            # Second call — cache hit, no additional RPC
            tools2 = build_mcp_tool_wrappers("srv1", config, client_factory=None)
            assert mock_discover.call_count == 1
            assert len(tools2) == 2

    def test_cache_key_isolation(self):
        """Different server_name should have separate cache entries."""
        config = _make_server_config()
        specs_a = _make_specs("srv_a", ["tool_x"])
        specs_b = _make_specs("srv_b", ["tool_y", "tool_z"])

        with patch.object(MCPServerAdapter, "discover_tools") as mock_discover:
            mock_discover.return_value = specs_a
            tools_a = build_mcp_tool_wrappers("srv_a", config, client_factory=None)

            mock_discover.return_value = specs_b
            tools_b = build_mcp_tool_wrappers("srv_b", config, client_factory=None)

            # Both should have called discover_tools (different cache keys)
            assert mock_discover.call_count == 2
            assert len(tools_a) == 1
            assert len(tools_b) == 2

    def test_thread_safety(self):
        """Concurrent calls from multiple threads should not raise."""
        config = _make_server_config()
        fake_specs = _make_specs("srv_thread", ["tool_t"])
        errors: list[Exception] = []

        with patch.object(MCPServerAdapter, "discover_tools", return_value=fake_specs):

            def worker(idx: int):
                try:
                    # Use a unique server name per thread to stress the write path
                    build_mcp_tool_wrappers(f"srv_{idx}", config, client_factory=None)
                except Exception as exc:
                    errors.append(exc)

            with ThreadPoolExecutor(max_workers=8) as executor:
                futures = [executor.submit(worker, i) for i in range(20)]
                for f in futures:
                    f.result()

        assert errors == [], f"Thread safety violation: {errors}"

    def test_thread_safety_same_key(self):
        """Concurrent calls with the same cache key should not raise or corrupt."""
        config = _make_server_config()
        fake_specs = _make_specs("srv_same", ["tool_s"])
        call_count = 0
        lock = threading.Lock()

        def counting_discover(self_adapter):
            nonlocal call_count
            with lock:
                call_count += 1
            return fake_specs

        with patch.object(MCPServerAdapter, "discover_tools", counting_discover):

            def worker():
                build_mcp_tool_wrappers("srv_same", config, client_factory=None)

            with ThreadPoolExecutor(max_workers=8) as executor:
                futures = [executor.submit(worker) for _ in range(20)]
                for f in futures:
                    f.result()

        # At least one call must have happened; due to races, more than one
        # thread may execute discover_tools before any writes to cache.
        assert call_count >= 1

    def test_client_factory_bypasses_cache(self):
        """When client_factory is provided, cache is not used."""
        config = _make_server_config()
        fake_specs = _make_specs("srv_cf", ["tool_cf"])

        # Pre-fill cache to prove it's not consulted
        cache_key = _make_cache_key("srv_cf", config)
        _MCP_SPECS_CACHE[cache_key] = _make_specs("srv_cf", ["cached_tool"])

        # A custom client_factory (non-None) should bypass the cache
        with patch.object(MCPServerAdapter, "discover_tools", return_value=fake_specs) as mock_discover:
            dummy_factory = MagicMock()
            tools = build_mcp_tool_wrappers("srv_cf", config, client_factory=dummy_factory)
            # discover_tools is called despite cache being populated
            assert mock_discover.call_count == 1
            assert len(tools) == 1
            assert tools[0].name == "mcp_srv_cf_tool_cf"

    def test_client_factory_does_not_write_cache(self):
        """When client_factory is provided, results are not stored in cache."""
        config = _make_server_config()
        fake_specs = _make_specs("srv_no_write", ["tool_nw"])

        with patch.object(MCPServerAdapter, "discover_tools", return_value=fake_specs):
            dummy_factory = MagicMock()
            build_mcp_tool_wrappers("srv_no_write", config, client_factory=dummy_factory)

        cache_key = _make_cache_key("srv_no_write", config)
        assert cache_key not in _MCP_SPECS_CACHE

    def test_invalidate_clears_cache(self):
        """invalidate_mcp_specs_cache() should clear all cached entries."""
        config = _make_server_config()
        fake_specs = _make_specs("srv_inv", ["tool_inv"])

        with patch.object(MCPServerAdapter, "discover_tools", return_value=fake_specs) as mock_discover:
            build_mcp_tool_wrappers("srv_inv", config, client_factory=None)
            assert mock_discover.call_count == 1
            assert len(_MCP_SPECS_CACHE) == 1

            # Invalidate and verify cache is empty
            invalidate_mcp_specs_cache()
            assert len(_MCP_SPECS_CACHE) == 0

            # Next call should trigger discover_tools again
            build_mcp_tool_wrappers("srv_inv", config, client_factory=None)
            assert mock_discover.call_count == 2

    def test_exception_not_cached(self):
        """When discover_tools raises, result should not be stored in cache."""
        config = _make_server_config()
        cache_key = _make_cache_key("srv_err", config)

        with patch.object(MCPServerAdapter, "discover_tools", side_effect=RuntimeError("connection failed")):
            with pytest.raises(RuntimeError, match="connection failed"):
                build_mcp_tool_wrappers("srv_err", config, client_factory=None)

        # Cache should remain empty after failure
        assert cache_key not in _MCP_SPECS_CACHE

    def test_exception_does_not_poison_subsequent_success(self):
        """A failed discovery should not prevent a later successful one."""
        config = _make_server_config()
        fake_specs = _make_specs("srv_recover", ["tool_ok"])

        with patch.object(MCPServerAdapter, "discover_tools") as mock_discover:
            # First call fails
            mock_discover.side_effect = RuntimeError("temporary failure")
            with pytest.raises(RuntimeError):
                build_mcp_tool_wrappers("srv_recover", config, client_factory=None)

            # Second call succeeds
            mock_discover.side_effect = None
            mock_discover.return_value = fake_specs
            tools = build_mcp_tool_wrappers("srv_recover", config, client_factory=None)
            assert len(tools) == 1

    def test_cached_specs_produce_valid_tools(self):
        """Tools built from cache should have correct name and description."""
        config = _make_server_config()
        fake_specs = _make_specs("srv_valid", ["alpha", "beta"])

        with patch.object(MCPServerAdapter, "discover_tools", return_value=fake_specs):
            build_mcp_tool_wrappers("srv_valid", config, client_factory=None)

            # Second call (from cache)
            tools = build_mcp_tool_wrappers("srv_valid", config, client_factory=None)

        assert tools[0].name == "mcp_srv_valid_alpha"
        assert tools[1].name == "mcp_srv_valid_beta"
        assert "Tool alpha" in tools[0].description
        assert "Tool beta" in tools[1].description
