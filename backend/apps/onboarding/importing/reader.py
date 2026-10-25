"""
Reading a spreadsheet somebody uploaded.

An uploaded file is hostile until proven otherwise, and a hospital's
"price list.xlsx" has usually been through four people and two versions of
Excel. The rules here come from that, not from theory:

* **Size and row caps, checked before parsing.** A 40 MB XLSX is either a
  mistake or an attack; either way the answer is the same. The row cap is
  what stops a zip bomb — a 2 KB XLSX can expand to gigabytes of XML, and
  openpyxl's read-only mode streams it, so the only real defence is to stop
  reading.

* **Encoding is guessed, in a fixed order.** Nigerian hospitals run Excel on
  Windows, so a CSV saved from it is usually cp1252 with a BOM, not UTF-8.
  Guessing wrong turns ₦ into a mojibake and a surname into a question mark,
  so UTF-8-with-BOM is tried first, then plain UTF-8, then cp1252 — which
  cannot fail, and is therefore last.

* **Nothing is coerced here.** Every cell comes back as a trimmed string,
  and the importers decide what a price or a date means. A reader that
  guesses types produces a file that imports differently depending on how
  Excel happened to format a column.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from typing import Any

#: Bigger than any hospital's opening price list, small enough to parse in a
#: request. A hospital with more than this has a migration, not an import.
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_ROWS = 5_000
MAX_COLUMNS = 60

#: Tried in order. cp1252 is last because it accepts any byte sequence, so
#: anything before it would never be reached.
ENCODINGS = ("utf-8-sig", "utf-8", "cp1252")


class SpreadsheetError(Exception):
    """The file could not be read. The message is shown to the person."""


@dataclass(slots=True)
class ParsedSheet:
    """A header row and the rows under it, all strings."""

    headers: list[str]
    rows: list[list[str]] = field(default_factory=list)
    #: True when the file had more rows than MAX_ROWS, so the person is told
    #: rather than silently importing the first five thousand.
    truncated: bool = False

    def as_dicts(self) -> list[dict[str, str]]:
        return [dict(zip(self.headers, row, strict=False)) for row in self.rows]


def read_upload(*, name: str, data: bytes) -> ParsedSheet:
    """
    Parse an uploaded CSV or XLSX into strings.

    Dispatches on the file name rather than sniffing the content, and then
    the parser itself rejects anything that is not actually that format. A
    content sniff would be friendlier and is not worth it: somebody who
    renames a .docx to .csv gets a clear error either way.
    """
    if not data:
        raise SpreadsheetError("That file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise SpreadsheetError(
            f"That file is {len(data) // 1024 // 1024} MB. The limit is "
            f"{MAX_UPLOAD_BYTES // 1024 // 1024} MB — if your list is really that "
            "big, send it to us and we will load it for you."
        )

    lowered = name.lower()
    if lowered.endswith(".csv") or lowered.endswith(".txt"):
        return _read_csv(data)
    if lowered.endswith(".xlsx") or lowered.endswith(".xlsm"):
        return _read_xlsx(data)
    if lowered.endswith(".xls"):
        raise SpreadsheetError(
            "That is the old Excel format (.xls). Open it in Excel and use "
            "File → Save As → Excel Workbook (.xlsx), then upload it again."
        )
    raise SpreadsheetError("Upload a .csv or .xlsx file.")


def _decode(data: bytes) -> str:
    for encoding in ENCODINGS:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    # Unreachable: cp1252 maps every byte. Kept so a future edit to ENCODINGS
    # cannot silently produce an unbound variable.
    raise SpreadsheetError("That file is not text we can read.")  # pragma: no cover


def _read_csv(data: bytes) -> ParsedSheet:
    text = _decode(data)

    # Sniff the delimiter. Nigerian Excel installs are usually comma, but a
    # machine set to a European locale saves semicolons, and the file still
    # says .csv.
    sample = text[:8192]
    try:
        dialect: Any = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel

    reader = csv.reader(io.StringIO(text), dialect)
    try:
        raw_headers = next(reader)
    except StopIteration:
        raise SpreadsheetError("That file has no rows in it.") from None

    headers = _clean_headers(raw_headers)
    rows: list[list[str]] = []
    truncated = False
    for row in reader:
        if len(rows) >= MAX_ROWS:
            truncated = True
            break
        cleaned = [str(cell).strip() for cell in row[: len(headers)]]
        if not any(cleaned):
            continue  # a blank line in the middle of a sheet
        cleaned += [""] * (len(headers) - len(cleaned))
        rows.append(cleaned)

    return ParsedSheet(headers=headers, rows=rows, truncated=truncated)


def _read_xlsx(data: bytes) -> ParsedSheet:
    try:
        from openpyxl import load_workbook
    except ImportError:  # pragma: no cover - openpyxl is a hard dependency
        raise SpreadsheetError(
            "Excel files cannot be read on this server. Save as .csv instead."
        ) from None

    try:
        # read_only streams the sheet instead of building the whole thing in
        # memory, and data_only takes the cached VALUE of a formula rather
        # than the formula text — a price list full of =B2*1.1 should import
        # as numbers, and we are not going to evaluate somebody's formulas.
        workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True, keep_links=False)
    except Exception as exc:
        raise SpreadsheetError(
            "That file could not be opened as a spreadsheet. If it came from "
            "Google Sheets, use File → Download → Microsoft Excel (.xlsx)."
        ) from exc

    try:
        sheet = workbook.worksheets[0] if workbook.worksheets else None
        if sheet is None:
            raise SpreadsheetError("That workbook has no sheets in it.")

        rows_iter = sheet.iter_rows(values_only=True)
        try:
            raw_headers = next(rows_iter)
        except StopIteration:
            raise SpreadsheetError("That sheet has no rows in it.") from None

        headers = _clean_headers([_cell(value) for value in raw_headers])
        rows: list[list[str]] = []
        truncated = False
        for raw in rows_iter:
            if len(rows) >= MAX_ROWS:
                truncated = True
                break
            cleaned = [_cell(value) for value in raw[: len(headers)]]
            if not any(cleaned):
                continue
            cleaned += [""] * (len(headers) - len(cleaned))
            rows.append(cleaned)

        return ParsedSheet(headers=headers, rows=rows, truncated=truncated)
    finally:
        # read_only workbooks hold the zip open until closed.
        workbook.close()


def _cell(value: object) -> str:
    """
    One cell, as the string a human would have typed.

    Excel stores every number as a float, so a quantity of 30 arrives as
    30.0 and a code of 00123 arrives as 123. The first is fixed here; the
    second cannot be — the leading zeros were lost when the file was saved,
    and the importers say so when a code looks wrong.
    """
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _clean_headers(raw: list[Any]) -> list[str]:
    headers: list[str] = []
    seen: dict[str, int] = {}
    for index, value in enumerate(raw[:MAX_COLUMNS]):
        name = str(value or "").strip()
        if not name:
            name = f"Column {index + 1}"
        # Two columns called "Price" is common and is not an error; they are
        # distinguished so a mapping can name one of them unambiguously.
        if name in seen:
            seen[name] += 1
            name = f"{name} ({seen[name]})"
        else:
            seen[name] = 1
        headers.append(name)

    if not headers:
        raise SpreadsheetError("The first row of that file has no column names in it.")
    return headers
