"""
The shape of an import, and what happens to each row.

Every import is the same three steps, and they exist in this order for one
reason: **nothing is written until the person has seen what will happen.**

    analyse   what columns are in your file, and what we think they mean
    preview   every row validated, with the errors named, nothing written
    commit    the same validation again, then write

Preview and commit run the *same* validation over the same file, so what the
person approved is what gets written. Commit re-validates rather than trusting
a preview token, because the alternative is holding parsed state on the server
between two requests and hoping the file has not changed underneath it.

An error report is per-row and per-column, never "import failed". A hospital
with a 900-line price list and three bad rows needs to know which three.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from apps.core.models import Organisation

from .reader import ParsedSheet


@dataclass(frozen=True, slots=True)
class ColumnSpec:
    """One column an import understands."""

    key: str
    label: str
    required: bool = True
    #: Lower-case header fragments that mean this column. Used to guess a
    #: mapping, never to decide one — the person always confirms.
    aliases: tuple[str, ...] = ()
    help_text: str = ""
    example: str = ""


@dataclass(slots=True)
class RowError:
    row_number: int
    column: str
    message: str


@dataclass(slots=True)
class PreparedRow:
    """One validated row, ready to write."""

    row_number: int
    values: dict[str, Any]
    #: A short description of what committing this row will do, shown in the
    #: preview so the person is approving an outcome rather than a payload.
    summary: str = ""


@dataclass(slots=True)
class PreviewResult:
    ready: list[PreparedRow] = field(default_factory=list)
    errors: list[RowError] = field(default_factory=list)
    #: Rows that are valid but will update something that already exists.
    updates: int = 0
    truncated: bool = False

    @property
    def ok(self) -> int:
        return len(self.ready)

    @property
    def failed(self) -> int:
        return len({error.row_number for error in self.errors})


@dataclass(frozen=True, slots=True)
class ImportSpec:
    """One kind of import: its columns, its validator and its writer."""

    kind: str
    title: str
    blurb: str
    columns: tuple[ColumnSpec, ...]
    #: Validates one mapped row. Returns the prepared values, or raises
    #: ValueError with a message naming the column.
    validate: Callable[[Organisation, dict[str, str], int], PreparedRow]
    #: Writes the prepared rows. Returns (created, updated).
    write: Callable[[Organisation, Sequence[PreparedRow]], tuple[int, int]]

    def column(self, key: str) -> ColumnSpec | None:
        return next((column for column in self.columns if column.key == key), None)

    def required_keys(self) -> set[str]:
        return {column.key for column in self.columns if column.required}


def suggest_mapping(spec: ImportSpec, headers: Sequence[str]) -> dict[str, str]:
    """
    Guess which column is which, so the usual file needs no mapping at all.

    Exact match on the label first, then an alias contained in the header.
    Deliberately conservative: a wrong guess that the person does not notice
    is worse than no guess, because the preview will look plausible. Anything
    unmatched is left blank for them to choose.
    """
    taken: set[str] = set()
    mapping: dict[str, str] = {}
    normalised = {header: _normalise(header) for header in headers}

    for column in spec.columns:
        wanted = [_normalise(column.label), *(alias.lower() for alias in column.aliases)]

        # Pass one: an exact match on the whole header.
        for header, clean in normalised.items():
            if header in taken:
                continue
            if clean in wanted:
                mapping[column.key] = header
                taken.add(header)
                break
        if column.key in mapping:
            continue

        # Pass two: an alias somewhere inside the header, so "Unit Price (N)"
        # still finds "price".
        for header, clean in normalised.items():
            if header in taken:
                continue
            if any(alias and alias in clean for alias in wanted):
                mapping[column.key] = header
                taken.add(header)
                break

    return mapping


def _normalise(value: str) -> str:
    return "".join(
        character for character in value.lower() if character.isalnum() or character == " "
    ).strip()


def build_preview(
    *,
    spec: ImportSpec,
    organisation: Organisation,
    sheet: ParsedSheet,
    mapping: dict[str, str],
) -> PreviewResult:
    """
    Validate every row. Writes nothing.

    Run again, unchanged, by commit — see the module docstring.
    """
    result = PreviewResult(truncated=sheet.truncated)

    missing = sorted(spec.required_keys() - {key for key, header in mapping.items() if header})
    if missing:
        # `found` rather than `column`: a walrus inside a comprehension binds
        # in the ENCLOSING scope, and `column` is reused further down for the
        # column name parsed out of a validator's error.
        labels = [(found.label if (found := spec.column(key)) else key) for key in missing]
        result.errors.append(
            RowError(
                row_number=0,
                column="",
                message=f"These columns still need to be matched: {', '.join(labels)}.",
            )
        )
        return result

    index_of = {header: position for position, header in enumerate(sheet.headers)}

    for offset, row in enumerate(sheet.rows):
        # +2: row 1 is the header, and spreadsheets are 1-indexed. The number
        # shown has to be the one the person sees in Excel, or the error
        # report is useless.
        row_number = offset + 2

        mapped: dict[str, str] = {}
        for key, header in mapping.items():
            position = index_of.get(header)
            mapped[key] = row[position] if position is not None and position < len(row) else ""

        try:
            prepared = spec.validate(organisation, mapped, row_number)
        except ValueError as exc:
            column, _, message = str(exc).partition(": ")
            result.errors.append(
                RowError(
                    row_number=row_number,
                    column=column if message else "",
                    message=message or str(exc),
                )
            )
            continue

        result.ready.append(prepared)

    return result
