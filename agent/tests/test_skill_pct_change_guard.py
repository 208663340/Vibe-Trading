"""Every ``pct_change`` a skill hands the agent must state its fill policy.

pandas 2.x still defaults ``Series.pct_change`` to ``fill_method='pad'``: a
missing close is forward-filled, so the gap bar reports a **0.0% return** that
never happened and the following bar reports the two-day move as a one-day move.
Both are fabricated observations, not a bias with a known sign -- the rolling
volatility built on them can land either side of the truth depending on the
data. pandas already emits a FutureWarning for the implicit default and removes
it in 3.0, but this repo pins ``pandas>=2.0.0,<3.0.0`` (pyproject.toml), so the
padding is live in every supported version.

Skill files are not inert documentation: the agent reads these examples and
templates and runs what it copies, so a padded ``pct_change`` there becomes a
padded ``pct_change`` in a user's strategy. This guard is repo-wide because the
defect arrived one file at a time (#1397 and #1398 each fixed a single call
site while eleven more sat untouched, two of them in files those same PRs
edited).

Equity-curve returns in ``src/backtest/`` are deliberately out of scope: the engine
builds that series with a value on every bar and ``metrics.py`` documents its
``.fillna(0.0)`` choice at the seam.
"""

from __future__ import annotations

import re
from pathlib import Path

SKILLS_ROOT = Path(__file__).resolve().parents[1] / "src" / "research" / "skills"

# Matches a call and captures its argument list, including the empty one.
_CALL = re.compile(r"\.pct_change\(([^)]*)\)")


def _offenders() -> list[str]:
    """Return ``path:line`` for every pct_change call with no fill policy."""
    found: list[str] = []
    for path in sorted(SKILLS_ROOT.rglob("*")):
        if path.suffix not in {".py", ".md"} or not path.is_file():
            continue
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            for match in _CALL.finditer(line):
                if "fill_method" not in match.group(1):
                    rel = path.relative_to(SKILLS_ROOT.parents[2])
                    found.append(f"{rel}:{lineno}")
    return found


def test_no_skill_relies_on_the_pandas_pct_change_fill_default() -> None:
    offenders = _offenders()
    assert not offenders, (
        "pct_change() without an explicit fill_method pads missing prices into "
        "a fabricated 0.0% return; pass fill_method=None:\n  "
        + "\n  ".join(offenders)
    )


def test_the_guard_can_actually_see_an_offender() -> None:
    """The scan must fail on a padded call, not just on nothing existing."""
    assert [m.group(1) for m in _CALL.finditer("x.pct_change()")] == [""]
    assert [m.group(1) for m in _CALL.finditer("x.pct_change(5)")] == ["5"]
    assert "fill_method" not in _CALL.search("x.pct_change(5)").group(1)
    assert "fill_method" in _CALL.search("x.pct_change(fill_method=None)").group(1)
    assert SKILLS_ROOT.is_dir() and any(SKILLS_ROOT.rglob("*.md"))
