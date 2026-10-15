"""get_research_reports (MCP) must forward beginTime/endTime.

ResearchReportsTool documents beginTime/endTime as an optional publish-date
window (defaulting to a trailing two years) and enforces it against the real
Eastmoney endpoint. The MCP wrapper dropped both parameters on the way in, so
an MCP client asking for reports in a specific historical window always got
the default trailing-two-year window instead, the same drift class PR #1131
and #1138 fixed elsewhere in this file.
"""

from __future__ import annotations

import inspect
from unittest.mock import patch

import src.interfaces.mcp_server as mcp_server
from src.runtime.tools import research_reports_tool as rrt
from src.runtime.tools.research_reports_tool import ResearchReportsTool

_get_reports = getattr(mcp_server.get_research_reports, "fn", None) or getattr(
    mcp_server.get_research_reports, "__wrapped__", mcp_server.get_research_reports
)


def test_get_research_reports_wrapper_matches_tool_contract():
    spec = ResearchReportsTool.parameters
    sig = inspect.signature(_get_reports)
    missing = set(spec["properties"]) - set(sig.parameters)
    assert not missing, f"MCP wrapper is missing parameters the tool declares: {missing}"


def test_mcp_get_research_reports_forwards_the_date_window():
    captured_params: dict = {}

    def _fake_get_json(url, params=None, **kwargs):
        captured_params.update(params or {})
        return {"data": []}

    with (
        patch.object(rrt, "get_json", side_effect=_fake_get_json),
        patch.object(rrt, "resolve_secid", return_value="123"),
        patch.object(rrt, "_fetch_consensus_eps", return_value=[]),
    ):
        _get_reports(code="600519.SH", beginTime="20200101", endTime="20201231")

    assert (
        captured_params.get("beginTime") == "20200101"
    ), f"beginTime was not forwarded through the MCP wrapper: {captured_params}"
    assert (
        captured_params.get("endTime") == "20201231"
    ), f"endTime was not forwarded through the MCP wrapper: {captured_params}"
