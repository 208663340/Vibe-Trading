"""Read-only reader for extraETF portfolio exports.

extraETF exports two semicolon-delimited, German-locale files:

``holdings`` — the positions of one view at export time::

    ISIN;Name;Typ;Anzahl;Kaufpreis;Aktueller Kurs;Aktueller Wert;Währung;Wechselkurs;Region;Land;Sektor;Portfolioname;Portfolio ID

``transactions`` — the movement history of one view::

    Datum;ISIN;Name;Typ;Transaktion;Anzahl;Preis;Gebühren;Steuern;Währung;Wechselkurs;Portfolioname;Portfolio ID;Stückzinsen

Three properties are deliberate and load-bearing.

**A transactions export can never be read as a portfolio.** Its rows are
movements — ``Kauf``/``Verkauf``/``Dividende``/``Einbuchung``/``Ausbuchung`` —
not holdings. An allocation computed from trades looks perfectly plausible and
is wrong, so the two formats are told apart by their header and positions are
reachable only through :meth:`ExtraEtfExport.require_positions`, which refuses
any other export. Movements carry an explicit ``movement_kind`` so a dividend or
a transfer is never mistaken for a trade.

**A holdings export carries no date.** The format has no snapshot column, so
freshness can only come from the file's own modification time, and the export
exposes it as ``source_mtime``, with ``as_of_source`` recording where it came
from. It is named for what it is — a file's modification time, not an
observation time and not a snapshot date. No position carries an observation
time at all: ``updated_at`` is ``None`` rather than a copy of the file's mtime.
Copying or syncing an export rewrites that mtime, so ``source_mtime`` reports
when the *file* last changed and nothing more.

**Validation fails closed.** A row this reader does not recognise raises
:class:`ExtraEtfFormatError` instead of being skipped: an import computed from
90% of a portfolio reads as entirely plausible, whereas a refused import is
merely annoying. That covers ragged rows, records that span more than one
physical line, unknown or missing columns, unreadable or contradictory numbers,
numbers carrying more precision than the output can hold, empty required
fields, text fields carrying control characters, an instrument listed twice
under one portfolio, and a transaction whose label or identifier cannot be read
as anything this format documents.

The number rule is precise, because it is the one that can change a position's
size by orders of magnitude. A comma is the decimal separator and a period may
only be a thousands separator, so ``1.386,608`` is 1386.608 and ``12,3456`` is
12.3456. A value whose separators are US-ordered (``1,234.56``) is refused,
because reading it as German would be wrong by a thousandfold. A period grouping
that is not a plain thousands separator — a first group longer than three
digits, or a later group that is not exactly three (``1.23``, ``1234.56``,
``1234.567``) — is refused too, because it is neither a valid German number nor
safely a US one. What is *not*
refused is a period-grouped value that is valid German — ``1.900`` is 1900 — and
that reading is a convention of the format, not a deduction: ``1.900`` could be
1.9 in a US-locale file, and no amount of parsing can tell the two apart.
extraETF writes German, so German wins, and a file that had been round-tripped
through a US-locale spreadsheet is caught by the *other* values in it rather
than by this one.

Rows are read by column name, so a reordered export cannot mis-align a value
into the wrong column. Rows are **never** merged or de-duplicated silently. The
same instrument under two different portfolio IDs is two real positions and is
left alone. The same instrument twice under **one** portfolio ID is refused:
the two rows could be one lot listed twice or two genuine lots, and a caller
that sums them double-counts the position while a caller that keeps one drops
the other, so the file is rejected rather than guessed at.

The emitted position uses the portfolio package's own field names so a container
can consume it, and carries the export's remaining columns alongside them. It
does **not** route the row through
:func:`src.markets.portfolio.normalization.normalize_position`, which is the obvious way
to guarantee one wire shape, because that function stamps ``updated_at`` with
the current time — an observation time this reader never made — and defaults an
unknown instrument class to ``"stock"``. Both are fabrications a file import
must not make, so the mapping is done here and the divergences are shown above:
``quote_symbol``'s None, ``asset_type``'s None, ``market``'s None (the
connector path stamps the broker there), and ``updated_at`` against
``source_mtime``.

Values are carried as the export reports them rather than repaired: a zero or
negative quantity, a zero FX rate, and two different portfolio names under one
portfolio ID all pass through, because a reader that silently corrects its input
is a reader whose output no longer describes the file. What is refused is input
that cannot be read at all.

Two limits are inherent to the format and belong in the caller's hands. A file
truncated exactly at a record boundary is indistinguishable from a short export,
because neither format carries a row count or a footer; a caller that knows how
many positions to expect is the only thing that can catch that. An export with a
header and no rows is read as a valid empty portfolio, because an empty
portfolio and an unreadable file are different states.

The module writes nothing, holds no credentials, and touches no network. It
only reads a local file the user exported themselves, and it has no path to any
order surface.
"""

from __future__ import annotations

import csv
import io
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal

BROKER = "extraetf"

_HOLDINGS_COLUMNS: tuple[str, ...] = (
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
)

_TRANSACTIONS_COLUMNS: tuple[str, ...] = (
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
)

ExportKind = Literal["holdings", "transactions"]

#: Case-insensitive so an otherwise valid lower-cased ISIN is still read, and
#: ASCII-anchored with an exact twelve-character length so ``.upper()`` is only
#: ever applied to a value that already has ISIN shape. ``.upper()`` is neither
#: length- nor ASCII-preserving — it turns ``ß`` into ``SS`` — so using it as the
#: predicate invents twelve-character ISINs out of eleven-character inputs.
_ISIN_PATTERN = re.compile(r"^[A-Za-z]{2}[A-Za-z0-9]{9}[0-9]$")
_WIRE_QUANTUM = Decimal("0.00000001")
_CRYPTO_PAIR_PATTERN = re.compile(r"^[A-Z0-9]{2,12}_to_[A-Z0-9]{2,12}$", re.IGNORECASE)
_CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")
_DATE_PATTERN = re.compile(r"^([0-9]{2})\.([0-9]{2})\.([0-9]{4})$")
_NUMERAL_PATTERN = re.compile(r"^[0-9.,]+$")
#: The bare-symbol shape a crypto transactions row carries in its ``ISIN``
#: column (e.g. ``BTC``). A holdings row must carry an ISIN or a pair label, so
#: this shape is only accepted for movements — but there it is accepted, and
#: anything outside it is refused rather than emitted as an identity.
_SYMBOL_PATTERN = re.compile(r"^[A-Za-z0-9]{1,12}$")

#: ``Typ`` is an instrument class, not a row shape: a class this reader does not
#: know still describes a real position, so it is carried through with
#: ``asset_type`` left ``None`` rather than refused or guessed at. extraETF's
#: ``Währung / Krypto`` class is deliberately absent from this map: it covers
#: foreign-exchange pairs as well as crypto, so a ``USD_to_EUR`` row would end up
#: labelled a crypto holding — a guess about the asset class, which is precisely
#: what leaving the class unset exists to avoid.
_ASSET_TYPE_BY_EXPORT_TYPE = {
    "etf": "etf",
    "aktie": "stock",
}

_MOVEMENT_KIND_BY_TRANSACTION = {
    "kauf": "buy",
    "verkauf": "sell",
    "dividende": "dividend",
    "einbuchung": "transfer_in",
    "ausbuchung": "transfer_out",
}


class ExtraEtfFormatError(ValueError):
    """Raised when an export cannot be read without guessing."""


@dataclass(frozen=True)
class ExtraEtfExport:
    """One parsed export, with its provenance attached.

    The payloads are deliberately private. An export cannot hold both positions
    and movements, and reaching for the wrong one used to yield an empty tuple
    rather than an error — which turns "this is a transactions export" into "this
    portfolio is empty", the exact plausible-and-wrong outcome this module exists
    to prevent. Both accessors refuse a mismatched export instead.

    Attributes:
        kind: Which of the two extraETF formats this file is.
        source_mtime: ISO-8601 modification time of the file this export came
            from, or ``None`` when it was parsed from bare text. It is a file's
            timestamp, not an observation time, and it is named so that nothing
            downstream can read it as a snapshot date.
        as_of_source: Where ``source_mtime`` came from — ``"file_mtime"`` for a
            file read from disk, ``"unavailable"`` for bare text with no
            timestamp. A holdings export has no date column, so ``file_mtime``
            is an inference from the file's own modification time and is
            labelled as one rather than presented as a recorded snapshot date.
        source_path: The file this export was read from, when there was one.
    """

    kind: ExportKind
    source_mtime: str | None
    as_of_source: Literal["file_mtime", "unavailable"]
    source_path: Path | None
    _positions: tuple[dict[str, Any], ...] = ()
    _movements: tuple[dict[str, Any], ...] = ()

    def require_positions(self) -> tuple[dict[str, Any], ...]:
        """Return holdings positions, refusing any other export outright.

        Returns:
            The parsed positions, possibly empty for an empty portfolio.

        Raises:
            ExtraEtfFormatError: If this export is a transactions export, whose
                rows are movements rather than holdings.
        """
        if self.kind != "holdings":
            raise ExtraEtfFormatError(
                "this is a transactions export and holds movements, not positions; "
                "importing it as a portfolio would build an allocation out of trades"
            )
        return self._positions

    def require_movements(self) -> tuple[dict[str, Any], ...]:
        """Return movements, refusing a holdings export outright.

        Returns:
            The parsed movements, possibly empty for an account with no history.

        Raises:
            ExtraEtfFormatError: If this export is a holdings export, whose rows
                are positions rather than movements.
        """
        if self.kind != "transactions":
            raise ExtraEtfFormatError("this is a holdings export and holds positions, not movements")
        return self._movements


def read_export(path: str | Path) -> ExtraEtfExport:
    """Read and parse one extraETF export file.

    The file's modification time becomes ``source_mtime`` with
    ``as_of_source="file_mtime"``, because neither export format carries a
    snapshot-observation column.

    Args:
        path: The exported ``.csv`` file.

    Returns:
        The parsed export.

    Raises:
        ExtraEtfFormatError: If the file cannot be read, decoded, or parsed.
    """
    resolved = Path(path)
    try:
        # Stat the same descriptor the bytes came from: a separate ``stat`` call
        # can observe a later write than the read did, which would let the import
        # claim a fresher observation time than the content it actually holds.
        with resolved.open("rb") as handle:
            data = handle.read()
            modified = os.fstat(handle.fileno()).st_mtime
    except OSError as exc:
        raise ExtraEtfFormatError(f"cannot read export {resolved}: {exc}") from exc
    source_mtime = datetime.fromtimestamp(modified, tz=timezone.utc).isoformat()
    return parse_export(data, source_path=resolved, source_mtime=source_mtime, as_of_source="file_mtime")


def parse_export(
    data: str | bytes,
    *,
    source_path: str | Path | None = None,
    source_mtime: str | None = None,
    as_of_source: Literal["file_mtime", "unavailable"] = "unavailable",
) -> ExtraEtfExport:
    """Parse one extraETF export from text or raw bytes.

    Args:
        data: The export contents, as text or as raw file bytes.
        source_path: The originating file, recorded for provenance only.
        source_mtime: ISO-8601 modification time of the originating file, when
            the caller knows one.
        as_of_source: Where ``source_mtime`` came from.

    Returns:
        The parsed export. An export with a header and no data rows is valid and
        yields no positions — an empty portfolio is not an unreadable one.

    Raises:
        ExtraEtfFormatError: If the header is not one of the two known formats,
            or any row cannot be read without guessing.
        ValueError: If ``as_of_source`` and ``source_mtime`` disagree.
    """
    if source_mtime is None and as_of_source != "unavailable":
        raise ValueError(
            "as_of_source cannot claim where a source modification time came from "
            "when there is no source modification time"
        )
    if source_mtime is not None and as_of_source == "unavailable":
        raise ValueError("as_of_source is 'unavailable' but a source modification time was given; the two must agree")
    if as_of_source not in ("file_mtime", "unavailable"):
        raise ValueError(f"as_of_source must be 'file_mtime' or 'unavailable', not {as_of_source!r}")
    text = _decode(data)
    kind, header, rows = _read_rows(text)
    if kind == "holdings":
        positions = tuple(_parse_holding(header, cells, number, source_mtime=source_mtime) for number, cells in rows)
        _refuse_duplicate_positions(positions, rows)
        movements: tuple[dict[str, Any], ...] = ()
    else:
        positions = ()
        movements = tuple(_parse_movement(header, cells, number) for number, cells in rows)
    return ExtraEtfExport(
        kind=kind,
        source_mtime=source_mtime,
        as_of_source=as_of_source,
        source_path=Path(source_path) if source_path is not None else None,
        _positions=positions,
        _movements=movements,
    )


def _refuse_duplicate_positions(positions: tuple[dict[str, Any], ...], rows: list[tuple[int, list[str]]]) -> None:
    """Refuse one instrument listed twice under a single portfolio ID.

    The same instrument under two different portfolio IDs is two genuine
    positions and is left alone. The same instrument twice under **one**
    portfolio ID is a file whose meaning is ambiguous — one lot listed twice, or
    two real lots — and a caller that sums those rows double-counts the position
    while a caller that keeps one drops the other, so the file is refused.

    Args:
        positions: The parsed holdings positions, in row order.
        rows: The ``(line number, cells)`` pairs those positions came from.

    Raises:
        ExtraEtfFormatError: If a ``(portfolio_id, instrument_id)`` pair repeats.
    """
    seen: dict[tuple[str, str], int] = {}
    for (number, _cells), position in zip(rows, positions, strict=True):
        # The identifier is emitted exactly as exported — rewriting a crypto
        # pair's case would invent an identity this module does not own — but
        # comparison folds case, because the pair shape is matched
        # case-insensitively: BTC_to_EUR and btc_to_eur are the same label, and
        # two positions for them would double-count. ISINs are already canonical
        # upper-case, so folding changes nothing for them.
        key = (position["portfolio_id"], position["instrument_id"].casefold())
        first = seen.get(key)
        if first is not None:
            raise ExtraEtfFormatError(
                f"row {number}: {position['instrument_id']} is listed twice under portfolio "
                f"{position['portfolio_id']!r} (first at row {first}); a repeated instrument in one "
                f"portfolio cannot be summed or collapsed without inventing a position"
            )
        seen[key] = number


def _decode(data: str | bytes) -> str:
    """Decode export bytes, preferring UTF-8 and falling back to cp1252.

    Args:
        data: Export contents as text or bytes.

    Returns:
        The decoded text.

    Raises:
        ExtraEtfFormatError: If neither encoding decodes the bytes.
    """
    if isinstance(data, str):
        return data
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ExtraEtfFormatError("export is decodable as neither UTF-8 nor cp1252")


def _read_rows(
    text: str,
) -> tuple[ExportKind, tuple[str, ...], list[tuple[int, list[str]]]]:
    """Split an export into its format, header and data rows.

    Args:
        text: The decoded export.

    Returns:
        The detected format, the header columns, and ``(line number, cells)``
        pairs for every non-blank data row.

    Raises:
        ExtraEtfFormatError: If the export is empty, its header is not one of the
            two known formats, or a row cannot be aligned to that header.
    """
    reader = csv.reader(io.StringIO(text), delimiter=";")
    header: tuple[str, ...] | None = None
    number = 0
    try:
        for cells in reader:
            number += 1
            _refuse_multiline_record(cells, number)
            if _skip_blank_row(cells, number):
                continue
            header = tuple(cell.replace("\ufeff", "").strip() for cell in cells)
            break
        if header is None:
            raise ExtraEtfFormatError("export is empty")
        kind = _detect_kind(header)

        rows: list[tuple[int, list[str]]] = []
        for cells in reader:
            number += 1
            _refuse_multiline_record(cells, number)
            if _skip_blank_row(cells, number):
                continue
            if len(cells) != len(header):
                raise ExtraEtfFormatError(
                    f"row {number}: expected {len(header)} fields to match the header, "
                    f"found {len(cells)}; a row that cannot be aligned is refused rather "
                    f"than skipped"
                )
            rows.append((number, [cell.strip() for cell in cells]))
    except csv.Error as exc:
        # csv raises its own errors for an over-long field, a bare carriage return
        # or a stray quote. A caller catches the documented error type, so it is
        # translated rather than allowed to escape as a bare csv error.
        raise ExtraEtfFormatError(f"export is not readable as CSV: {exc}") from exc
    return kind, header, rows


def _skip_blank_row(cells: list[str], number: int) -> bool:
    """Return whether a row is blank enough to skip, refusing an odd blank one.

    A genuinely empty line is how every file ends. A line of nothing but
    separators is not: it is a record this reader cannot align, and dropping it
    while claiming to refuse anything unalignable would be the silent skip the
    rest of this module exists to avoid.

    Args:
        cells: The record's fields.
        number: The record's position in the file, for error messages.

    Returns:
        ``True`` when the row carries no data and should be skipped.

    Raises:
        ExtraEtfFormatError: If the row is blank but wider than one field.
    """
    if any(cell.strip() for cell in cells):
        return False
    if len(cells) <= 1:
        return True
    raise ExtraEtfFormatError(
        f"row {number}: a blank row of {len(cells)} fields is not a layout this export "
        f"declares, so it is refused rather than dropped silently"
    )


def _refuse_multiline_record(cells: list[str], number: int) -> None:
    """Refuse a csv record whose fields span more than one physical line.

    extraETF writes one record per line and never quotes an embedded newline. A
    stray quote in one field therefore makes csv fold the *following* lines into
    that record: the record still has the right field count, so the arity check
    passes, and two positions silently become one with another instrument's
    quantity and value grafted onto it. That is a plausible-looking wrong
    portfolio, so the fold is refused outright rather than aligned.

    Args:
        cells: The record's fields.
        number: The record's position in the file, for error messages.

    Raises:
        ExtraEtfFormatError: If any field contains a line break.
    """
    if any("\n" in cell or "\r" in cell for cell in cells):
        raise ExtraEtfFormatError(
            f"row {number}: a field spans more than one line, so this record and "
            f"the line after it were folded together; a stray quote is the usual "
            f"cause and the positions it swallowed cannot be recovered here"
        )


def _detect_kind(header: tuple[str, ...]) -> ExportKind:
    """Identify which extraETF format a header belongs to.

    Args:
        header: The export's column names.

    Returns:
        ``"holdings"`` or ``"transactions"``.

    Raises:
        ExtraEtfFormatError: If the header is neither known format exactly.
    """
    columns = set(header)
    if len(header) == len(_HOLDINGS_COLUMNS) and columns == set(_HOLDINGS_COLUMNS):
        return "holdings"
    if len(header) == len(_TRANSACTIONS_COLUMNS) and columns == set(_TRANSACTIONS_COLUMNS):
        return "transactions"
    transactions_like = "Transaktion" in columns
    expected = _TRANSACTIONS_COLUMNS if transactions_like else _HOLDINGS_COLUMNS
    missing = [column for column in expected if column not in columns]
    unexpected = [column for column in header if column not in expected]
    raise ExtraEtfFormatError(
        "export header is not a recognised extraETF format; this reader knows two, the "
        f"holdings export {list(_HOLDINGS_COLUMNS)} and the transactions export "
        f"{list(_TRANSACTIONS_COLUMNS)}; the nearest of those is "
        f"{'transactions' if transactions_like else 'holdings'}, against which missing "
        f"columns: {missing or 'none'}, unexpected columns: {unexpected or 'none'}"
    )


def _parse_holding(
    header: tuple[str, ...], cells: list[str], number: int, *, source_mtime: str | None
) -> dict[str, Any]:
    """Convert one holdings row into a position.

    Args:
        header: The export's column names, which key the row's fields.
        cells: The row's fields.
        number: The row's line number, for error messages.
        source_mtime: The export file's modification time, mirrored onto the
            position under a name that cannot be read as an observation time.

    Returns:
        One position in the portfolio wire shape.

    Raises:
        ExtraEtfFormatError: If the row cannot be read without guessing.
    """
    row = dict(zip(header, cells, strict=True))
    identifier, identifier_kind, checksum_ok = _describe_identifier(_identifier_cell(row, number))
    if identifier_kind not in {"isin", "crypto_pair"}:
        raise ExtraEtfFormatError(
            f"row {number}: {row['ISIN']!r} is neither an ISIN nor an extraETF crypto "
            f"pair such as BTC_to_EUR, so the holding cannot be attributed to an "
            f"instrument"
        )
    name = _required(row, "Name", number)
    instrument_type = _required(row, "Typ", number)
    currency = _required(row, "Währung", number).upper()
    if not _CURRENCY_PATTERN.fullmatch(currency):
        raise ExtraEtfFormatError(f"row {number}: Währung is not a three-letter currency code: {row['Währung']!r}")
    portfolio_id = _required(row, "Portfolio ID", number)
    quantity = _parse_decimal(row["Anzahl"], field="Anzahl", row_number=number)
    if quantity is None:
        raise ExtraEtfFormatError(f"row {number}: Anzahl is empty")
    return {
        "broker": BROKER,
        "source": "extraetf_export",
        "symbol": identifier,
        # The export names an instrument, never a quoting instrument: an ISIN in
        # a quote-symbol field is a request for a quote on a security identifier,
        # so this stays None for the container to resolve. ``market`` is None for
        # the same reason — extraETF rows carry no venue.
        "quote_symbol": None,
        "instrument_id": identifier,
        "instrument_id_kind": identifier_kind,
        "instrument_id_checksum_ok": checksum_ok,
        "name": name,
        "instrument_type": instrument_type,
        # The export's instrument class mapped into the repo's vocabulary, and
        # None when it is a class this reader does not know. Deliberately not
        # defaulted to "stock": a guessed asset type is a fabricated fact about
        # the instrument, where an unknown class is honestly unknown.
        "asset_type": _ASSET_TYPE_BY_EXPORT_TYPE.get(instrument_type.strip().lower()),
        "market": None,
        "currency": currency,
        "price_currency": currency,
        "quantity": _number(quantity),
        "cost_price": _optional_number(row["Kaufpreis"], field="Kaufpreis", row_number=number),
        "market_price": _optional_number(row["Aktueller Kurs"], field="Aktueller Kurs", row_number=number),
        "source_market_value": _optional_number(row["Aktueller Wert"], field="Aktueller Wert", row_number=number),
        "fx_rate": _optional_number(row["Wechselkurs"], field="Wechselkurs", row_number=number),
        "region": _optional_text(row, "Region", number),
        "country": _optional_text(row, "Land", number),
        "sector": _optional_text(row, "Sektor", number),
        "portfolio_id": portfolio_id,
        "portfolio_name": _optional_text(row, "Portfolioname", number),
        # No observation time exists in this format, so the repo's "when was this
        # observed" field stays empty rather than borrowing the file's mtime,
        # which any copy, sync or restore rewrites.
        "updated_at": None,
        "source_mtime": source_mtime,
    }


def _parse_movement(header: tuple[str, ...], cells: list[str], number: int) -> dict[str, Any]:
    """Convert one transactions row into a movement.

    Args:
        header: The export's column names, which key the row's fields.
        cells: The row's fields.
        number: The row's line number, for error messages.

    Returns:
        One movement, carrying the export's own transaction label plus a
        normalised ``movement_kind``.

    Raises:
        ExtraEtfFormatError: If the row cannot be read without guessing.
    """
    row = dict(zip(header, cells, strict=True))
    movement = _required(row, "Transaktion", number)
    movement_kind = _MOVEMENT_KIND_BY_TRANSACTION.get(movement.strip().lower())
    if movement_kind is None:
        raise ExtraEtfFormatError(
            f"row {number}: Transaktion {movement!r} is none of the labels this format documents "
            f"({', '.join(sorted(_MOVEMENT_KIND_BY_TRANSACTION))}); a movement whose kind is unknown "
            f"is refused rather than carried through unlabelled, because a dividend, a fee and a "
            f"cash movement all look alike once the label is dropped"
        )
    transaction_date = _required(row, "Datum", number)
    match = _DATE_PATTERN.fullmatch(transaction_date)
    if match is None:
        raise ExtraEtfFormatError(f"row {number}: Datum is not a DD.MM.YYYY date: {transaction_date!r}")
    day, month, year = (int(part) for part in match.groups())
    try:
        booked_on = datetime(year, month, day).date().isoformat()
    except ValueError as exc:
        raise ExtraEtfFormatError(f"row {number}: Datum is not a real date: {transaction_date!r}") from exc
    currency = _required(row, "Währung", number).upper()
    if not _CURRENCY_PATTERN.fullmatch(currency):
        raise ExtraEtfFormatError(f"row {number}: Währung is not a three-letter currency code: {row['Währung']!r}")
    identifier, identifier_kind, checksum_ok = _describe_identifier(_identifier_cell(row, number))
    if identifier_kind == "unknown":
        raise ExtraEtfFormatError(
            f"row {number}: the ISIN column is empty, so the movement cannot be attributed to an "
            f"instrument; a cash or fee row carrying no instrument is not a movement this reader "
            f"can represent"
        )
    if identifier_kind == "raw_symbol" and not _SYMBOL_PATTERN.fullmatch(identifier):
        # A movement carries a bare symbol for crypto, so ``raw_symbol`` is
        # allowed here where a holdings row would refuse it — but only when it
        # looks like a symbol. Anything else is refused rather than carried as
        # an identity it does not have.
        raise ExtraEtfFormatError(
            f"row {number}: {identifier!r} is neither an ISIN nor a crypto symbol, so the movement "
            f"cannot be attributed to an instrument"
        )
    quantity = _parse_decimal(row["Anzahl"], field="Anzahl", row_number=number)
    if quantity is None:
        raise ExtraEtfFormatError(f"row {number}: Anzahl is empty")
    return {
        "broker": BROKER,
        "source": "extraetf_export",
        "date": booked_on,
        "instrument_id": identifier,
        "instrument_id_kind": identifier_kind,
        "instrument_id_checksum_ok": checksum_ok,
        "name": _required(row, "Name", number),
        "instrument_type": _required(row, "Typ", number),
        "movement": movement,
        "movement_kind": movement_kind,
        "quantity": _number(quantity),
        "price": _optional_number(row["Preis"], field="Preis", row_number=number),
        "fees": _optional_number(row["Gebühren"], field="Gebühren", row_number=number),
        "taxes": _optional_number(row["Steuern"], field="Steuern", row_number=number),
        "currency": currency,
        "fx_rate": _optional_number(row["Wechselkurs"], field="Wechselkurs", row_number=number),
        "accrued_interest": _optional_number(row["Stückzinsen"], field="Stückzinsen", row_number=number),
        "portfolio_id": _required(row, "Portfolio ID", number),
        "portfolio_name": _optional_text(row, "Portfolioname", number),
    }


def _has_control_characters(value: str) -> bool:
    """Report whether text carries characters that are corruption, not content.

    C0 controls (below 32), DEL and the C1 block are all refused. None of them
    is part of an exported name, label or symbol, and a field carrying one is
    damaged text rather than data.

    Args:
        value: The raw field text.

    Returns:
        ``True`` when the text carries a control character.
    """
    return any(ord(character) < 32 or 0x7F <= ord(character) <= 0x9F for character in value)


def _identifier_cell(row: dict[str, str], number: int) -> str:
    """Return the ``ISIN`` column, refusing text that cannot identify anything.

    The identifier column is read through :func:`_describe_identifier` rather
    than through :func:`_required`, because a crypto row carries a symbol here
    instead of a code. That path performs no corruption check of its own, so
    without this a symbol carrying control characters would be emitted verbatim.

    Args:
        row: The aligned row.
        number: The row's line number, for error messages.

    Returns:
        The raw identifier text, unmodified.

    Raises:
        ExtraEtfFormatError: If the text carries control characters.
    """
    value = row["ISIN"]
    if _has_control_characters(value):
        raise ExtraEtfFormatError(f"row {number}: ISIN carries control characters")
    return value


def _optional_text(row: dict[str, str], column: str, number: int) -> str | None:
    """Return an optional text column, refusing corruption.

    Args:
        row: The aligned row.
        column: The column to read.
        number: The row's line number, for error messages.

    Returns:
        The value, or ``None`` when the field is blank.

    Raises:
        ExtraEtfFormatError: If the value carries control characters.
    """
    value = row[column]
    if not value:
        return None
    if _has_control_characters(value):
        raise ExtraEtfFormatError(f"row {number}: {column} carries control characters")
    return value


def _required(row: dict[str, str], column: str, number: int) -> str:
    """Return a column's value, refusing an empty or corrupt one.

    Args:
        row: The aligned row.
        column: The column to read.
        number: The row's line number, for error messages.

    Returns:
        The non-empty value.

    Raises:
        ExtraEtfFormatError: If the value is empty or carries control
            characters. Control characters are refused for the same reason
            :func:`src.markets.portfolio.config.parse_settings` refuses them in a source
            label: they are corruption, and text carrying them does not
            round-trip.
    """
    value = row[column]
    if not value:
        raise ExtraEtfFormatError(f"row {number}: {column} is empty")
    if _has_control_characters(value):
        raise ExtraEtfFormatError(f"row {number}: {column} carries control characters")
    return value


def _optional_number(raw: str, *, field: str, row_number: int) -> float | None:
    """Parse an optional German-formatted number.

    Args:
        raw: The raw field text.
        field: The column name, for error messages.
        row_number: The row's line number, for error messages.

    Returns:
        The value, or ``None`` when the field is blank. A blank price is an
        absent observation rather than a zero, and is never guessed at.

    Raises:
        ExtraEtfFormatError: If the field is present but unreadable.
    """
    value = _parse_decimal(raw, field=field, row_number=row_number)
    return None if value is None else _number(value)


def _parse_decimal(raw: str, *, field: str, row_number: int) -> Decimal | None:
    """Parse a German-formatted number, refusing anything ambiguous.

    A comma is the decimal separator and a period may only be a thousands
    separator: ``1.386,608`` is 1386.608 and ``12,3456`` is 12.3456. A value
    whose separators are US-ordered, or whose period grouping is not a plain
    thousands separator, is refused rather than guessed at, because guessing
    wrong changes a position's size by orders of magnitude.

    Args:
        raw: The raw field text.
        field: The column name, for error messages.
        row_number: The row's line number, for error messages.

    Returns:
        The parsed value, or ``None`` when the field is blank.

    Raises:
        ExtraEtfFormatError: If the field is unreadable or ambiguous.
    """
    text = raw.strip()
    if not text:
        return None
    sign = ""
    if text[0] in "+-":
        sign, text = text[0], text[1:]
    if not text or not _NUMERAL_PATTERN.fullmatch(text):
        raise ExtraEtfFormatError(f"row {row_number}: {field} is not a number: {raw!r}")
    whole, comma, fraction = text.rpartition(",")
    if not comma:
        whole, fraction = text, ""
    elif "." in fraction:
        raise ExtraEtfFormatError(
            f"row {row_number}: {field} reads as a US-formatted number: {raw!r}; "
            f"extraETF exports use German formatting such as 1.386,608"
        )
    # The leading group is capped at three digits too. A plain ``1234.567`` is a
    # US decimal, and reading its period as a thousands separator would scale the
    # value by a thousand — the misread this whole rule exists to refuse.
    groups = whole.split(".")
    if (
        any(not group.isdigit() for group in groups)
        or (len(groups) > 1 and len(groups[0]) > 3)
        or any(len(group) != 3 for group in groups[1:])
        or (comma and not fraction.isdigit())
    ):
        raise ExtraEtfFormatError(
            f"row {row_number}: {field} has ambiguous separators: {raw!r}; expected German formatting such as 1.386,608"
        )
    digits = "".join(groups) + (f".{fraction}" if comma else "")
    try:
        value = Decimal(f"{sign}{digits}")
    except InvalidOperation as exc:  # pragma: no cover - guarded by the checks above
        raise ExtraEtfFormatError(f"row {row_number}: {field} is not a number: {raw!r}") from exc
    try:
        # Probe the wire format's precision here, while the row is still known.
        # Handing an unrepresentable magnitude to the formatter would surface as
        # a bare decimal.InvalidOperation, which is not the documented error.
        quantized = value.quantize(_WIRE_QUANTUM)
    except InvalidOperation as exc:
        raise ExtraEtfFormatError(
            f"row {row_number}: {field} carries more precision than a position can represent: {raw!r}"
        ) from exc
    if quantized != value:
        # Quantizing also *rounds*, and a discarded rounding is a silently
        # altered position: 0,000000004 is not zero in the file, so it must not
        # become an empty position in the output, and 1,123456789 must not lose
        # its ninth decimal place without saying so.
        raise ExtraEtfFormatError(
            f"row {row_number}: {field} carries more precision than a position can represent: {raw!r}"
        )
    if Decimal(repr(float(value))) != value:
        # ``float`` holds about seventeen significant digits, so a value with
        # more integer digits than that still changes magnitude on the way to
        # the wire format even though it quantized cleanly.
        raise ExtraEtfFormatError(
            f"row {row_number}: {field} carries more precision than a position can represent: {raw!r}"
        )
    return value


def _describe_identifier(raw: str) -> tuple[str, str, bool | None]:
    """Describe an export identifier without refusing anything.

    Args:
        raw: The raw ``ISIN`` column value.

    Returns:
        The identifier as the export spelled it, its kind — ``"isin"``,
        ``"crypto_pair"``, ``"raw_symbol"`` or ``"unknown"`` — and, for an
        ISIN-shaped value, whether its check digit is valid. extraETF stores
        crypto identifiers that are not ISINs, so the check digit is reported
        rather than enforced: refusing a whole import over one unverifiable
        identifier would block a user whose positions are otherwise perfectly
        readable.

        An ISIN is returned upper-cased, because an ISIN is canonical and
        case-insensitive. A crypto pair such as ``BTC_to_EUR`` is returned
        exactly as exported, because it is extraETF's own label and not a
        canonical code — rewriting its case would invent an identity we do not
        own.

        The kind describes the *shape* of the identifier, never its asset class.
        ``"crypto_pair"`` means the value has extraETF's ``X_to_Y`` pair form, so
        a foreign-exchange pair such as ``USD_to_EUR`` is described the same way
        — which is why a ``Währung / Krypto`` row is left with no ``asset_type``
        rather than being called a crypto holding.
    """
    identifier = raw.strip()
    if not identifier:
        return "", "unknown", None
    if _ISIN_PATTERN.fullmatch(identifier):
        canonical = identifier.upper()
        return canonical, "isin", _isin_checksum_ok(canonical)
    if _CRYPTO_PAIR_PATTERN.fullmatch(identifier):
        return identifier, "crypto_pair", None
    return identifier, "raw_symbol", None


def _isin_checksum_ok(value: str) -> bool:
    """Check an ISIN's check digit.

    Args:
        value: A value already matching :data:`_ISIN_PATTERN`.

    Returns:
        ``True`` when the Luhn-style check digit is valid.
    """
    digits = "".join(str(ord(character) - 55) if character.isalpha() else character for character in value)
    total = 0
    for index, character in enumerate(reversed(digits)):
        digit = int(character)
        if index % 2:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def _number(value: Decimal) -> float:
    """Return a wire-ready float, quantized as the connector path does.

    Args:
        value: A value already probed by :func:`_parse_decimal`, which proves the
            value survives this quantization unchanged, so this cannot round or
            raise.

    Returns:
        The value as a float, quantized to eight decimal places to match
        :func:`src.markets.portfolio.normalization.normalize_position`.
    """
    return float(value.quantize(_WIRE_QUANTUM))
