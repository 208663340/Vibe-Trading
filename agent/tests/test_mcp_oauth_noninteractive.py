"""Unit tests for the non-interactive OAuth guard on ``MCPServerAdapter``.

Covers:

* ``MCPServerAdapter(..., interactive_oauth=False)`` builds an OAuth provider
  whose ``redirect_handler`` raises instead of opening a browser, and never
  performs the pre-flight HTTP probe either.
* The default (``interactive_oauth=True``) keeps ``fastmcp``'s own behaviour:
  pre-flight the authorization URL, then hand it to the browser.
"""

from __future__ import annotations

import asyncio
import webbrowser

import pytest
from fastmcp.client.auth import OAuth

from src.platform.config.schema import MCPServerConfig

pytestmark = pytest.mark.unit


class _StubResponse:
    """Minimal stand-in for the pre-flight authorization response."""

    status_code = 302


class _StubHTTPClient:
    """Async-context httpx stand-in that records the pre-flight request."""

    requests: list[str] = []

    async def __aenter__(self) -> "_StubHTTPClient":
        """Enter the async context.

        Returns:
            This client.
        """
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        """Leave the async context.

        Args:
            exc_type: Exception type raised inside the block, if any.
            exc: Exception instance raised inside the block, if any.
            tb: Traceback for the raised exception, if any.

        Returns:
            None.
        """
        return None

    async def get(self, url: str, follow_redirects: bool = False) -> _StubResponse:
        """Record the pre-flight GET and return a redirect response.

        Args:
            url: Authorization URL being probed.
            follow_redirects: Whether the caller wants redirects followed.

        Returns:
            A stub response carrying HTTP 302.
        """
        _StubHTTPClient.requests.append(url)
        return _StubResponse()


class _ExplodingHTTPClient:
    """httpx stand-in that fails loudly if any HTTP call is attempted."""

    async def __aenter__(self) -> "_ExplodingHTTPClient":
        """Fail as soon as an HTTP client is opened.

        Raises:
            AssertionError: Always.
        """
        raise AssertionError("non-interactive OAuth must not make HTTP requests")

    async def __aexit__(self, exc_type, exc, tb) -> None:
        """Unreachable; the context is never entered successfully.

        Args:
            exc_type: Exception type raised inside the block, if any.
            exc: Exception instance raised inside the block, if any.
            tb: Traceback for the raised exception, if any.

        Returns:
            None.
        """
        return None


def _oauth_provider(
    tmp_path,
    *,
    interactive: bool,
    server_name: str = "robinhood",
    url: str = "https://agent.robinhood.com/mcp/trading",
) -> OAuth:
    """Build the OAuth provider an adapter would attach to its transport.

    Args:
        tmp_path: Pytest tmp dir used as the OAuth token cache root.
        interactive: Value for the adapter's ``interactive_oauth`` flag.

    Returns:
        The ``OAuth`` instance wired onto the streamable-HTTP transport.
    """
    from src.runtime.tools.mcp import MCPServerAdapter

    cfg = MCPServerConfig.model_validate(
        {
            "type": "streamableHttp",
            "url": url,
            "auth": {
                "type": "oauth",
                "scopes": ["trading.read"],
                "client_name": "Vibe-Trading",
                "cache_dir": str(tmp_path / "oauth"),
                "client_id": "client-id",
            },
        }
    )
    adapter = MCPServerAdapter(server_name, cfg, interactive_oauth=interactive)
    auth = adapter._build_client().transport.auth
    assert isinstance(auth, OAuth)
    return auth


def test_noninteractive_adapter_refuses_to_open_a_browser(tmp_path, monkeypatch) -> None:
    def _boom(url: str) -> bool:
        raise AssertionError(f"webbrowser.open must not be called, got {url}")

    monkeypatch.setattr(webbrowser, "open", _boom)

    auth = _oauth_provider(tmp_path, interactive=False)
    auth.httpx_client_factory = _ExplodingHTTPClient

    with pytest.raises(RuntimeError, match="connect/reconnect"):
        asyncio.run(auth.redirect_handler("https://agent.robinhood.com/oauth2/authorize?x=1"))


def test_noninteractive_ibkr_adapter_refuses_to_open_a_browser(tmp_path) -> None:
    auth = _oauth_provider(
        tmp_path,
        interactive=False,
        server_name="ibkr",
        url="https://api.ibkr.com/v1/api/mcp-public",
    )
    auth.httpx_client_factory = _ExplodingHTTPClient

    with pytest.raises(RuntimeError, match="connect/reconnect"):
        asyncio.run(auth.redirect_handler("https://api.ibkr.com/oauth2/authorize?x=1"))


def test_interactive_adapter_keeps_default_browser_flow(tmp_path, monkeypatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(webbrowser, "open", opened.append)
    _StubHTTPClient.requests = []

    auth = _oauth_provider(tmp_path, interactive=True)
    auth.httpx_client_factory = _StubHTTPClient

    url = "https://agent.robinhood.com/oauth2/authorize?x=1"
    asyncio.run(auth.redirect_handler(url))

    # The URL is passed through untouched: no provider-specific rewriting.
    assert opened == [url]
    assert _StubHTTPClient.requests == [url]
