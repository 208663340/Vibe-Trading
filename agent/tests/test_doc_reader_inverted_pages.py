"""Inverted PDF page ranges must raise, not silently read nothing."""

from __future__ import annotations

import pytest

from src.runtime.tools.doc_reader_tool import _parse_pages


def test_inverted_page_range_raises() -> None:
    with pytest.raises(ValueError, match="inverted page range"):
        _parse_pages("10-5", 20)


def test_normal_page_range_ok() -> None:
    assert _parse_pages("1-3", 20) == [0, 1, 2]


def test_comma_list_ok() -> None:
    assert _parse_pages("1,3,5", 20) == [0, 2, 4]
