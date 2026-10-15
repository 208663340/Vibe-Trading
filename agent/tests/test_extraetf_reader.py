"""Tests for the read-only extraETF export reader.

All fixtures are synthetic, shaped after the anonymized exports posted in
HKUDS/Vibe-Trading#1170. No real portfolio data is used.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from src.markets.portfolio.extraetf import (
    ExtraEtfFormatError,
    parse_export,
    read_export,
)

HOLDINGS_COLUMNS = [
    "ISIN",
    "Name",
    "Typ",
    "Anzahl",
    "Kaufpreis",
    "Aktueller Kurs",
    "Aktueller Wert",
    "Währung",
    "Wechselkurs",
    "Region",
    "Land",
    "Sektor",
    "Portfolioname",
    "Portfolio ID",
]

TRANSACTIONS_COLUMNS = [
    "Datum",
    "ISIN",
    "Name",
    "Typ",
    "Transaktion",
    "Anzahl",
    "Preis",
    "Gebühren",
    "Steuern",
    "Währung",
    "Wechselkurs",
    "Portfolioname",
    "Portfolio ID",
    "Stückzinsen",
]

#: A real, checksum-valid ISIN, used so the happy path is realistic.
VALID_ISIN = "US0378331005"
#: The anonymized export's placeholder ISIN, which is shaped like an ISIN but has
#: an invalid check digit.
PLACEHOLDER_ISIN = "IE00AAAA0000"


def _holding(
    *,
    isin: str = VALID_ISIN,
    name: str = "Example World ETF",
    instrument_type: str = "ETF",
    anzahl: str = "12,3456",
    kaufpreis: str = "101,234567",
    kurs: str = "112,345",
    wert: str = "1.386,608",
    waehrung: str = "EUR",
    wechselkurs: str = "1,00",
    region: str = "Welt",
    land: str = "",
    sektor: str = "",
    portfolio: str = "Example broker",
    portfolio_id: str = "1000001",
) -> dict[str, str]:
    return {
        "ISIN": isin,
        "Name": name,
        "Typ": instrument_type,
        "Anzahl": anzahl,
        "Kaufpreis": kaufpreis,
        "Aktueller Kurs": kurs,
        "Aktueller Wert": wert,
        "Währung": waehrung,
        "Wechselkurs": wechselkurs,
        "Region": region,
        "Land": land,
        "Sektor": sektor,
        "Portfolioname": portfolio,
        "Portfolio ID": portfolio_id,
    }


def _movement(
    *,
    datum: str = "01.09.2026",
    isin: str = VALID_ISIN,
    name: str = "Example World ETF",
    instrument_type: str = "ETF",
    transaktion: str = "Kauf",
    anzahl: str = "1,2345",
    preis: str = "110,123456",
    gebuehren: str = "0,00",
    steuern: str = "0,00",
    waehrung: str = "EUR",
    wechselkurs: str = "1,00",
    portfolio: str = "Example broker",
    portfolio_id: str = "1000001",
    stueckzinsen: str = "0",
) -> dict[str, str]:
    return {
        "Datum": datum,
        "ISIN": isin,
        "Name": name,
        "Typ": instrument_type,
        "Transaktion": transaktion,
        "Anzahl": anzahl,
        "Preis": preis,
        "Gebühren": gebuehren,
        "Steuern": steuern,
        "Währung": waehrung,
        "Wechselkurs": wechselkurs,
        "Portfolioname": portfolio,
        "Portfolio ID": portfolio_id,
        "Stückzinsen": stueckzinsen,
    }


def _export(columns: list[str], rows: list[dict[str, str]]) -> str:
    lines = [";".join(columns)]
    lines.extend(";".join(row[column] for column in columns) for row in rows)
    return "\n".join(lines) + "\n"


def _holdings_export(*rows: dict[str, str]) -> str:
    return _export(HOLDINGS_COLUMNS, list(rows))


def _transactions_export(*rows: dict[str, str]) -> str:
    return _export(TRANSACTIONS_COLUMNS, list(rows))


def test_holdings_export_normalizes_a_german_locale_position():
    export = parse_export(_holdings_export(_holding()))

    assert export.kind == "holdings"
    (position,) = export.require_positions()
    assert position["instrument_id"] == VALID_ISIN
    assert position["instrument_id_kind"] == "isin"
    assert position["instrument_id_checksum_ok"] is True
    assert position["symbol"] == VALID_ISIN
    assert position["broker"] == "extraetf"
    assert position["quantity"] == pytest.approx(12.3456)
    assert position["cost_price"] == pytest.approx(101.234567)
    assert position["market_price"] == pytest.approx(112.345)
    assert position["source_market_value"] == pytest.approx(1386.608)
    assert position["currency"] == "EUR"
    assert position["price_currency"] == "EUR"
    assert position["asset_type"] == "etf"
    assert position["instrument_type"] == "ETF"
    assert position["portfolio_id"] == "1000001"
    assert position["portfolio_name"] == "Example broker"
    assert position["region"] == "Welt"
    assert position["country"] is None
    assert position["sector"] is None


def test_german_separators_are_not_read_as_us_numbers():
    """``1.386,608`` is 1386.608, not 1.386 and not 1386608."""
    export = parse_export(
        _holdings_export(
            _holding(
                anzahl="12,3456",
                kaufpreis="1.386,608",
                kurs="59.999,99",
                wert="1.386.608,42",
                wechselkurs="1,0855",
            )
        )
    )

    (position,) = export.require_positions()
    assert position["quantity"] == pytest.approx(12.3456)
    assert position["cost_price"] == pytest.approx(1386.608)
    assert position["market_price"] == pytest.approx(59999.99)
    assert position["source_market_value"] == pytest.approx(1386608.42)
    assert position["fx_rate"] == pytest.approx(1.0855)


def test_us_formatted_number_is_refused_rather_than_misread():
    with pytest.raises(ExtraEtfFormatError, match="US-formatted"):
        parse_export(_holdings_export(_holding(kaufpreis="1,234.56")))


@pytest.mark.parametrize("ambiguous", ["1.23", "1234.56", "0.5"])
def test_ambiguous_separator_grouping_is_refused(ambiguous):
    """A period that is not a thousands separator cannot be guessed at."""
    with pytest.raises(ExtraEtfFormatError, match="ambiguous separators"):
        parse_export(_holdings_export(_holding(kurs=ambiguous)))


@pytest.mark.parametrize("unreadable", ["zwei", "10,00 EUR", "--", ","])
def test_unreadable_number_is_refused(unreadable):
    with pytest.raises(ExtraEtfFormatError, match="is not a number|ambiguous separators"):
        parse_export(_holdings_export(_holding(anzahl=unreadable)))


def test_blank_optional_fields_are_none_rather_than_zero():
    export = parse_export(_holdings_export(_holding(kaufpreis="", kurs="", wert="", wechselkurs="")))

    (position,) = export.require_positions()
    assert position["cost_price"] is None
    assert position["market_price"] is None
    assert position["source_market_value"] is None
    assert position["fx_rate"] is None


def test_transactions_export_yields_movements_and_never_positions():
    """A transactions export is a movement history, not a portfolio."""
    export = parse_export(
        _transactions_export(
            _movement(transaktion="Kauf"),
            _movement(transaktion="Verkauf", anzahl="2,5000", preis="19,876543"),
            _movement(
                transaktion="Dividende",
                anzahl="0,5000",
                preis="2,345",
                steuern="0,45",
            ),
            _movement(transaktion="Einbuchung", anzahl="5,0000", preis="95,000000"),
            _movement(transaktion="Ausbuchung", anzahl="0,5000", preis="90,000000"),
        )
    )

    assert export.kind == "transactions"
    with pytest.raises(ExtraEtfFormatError, match="movements, not positions"):
        export.require_positions()
    movements = export.require_movements()
    assert movements[0]["movement"] == "Kauf"
    assert movements[0]["movement_kind"] == "buy"
    assert [m["movement_kind"] for m in movements] == [
        "buy",
        "sell",
        "dividend",
        "transfer_in",
        "transfer_out",
    ]
    # A dividend row carries a quantity of its own; it must never become a holding.
    assert movements[2]["quantity"] == pytest.approx(0.5)
    assert movements[2]["taxes"] == pytest.approx(0.45)
    assert all("asset_type" not in movement for movement in movements)
    assert all("accrued_interest" in movement for movement in movements)
    assert movements[0]["date"] == "2026-09-01"
    assert movements[0]["accrued_interest"] == pytest.approx(0.0)


def test_a_transactions_export_refuses_to_pose_as_a_portfolio():
    export = parse_export(_transactions_export(_movement(transaktion="Kauf")))

    with pytest.raises(ExtraEtfFormatError, match="movements, not positions"):
        export.require_positions()


def test_unknown_transaction_label_is_refused():
    """A movement whose label is dropped is a movement that means nothing.

    Carried through with ``movement_kind=None``, a ``Steuer`` row would land in
    the same bucket as every other unrecognised label, where a dividend, a fee
    and a cash movement are indistinguishable from each other.
    """
    with pytest.raises(ExtraEtfFormatError, match="none of the labels this format documents"):
        parse_export(_transactions_export(_movement(transaktion="Steuer")))


def test_a_movement_without_an_instrument_is_refused():
    """A blank ISIN is a cash or fee row, not a movement this reader can place.

    Allowing it produced a movement with ``instrument_id=None`` — a movement
    attributed to no instrument, which is not a position and not a trade.
    """
    for blank in ("", "   "):
        with pytest.raises(ExtraEtfFormatError, match="ISIN column is empty"):
            parse_export(_transactions_export(_movement(isin=blank)))


def test_a_bare_symbol_movement_is_still_read():
    """The crypto transactions shape carries its own symbol, not an ISIN.

    This is the documented shape for the Bitpanda transactions export, so the
    stricter movement checks must not refuse it.
    """
    export = parse_export(_transactions_export(_movement(isin="BTC", transaktion="Kauf")))

    (movement,) = export.require_movements()
    assert movement["instrument_id"] == "BTC"
    assert movement["instrument_id_kind"] == "raw_symbol"


def test_invalid_transaction_date_is_refused():
    with pytest.raises(ExtraEtfFormatError, match="DD.MM.YYYY"):
        parse_export(_transactions_export(_movement(datum="2026-09-01")))

    with pytest.raises(ExtraEtfFormatError, match="not a real date"):
        parse_export(_transactions_export(_movement(datum="31.02.2026")))


def test_crypto_identity_is_a_pair_not_an_isin():
    export = parse_export(
        _holdings_export(
            _holding(
                isin="BTC_to_EUR",
                name="Bitcoin",
                instrument_type="Währung / Krypto",
                region="Global",
                sektor="Diversifizierte Kapitalmärkte",
                portfolio="Example crypto wallet",
                portfolio_id="1000002",
            )
        )
    )

    (position,) = export.require_positions()
    assert position["instrument_id"] == "BTC_to_EUR"
    assert position["instrument_id_kind"] == "crypto_pair"
    assert position["instrument_id_checksum_ok"] is None
    # ``Währung / Krypto`` is the class extraETF gives FX pairs as well as crypto,
    # so this reader will not label a position from it: a USD_to_EUR row in this
    # shape is not a crypto holding.
    assert position["asset_type"] is None
    assert position["portfolio_id"] == "1000002"


@pytest.mark.parametrize("identifier", ["BTC", "", "  ", "NOT*AN*ID"])
def test_unattributable_holding_identifier_is_refused(identifier):
    """A bare symbol cannot be resolved, so the import fails instead of guessing."""
    with pytest.raises(ExtraEtfFormatError, match="neither an ISIN nor"):
        parse_export(_holdings_export(_holding(isin=identifier)))


def test_invalid_isin_check_digit_is_reported_not_enforced():
    export = parse_export(_holdings_export(_holding(isin=VALID_ISIN), _holding(isin=PLACEHOLDER_ISIN)))

    valid, placeholder = export.require_positions()
    assert valid["instrument_id_checksum_ok"] is True
    assert placeholder["instrument_id_checksum_ok"] is False


def test_unknown_instrument_type_leaves_asset_type_unset():
    export = parse_export(_holdings_export(_holding(instrument_type="Anleihe")))

    (position,) = export.require_positions()
    assert position["instrument_type"] == "Anleihe"
    assert position["asset_type"] is None


def test_missing_required_field_is_refused():
    for kwarg, message in (
        ("name", "Name is empty"),
        ("instrument_type", "Typ is empty"),
        ("waehrung", "Währung is empty"),
        ("portfolio_id", "Portfolio ID is empty"),
    ):
        with pytest.raises(ExtraEtfFormatError, match=message):
            parse_export(_holdings_export(_holding(**{kwarg: ""})))


def test_invalid_currency_is_refused():
    with pytest.raises(ExtraEtfFormatError, match="three-letter currency"):
        parse_export(_holdings_export(_holding(waehrung="EURO")))


def test_unknown_column_makes_the_export_unrecognisable():
    row = _holding()
    header = ";".join([*HOLDINGS_COLUMNS, "Neue Spalte"])
    body = ";".join([*(row[column] for column in HOLDINGS_COLUMNS), "x"])

    with pytest.raises(ExtraEtfFormatError, match="unexpected columns: \\['Neue Spalte'\\]"):
        parse_export(f"{header}\n{body}\n")


def test_the_refusal_names_both_known_column_sets():
    """#1170 asked for the refusal to name the sets it knows, not only the nearest
    one, so the error says what *is* supported rather than only what is wrong."""
    with pytest.raises(ExtraEtfFormatError) as caught:
        parse_export("Datum;ISIN;Name;Typ;Ausschüttung;Währung;Portfolioname;Portfolio ID\n")

    message = str(caught.value)
    assert "holdings export" in message and "transactions export" in message
    assert "Kaufpreis" in message and "Stückzinsen" in message


def test_a_fiat_pair_is_not_labelled_crypto():
    """``Währung / Krypto`` covers foreign-exchange pairs, so a ``USD_to_EUR`` row
    is described by its shape and given no asset class at all."""
    export = parse_export(
        _holdings_export(_holding(isin="USD_to_EUR", name="US Dollar / Euro", instrument_type="Währung / Krypto"))
    )

    position = export.require_positions()[0]
    assert position["asset_type"] is None
    assert position["instrument_id"] == "USD_to_EUR"


def test_missing_column_makes_the_export_unrecognisable():
    columns = [column for column in HOLDINGS_COLUMNS if column != "Anzahl"]
    row = _holding()

    with pytest.raises(ExtraEtfFormatError, match="missing columns: \\['Anzahl'\\]"):
        parse_export(_export(columns, [row]))


def test_ragged_row_is_refused_rather_than_partially_imported():
    """One malformed row fails the import; it never yields a short portfolio."""
    row = _holding()
    short = ";".join(row[column] for column in HOLDINGS_COLUMNS[:-1])
    long = ";".join([*(row[column] for column in HOLDINGS_COLUMNS), "extra"])

    for ragged in (short, long):
        with pytest.raises(ExtraEtfFormatError, match="cannot be aligned"):
            parse_export(_holdings_export(_holding(), _holding()) + ragged + "\n")


def test_reordered_columns_do_not_mis_align_values():
    """Rows are keyed by column name, so a reordered export stays correct."""
    columns = list(reversed(HOLDINGS_COLUMNS))

    export = parse_export(_export(columns, [_holding()]))

    (position,) = export.require_positions()
    assert position["currency"] == "EUR"
    assert position["quantity"] == pytest.approx(12.3456)
    assert position["name"] == "Example World ETF"
    assert position["portfolio_id"] == "1000001"


def test_a_repeated_instrument_in_one_portfolio_is_refused():
    """Two rows for one instrument under one Portfolio ID cannot be read safely.

    Summing them double-counts the position; keeping one drops the other. Which
    of the two the file meant is not this reader's call, so it refuses instead
    of choosing.
    """
    with pytest.raises(ExtraEtfFormatError, match="listed twice under portfolio"):
        parse_export(_holdings_export(_holding(anzahl="1,0000"), _holding(anzahl="2,0000")))


def test_a_repeated_instrument_names_both_rows():
    """The refusal has to point at the file, not just say no."""
    with pytest.raises(ExtraEtfFormatError, match="row 3.*first at row 2"):
        parse_export(_holdings_export(_holding(), _holding(anzahl="2,0000")))


def test_one_instrument_under_two_portfolios_stays_two_positions():
    """Every export covers all depots, so one ISIN under two Portfolio IDs is
    the normal case rather than a conflict the reader is allowed to resolve."""
    export = parse_export(
        _holdings_export(
            _holding(anzahl="10,0000", portfolio="Example bank", portfolio_id="1000001"),
            _holding(anzahl="25,0000", portfolio="Example broker", portfolio_id="1000002"),
        )
    )

    positions = export.require_positions()
    assert [position["quantity"] for position in positions] == [
        pytest.approx(10.0),
        pytest.approx(25.0),
    ]
    assert [position["portfolio_id"] for position in positions] == ["1000001", "1000002"]
    assert [position["portfolio_name"] for position in positions] == [
        "Example bank",
        "Example broker",
    ]


def test_empty_holdings_export_is_valid_and_not_an_error():
    """An empty portfolio and an unreadable file are different states."""
    export = parse_export(_holdings_export())

    assert export.kind == "holdings"
    assert export.require_positions() == ()


def test_empty_file_is_refused():
    for empty in ("", "\n\n  \n"):
        with pytest.raises(ExtraEtfFormatError, match="export is empty"):
            parse_export(empty)


def test_source_mtime_comes_from_the_file_and_is_labelled(tmp_path):
    path = tmp_path / "holdings.csv"
    path.write_text(_holdings_export(_holding()), encoding="utf-8")
    stamp = 1_760_000_000
    os.utime(path, (stamp, stamp))

    export = read_export(path)

    expected = datetime.fromtimestamp(stamp, tz=timezone.utc).isoformat()
    assert export.as_of_source == "file_mtime"
    assert export.source_mtime == expected
    assert export.source_path == path
    (position,) = export.require_positions()
    # The file's mtime is reported as the file's mtime, and never laundered into
    # the field every connector fills with a real observation time.
    assert position["source_mtime"] == expected
    assert position["updated_at"] is None


def test_bare_text_reports_no_source_mtime():
    export = parse_export(_holdings_export(_holding()))

    assert export.source_mtime is None
    assert export.as_of_source == "unavailable"
    (position,) = export.require_positions()
    assert position["updated_at"] is None
    assert position["source_mtime"] is None


def test_an_absent_source_mtime_cannot_claim_an_origin():
    with pytest.raises(ValueError, match="no source modification time"):
        parse_export(_holdings_export(_holding()), as_of_source="file_mtime")


def test_bom_and_umlauts_decode_from_utf8_and_cp1252(tmp_path):
    document = _holdings_export(_holding())

    utf8_path = tmp_path / "utf8.csv"
    utf8_path.write_bytes(("\ufeff" + document).encode("utf-8"))
    assert read_export(utf8_path).require_positions()[0]["currency"] == "EUR"

    cp1252_path = tmp_path / "cp1252.csv"
    cp1252_path.write_bytes(document.encode("cp1252"))
    assert read_export(cp1252_path).require_positions()[0]["currency"] == "EUR"


def test_missing_file_is_refused(tmp_path):
    with pytest.raises(ExtraEtfFormatError, match="cannot read export"):
        read_export(tmp_path / "absent.csv")


def test_a_record_folded_across_lines_is_refused_rather_than_aligned():
    """A stray quote makes csv fold the next line in; the fold must not land.

    The folded record still has the right field count, so an arity check alone
    passes: one instrument's quantity and value get grafted onto another's
    identity while a whole position disappears. That is a plausible-looking
    portfolio, so the fold is refused. Verified against the unfixed reader,
    which returned 3 positions totalling 3000.0 for this 4-position file.
    """
    document = (
        ";".join(HOLDINGS_COLUMNS) + "\n"
        "US0378331005;Apple Inc.;Aktie;10,0000;150,00;190,00;1.900,00;EUR;1,00;Nordamerika;USA;Technologie;Mein Depot;1000001\n"
        'DE0007100000;"Zwei;ETF;5,0000;100,00;110,00;550,00;EUR;1,00;Welt;;;Mein Depot;1000001\n'
        'IE00B4L5Y983;Three";ETF;7,0000;100,00;110,00;770,00;EUR;1,00;Welt;;;Mein Depot;1000001\n'
        "FR0000131104;Four;ETF;3,0000;100,00;110,00;330,00;EUR;1,00;Welt;;;Mein Depot;1000001\n"
    )

    with pytest.raises(ExtraEtfFormatError, match="spans more than one line"):
        parse_export(document)


def test_a_stray_quote_that_swallows_the_tail_is_refused():
    """An unbalanced quote in the last column absorbs every following line."""
    document = _holdings_export(_holding(), _holding(isin="DE0007100000"))
    document = document.replace("Example broker;1000001", 'Example broker;"1000001', 1)

    with pytest.raises(ExtraEtfFormatError, match="spans more than one line"):
        parse_export(document)


def test_an_over_long_field_is_refused_with_the_documented_error():
    """csv raises its own error for an over-long field; callers must not see it."""
    row = ";".join(["US0378331005", "x" * 140_000, *list(_holding().values())[2:]])

    with pytest.raises(ExtraEtfFormatError, match="not readable as CSV"):
        parse_export(_export(HOLDINGS_COLUMNS, []) + row + "\n")


def test_unrepresentable_precision_is_refused_not_raised_as_decimal_error():
    """A magnitude the wire format cannot carry is a format error, not a crash."""
    with pytest.raises(ExtraEtfFormatError, match="more precision than a position"):
        parse_export(_holdings_export(_holding(anzahl="1" + "0" * 30 + ",0")))


def test_control_characters_in_text_fields_are_refused():
    with pytest.raises(ExtraEtfFormatError, match="control characters"):
        parse_export(_holdings_export(_holding(name="Apple\x00Inc")))

    with pytest.raises(ExtraEtfFormatError, match="control characters"):
        parse_export(_holdings_export(_holding(portfolio="Mein\x1fDepot")))


@pytest.mark.parametrize(
    "identifier",
    [
        # .upper() is neither length- nor ASCII-preserving: each of these maps to a
        # twelve-character upper-cased string matching the ISIN shape, and one of
        # them passes the check digit too. None of them is an ISIN.
        "a\u00df000000001",
        "\u0131\u01310000000001",
        "\ufb01n000000001",
    ],
)
def test_case_mapping_cannot_fabricate_an_isin(identifier):
    """A shape predicate must not be run on a case-folded value.

    Each input is eleven characters that upper-case to a twelve-character string
    matching the ISIN shape, and ``aß000000001`` even matches the check digit. So
    the guard is to refuse them rather than read them as ISINs — on the holdings
    path as unattributable, and on the movements path because they are not a
    symbol either. Neither path rewrites them into a code they are not.
    """
    with pytest.raises(ExtraEtfFormatError, match="neither an ISIN nor"):
        parse_export(_holdings_export(_holding(isin=identifier)))

    with pytest.raises(ExtraEtfFormatError, match="neither an ISIN nor a crypto symbol"):
        parse_export(_transactions_export(_movement(isin=identifier, instrument_type="Fremdwährung")))

    # A value that *is* a plain symbol still reads as itself, unrewritten.
    export = parse_export(_transactions_export(_movement(isin="BTC", instrument_type="Fremdwährung")))
    (movement,) = export.require_movements()
    assert movement["instrument_id_kind"] == "raw_symbol"
    assert movement["instrument_id"] == "BTC"


def test_a_lowercased_isin_is_still_read_as_an_isin():
    """An ISIN is case-insensitive, so a lower-cased one is canonicalised."""
    export = parse_export(_holdings_export(_holding(isin="us0378331005")))

    (position,) = export.require_positions()
    assert position["instrument_id"] == "US0378331005"
    assert position["instrument_id_kind"] == "isin"
    assert position["instrument_id_checksum_ok"] is True


@pytest.mark.parametrize(
    "datum",
    [
        "\uff10\uff11.\uff10\uff19.\uff12\uff10\uff12\uff16",
        "\u0660\u0661.\u0660\u0669.\u0662\u0660\u0662\u0666",
    ],
)
def test_non_ascii_digits_are_refused_in_dates_as_they_are_in_numbers(datum):
    """Unicode digits are accepted by \\d; numbers here are ASCII only."""
    with pytest.raises(ExtraEtfFormatError, match="DD.MM.YYYY"):
        parse_export(_transactions_export(_movement(datum=datum)))


def test_a_blank_row_of_separators_is_refused_not_dropped():
    """An unalignable blank record must not be skipped silently."""
    with pytest.raises(ExtraEtfFormatError, match="blank row of 3 fields"):
        parse_export(_holdings_export(_holding()) + ";;\n")

    # A genuinely empty line is still just the end of a file.
    export = parse_export(_holdings_export(_holding()) + "\n\n  \n")
    assert len(export.require_positions()) == 1


def test_as_of_source_must_be_one_of_the_two_documented_origins():
    with pytest.raises(ValueError, match="as_of_source must be"):
        parse_export(
            _holdings_export(_holding()),
            source_mtime="2026-01-01T00:00:00+00:00",
            as_of_source="nonsense",
        )


def test_a_quantity_that_rounds_away_is_refused_not_silently_zeroed():
    """Quantizing to the wire format is a rounding, and a rounding is a change.

    0,000000004 is not zero in the file. Accepting it produced a position whose
    quantity had become 0.0 — an empty position built out of a real one.
    """
    with pytest.raises(ExtraEtfFormatError, match="more precision than a position"):
        parse_export(_holdings_export(_holding(anzahl="0,000000004")))


def test_a_quantity_beyond_the_wire_precision_is_refused():
    """A ninth decimal place is more than the output can hold, so it is refused
    rather than rounded away."""
    for anzahl in ("1,123456789", "12,345678901"):
        with pytest.raises(ExtraEtfFormatError, match="more precision than a position"):
            parse_export(_holdings_export(_holding(anzahl=anzahl)))


def test_a_quantity_at_the_wire_precision_is_accepted_unchanged():
    """The boundary itself is representable, so it must not be refused."""
    export = parse_export(_holdings_export(_holding(anzahl="1,12345678")))

    (position,) = export.require_positions()
    assert position["quantity"] == pytest.approx(1.12345678)

    # A tiny but representable non-zero quantity survives as itself.
    export = parse_export(_holdings_export(_holding(anzahl="0,00000001")))
    (position,) = export.require_positions()
    assert position["quantity"] > 0


def test_a_bom_followed_by_whitespace_still_reads_the_header():
    export = parse_export("\ufeff " + _holdings_export(_holding()))

    assert len(export.require_positions()) == 1


def test_require_movements_refuses_a_holdings_export():
    export = parse_export(_holdings_export(_holding()))

    with pytest.raises(ExtraEtfFormatError, match="holds positions, not movements"):
        export.require_movements()
    assert len(export.require_positions()) == 1


def test_the_crypto_transactions_shape_from_the_report_is_read():
    """The Bitpanda transactions export identifies crypto by bare symbol only.

    Ground truth from the anonymized samples: the same asset is ``BTC_to_EUR``
    in the holdings export and bare ``BTC`` with ``Typ=Fremdwährung`` in the
    transactions export. Resolving those onto one another is the container's
    job, so the reader reports the divergence rather than inventing a base
    asset: a movement keeps the export's own label and is flagged as an
    unresolved raw symbol.
    """
    document = _export(
        TRANSACTIONS_COLUMNS,
        [
            _movement(
                isin="BTC",
                name="Bitcoin",
                instrument_type="Fremdwährung",
                anzahl="0,001234",
                preis="60.000,00",
                portfolio="Example crypto wallet",
                portfolio_id="1000002",
            ),
            _movement(
                isin="ETH",
                name="Ethereum",
                instrument_type="Fremdwährung",
                transaktion="Einbuchung",
                anzahl="0,100000",
                preis="2.500,00",
                gebuehren="2,50",
                portfolio="Example crypto wallet",
                portfolio_id="1000002",
            ),
        ],
    )

    export = parse_export(document)

    first, second = export.require_movements()
    assert first["instrument_id"] == "BTC"
    assert first["instrument_id_kind"] == "raw_symbol"
    assert first["instrument_id_checksum_ok"] is None
    assert first["quantity"] == pytest.approx(0.001234)
    assert first["price"] == pytest.approx(60000.0)
    assert second["movement_kind"] == "transfer_in"
    assert second["fees"] == pytest.approx(2.5)
    with pytest.raises(ExtraEtfFormatError, match="movements, not positions"):
        export.require_positions()


def test_a_period_grouped_value_is_read_as_german_by_convention():
    """``1.900`` is 1900, because the export is German and nothing says otherwise.

    This reading is a convention of the format rather than a deduction — the
    same bytes could be 1.9 in a US-locale file — so it is pinned here to keep
    the behaviour deliberate. A file round-tripped through a US locale is caught
    by its other values, which stop looking like German thousands.
    """
    export = parse_export(_holdings_export(_holding(kaufpreis="1.900", kurs="110.123", wert="1.900")))

    (position,) = export.require_positions()
    assert position["cost_price"] == pytest.approx(1900.0)
    assert position["market_price"] == pytest.approx(110123.0)
    assert position["source_market_value"] == pytest.approx(1900.0)


def test_reader_imports_only_the_standard_library():
    """No network, no broker SDK, no order path can be reached from here."""
    import ast
    from pathlib import Path

    import src.markets.portfolio.extraetf as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    roots = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module and node.module != "__future__":
            roots.add(node.module.split(".")[0])

    assert roots == {
        "csv",
        "dataclasses",
        "datetime",
        "decimal",
        "io",
        "os",
        "pathlib",
        "re",
        "typing",
    }


def test_a_thousands_group_longer_than_three_digits_is_refused():
    """``1234.567`` is a US decimal; reading its period as German scales it 1000x.

    The leading group is the one the grouping rule used to skip, and it is the
    one that decides whether a bare period is a thousands separator at all —
    German never writes four digits before the first separator.
    """
    for value in ("1234.567", "12345.678", "9999.99"):
        with pytest.raises(ExtraEtfFormatError, match="ambiguous separators"):
            parse_export(_holdings_export(_holding(kurs=value)))

    # The German spelling of the same magnitude is still accepted.
    export = parse_export(_holdings_export(_holding(kurs="1.234,567")))
    assert export.require_positions()[0]["market_price"] == pytest.approx(1234.567)


@pytest.mark.parametrize("column", ["kaufpreis", "kurs", "wert", "wechselkurs"])
def test_extra_precision_is_refused_in_every_holding_number(column):
    """Precision is a property of the number grammar, not of one column."""
    with pytest.raises(ExtraEtfFormatError, match="more precision than a position"):
        parse_export(_holdings_export(_holding(**{column: "1,123456789"})))


@pytest.mark.parametrize("column", ["preis", "gebuehren", "steuern", "stueckzinsen"])
def test_extra_precision_is_refused_in_every_movement_number(column):
    with pytest.raises(ExtraEtfFormatError, match="more precision than a position"):
        parse_export(_transactions_export(_movement(**{column: "1,123456789"})))


def test_a_value_that_would_change_on_the_way_to_a_float_is_refused():
    """``float`` holds about seventeen significant digits, so a value past that
    would arrive with a different magnitude even though it quantized cleanly."""
    with pytest.raises(ExtraEtfFormatError, match="more precision than a position"):
        parse_export(_holdings_export(_holding(anzahl="999999999999999,99999999")))


def test_case_variant_crypto_pairs_are_one_instrument():
    """The pair label is extraETF's own, so it is emitted exactly as exported —
    but two rows differing only in case are one label, and two positions for
    them would double-count."""
    with pytest.raises(ExtraEtfFormatError, match="listed twice under portfolio"):
        parse_export(
            _holdings_export(
                _holding(isin="BTC_to_EUR", instrument_type="Währung / Krypto"),
                _holding(isin="btc_to_eur", instrument_type="Währung / Krypto"),
            )
        )


def test_a_crypto_pair_keeps_its_exported_case():
    """Comparison folds case; the emitted value does not."""
    export = parse_export(_holdings_export(_holding(isin="btc_to_eur", instrument_type="Währung / Krypto")))

    assert export.require_positions()[0]["instrument_id"] == "btc_to_eur"


def test_a_movement_identifier_that_is_not_a_symbol_is_refused():
    """A movement may carry a bare crypto symbol, but not arbitrary text."""
    for identifier in ("!!!", "hello world", "..", "BTC EUR"):
        with pytest.raises(ExtraEtfFormatError, match="neither an ISIN nor a crypto symbol"):
            parse_export(_transactions_export(_movement(isin=identifier)))


def test_del_and_c1_characters_are_refused_in_text_and_identity_fields():
    """Control characters are corruption, not content — DEL and C1 included."""
    for value in ("A\x7fB", "A\x85B", "A\x1fB", "A\x00B"):
        with pytest.raises(ExtraEtfFormatError, match="carries control characters"):
            parse_export(_holdings_export(_holding(name=value)))
        with pytest.raises(ExtraEtfFormatError, match="carries control characters"):
            parse_export(_holdings_export(_holding(isin=value)))


def test_an_unavailable_origin_cannot_carry_a_timestamp():
    """Provenance has to agree with itself in both directions."""
    with pytest.raises(ValueError, match="must agree"):
        parse_export(
            _holdings_export(_holding()),
            source_mtime="2026-01-01T00:00:00+00:00",
            as_of_source="unavailable",
        )


def test_transaction_labels_are_matched_ignoring_case_and_padding():
    """The label is German prose rather than a canonical enum, so it is matched
    case-insensitively, and a padded cell is stripped at the read boundary —
    while whatever the file did say is what the movement reports."""
    for label, expected in (("kauf", "buy"), ("KAUF", "buy"), ("Kauf ", "buy"), ("Dividende", "dividend")):
        export = parse_export(_transactions_export(_movement(transaktion=label)))
        movement = export.require_movements()[0]
        assert movement["movement_kind"] == expected
        assert movement["movement"] == label.strip()
