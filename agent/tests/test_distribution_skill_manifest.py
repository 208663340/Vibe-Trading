"""Keep the packaged agent skill's capability counts in sync with source."""

from __future__ import annotations

import ast
import re
from pathlib import Path


AGENT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = AGENT_ROOT / "SKILL.md"
SKILLS_DIR = AGENT_ROOT / "src" / "research" / "skills"
ZOOS_DIR = AGENT_ROOT / "src" / "research" / "factors" / "zoo"
PRESETS_DIR = AGENT_ROOT / "src" / "runtime" / "swarm" / "presets"
LOADER_REGISTRY_PATH = AGENT_ROOT / "src" / "backtest" / "loaders" / "registry.py"
MCP_SERVER_PATH = AGENT_ROOT / "src" / "interfaces" / "mcp_server.py"


def _manifest_text() -> str:
    return MANIFEST_PATH.read_text(encoding="utf-8")


def _literal_assignment(path: Path, name: str) -> object:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == name
        ):
            return ast.literal_eval(node.value)
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in {path}")


def _live_mcp_tool_count() -> int:
    """Return how many tools the MCP server actually serves.

    Returns:
        Length of ``mcp.list_tools()`` — decorator-registered wrappers plus
        every mirrored tool.
    """
    import asyncio
    import sys

    if str(AGENT_ROOT) not in sys.path:
        sys.path.insert(0, str(AGENT_ROOT))
    import src.interfaces.mcp_server as mcp_server

    return len(asyncio.run(mcp_server.mcp.list_tools()))


def _assert_all_counts(pattern: str, expected: int) -> None:
    counts = [
        int(value)
        for value in re.findall(pattern, _manifest_text(), flags=re.IGNORECASE)
    ]
    assert counts, f"No manifest count matched {pattern!r}"
    assert set(counts) == {expected}, f"Manifest counts {counts} do not match source count {expected}"


def test_finance_skill_count_matches_bundled_skill_directories() -> None:
    expected = sum(
        1
        for path in SKILLS_DIR.iterdir()
        if path.is_dir() and (path / "SKILL.md").is_file()
    )
    _assert_all_counts(r"\b(\d+)\s+(?:finance\s+|specialized\s+)?skills\b", expected)


def test_engine_counts_are_internally_consistent() -> None:
    canonical = re.search(
        r"across\s+(\d+)\s+engines\s*\(",
        _manifest_text(),
        flags=re.IGNORECASE,
    )
    assert canonical, "Manifest is missing the canonical backtest engine count"
    _assert_all_counts(r"\b(\d+)\s+engines\b", int(canonical.group(1)))


def test_alpha_zoo_counts_match_factor_modules() -> None:
    zoo_counts = {
        path.name: sum(1 for module in path.glob("*.py") if module.name != "__init__.py")
        for path in ZOOS_DIR.iterdir()
        if path.is_dir() and path.name != "__pycache__"
    }
    _assert_all_counts(r"Alpha Zoo\D{0,4}(\d+)\s+pre-built", sum(zoo_counts.values()))

    text = _manifest_text()
    for zoo, expected in zoo_counts.items():
        match = re.search(rf"\*\*{re.escape(zoo)}\*\*\s*\((\d+)\s+(?:alphas|factors)\)", text)
        assert match, f"Manifest is missing the {zoo} factor count"
        assert int(match.group(1)) == expected


def test_swarm_preset_count_matches_bundled_yaml_files() -> None:
    expected = sum(1 for path in PRESETS_DIR.glob("*.yaml") if path.is_file())
    _assert_all_counts(r"\b(\d+)\s+(?:multi-agent swarm|pre-built agent) teams\b", expected)


def test_market_data_source_count_matches_loader_registry() -> None:
    sources = _literal_assignment(LOADER_REGISTRY_PATH, "VALID_SOURCES")
    assert isinstance(sources, set)
    expected = len(sources - {"auto"})
    _assert_all_counts(r"\b(\d+)\s+market-data sources\b", expected)
    _assert_all_counts(r"across\s+(\d+)\s+sources\b", expected)


def test_mcp_tool_heading_matches_registered_tools() -> None:
    """The manifest heading must match the tools the server actually serves.

    Counting ``@mcp.tool`` decorators counts only the hand-written wrappers and
    silently ignores every tool registered through ``_MIRRORED_TOOL_SOURCES``.
    That proxy is what let the heading sit at 60 while the surface served 64:
    the four institutional-research tools were absent from the published
    manifest and no test could see it. Ask the server.
    """
    _assert_all_counts(r"Available MCP Tools\s*\((\d+)\)", _live_mcp_tool_count())


def test_manifest_python_requirement_matches_pyproject() -> None:
    """The manifest's declared Python range must be the packaged one.

    ``SKILL.md`` is a distribution manifest: consumers read the ``python:``
    field to decide whether the package is installable. Nothing else compared
    it against ``pyproject.toml``, so when the ``<3.14`` upper bound was
    dropped from packaging the manifest silently kept advertising it.
    """
    import tomllib

    pyproject = tomllib.loads(
        (AGENT_ROOT.parent / "pyproject.toml").read_text(encoding="utf-8")
    )
    packaged = pyproject["project"]["requires-python"].replace(" ", "")
    declared = re.search(
        r'^\s*python:\s*"([^"]+)"', MANIFEST_PATH.read_text(encoding="utf-8"), re.M
    )
    assert declared is not None, "SKILL.md declares no python requirement"
    assert set(declared.group(1).replace(" ", "").split(",")) == set(
        packaged.split(",")
    ), (
        f"SKILL.md says python {declared.group(1)!r} but pyproject.toml "
        f"requires {packaged!r}"
    )
