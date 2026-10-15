"""Every version string the project publishes must be the packaged version.

`pyproject.toml` is the source of truth: `agent/src/interfaces/cli/_version.py` derives
`__version__` from it, and the MCP server advertises that. Everything else is
a hand-maintained copy, and each copy that nothing compares is a copy that
rots silently.

Preparing 0.1.14 that is exactly what happened, twice. `agent/SKILL.md` — the
manifest published to ClawHub, which no build step touches because it is not
in the wheel — still advertised 0.1.13, and so did the Docker image's
`org.opencontainers.image.version` label. Both stayed green through the whole
suite. This file exists so the next release cannot repeat it: it enumerates
the declaration sites rather than spot-checking the ones somebody remembered.

The locale check globs the directory, so a newly added language is covered
without anyone remembering to extend this file.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LOCALES_DIR = REPO_ROOT / "frontend" / "src" / "i18n" / "locales"

pytestmark = pytest.mark.unit


def _packaged_version() -> str:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return pyproject["project"]["version"]


def test_skill_manifest_version_matches_pyproject() -> None:
    """`agent/SKILL.md` is the ClawHub manifest and ships outside the wheel."""
    text = (REPO_ROOT / "agent" / "SKILL.md").read_text(encoding="utf-8")
    found = re.search(r"^version:\s*(\S+)\s*$", text, re.M)
    assert found is not None, "SKILL.md declares no version"
    assert found.group(1).strip("\"'") == _packaged_version()


def test_docker_image_label_matches_pyproject() -> None:
    """The OCI version label is what `docker inspect` reports to a user."""
    text = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    found = re.search(r'org\.opencontainers\.image\.version="([^"]+)"', text)
    assert found is not None, "Dockerfile declares no image version label"
    assert found.group(1) == _packaged_version()


def test_frontend_package_version_matches_pyproject() -> None:
    pkg = json.loads((REPO_ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))
    assert pkg["version"] == _packaged_version()


def test_frontend_lockfile_version_matches_pyproject() -> None:
    """Both copies npm keeps of the project's own version, not its deps'."""
    lock = json.loads((REPO_ROOT / "frontend" / "package-lock.json").read_text(encoding="utf-8"))
    assert lock["version"] == _packaged_version()
    assert lock["packages"][""]["version"] == _packaged_version()


def test_every_locale_states_the_packaged_version() -> None:
    """The sidebar version a user reads, in every shipped language.

    Globbed rather than listed: a new locale is covered the day it lands.
    """
    expected = f"v{_packaged_version()}"
    locales = sorted(p for p in LOCALES_DIR.glob("*.json") if p.stem != "tsconfig")
    assert len(locales) >= 7, f"expected at least 7 locales, found {len(locales)}"
    stale = {}
    for path in locales:
        data = json.loads(path.read_text(encoding="utf-8"))
        declared = data.get("app", {}).get("version")
        if declared != expected:
            stale[path.name] = declared
    assert not stale, f"locales not on {expected}: {stale}"


def test_the_layout_test_mock_states_the_packaged_version() -> None:
    """The mocked sidebar version drifts silently otherwise.

    It is fixture data and nothing asserts on it, which is precisely why it
    sat at v0.1.13 while every real declaration moved on.
    """
    path = (
        REPO_ROOT
        / "frontend"
        / "src"
        / "components"
        / "layout"
        / "__tests__"
        / "Layout.test.tsx"
    )
    found = re.search(r'"app\.version":\s*"([^"]+)"', path.read_text(encoding="utf-8"))
    assert found is not None, "Layout.test.tsx no longer mocks app.version"
    assert found.group(1) == f"v{_packaged_version()}"
