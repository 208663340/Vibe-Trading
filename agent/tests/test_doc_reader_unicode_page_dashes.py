"""Unicode dash page ranges must parse like ASCII hyphens, not yield empty."""

from __future__ import annotations

import pytest

from src.runtime.tools.doc_reader_tool import _parse_pages


def test_en_dash_page_range_parses() -> None:
    assert _parse_pages("1\u201310", 20) == list(range(10))


def test_em_dash_page_range_parses() -> None:
    assert _parse_pages("2\u20144", 20) == [1, 2, 3]


def test_minus_sign_page_range_parses() -> None:
    assert _parse_pages("5\u22127", 20) == [4, 5, 6]


def test_ascii_hyphen_still_ok() -> None:
    assert _parse_pages("1-3", 20) == [0, 1, 2]


def test_inverted_en_dash_range_still_raises() -> None:
    with pytest.raises(ValueError, match="inverted page range"):
        _parse_pages("10\u20135", 20)
