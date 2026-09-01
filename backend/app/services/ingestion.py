"""Multi-format transaction ingestion.

Reads a JSON, CSV or XML file of transaction metadata, validates every record
against the `Transaction` schema in app/models.py, and returns typed objects.

The point of this module is to fail *loudly and specifically*. A parser that
silently drops malformed records produces a graph with holes in it, and the
missing edges look exactly like an absence of suspicious activity. So a bad
record raises, naming the record and the fields at fault.

List-valued columns (addresses and amounts) have no native representation in
CSV, so they are accepted in either of two encodings:

    JSON      ["1abc...", "1def..."]      preferred, unambiguous
    delimited 1abc...|1def...             pipe, semicolon or comma separated
"""

from __future__ import annotations

import ast
import csv
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.models import Transaction

SUPPORTED_EXTENSIONS = {".json", ".csv", ".xml"}

# Fields the schema declares as lists, so the CSV and XML readers know which
# cells need splitting. Derived from the model rather than hardcoded, so it
# stays correct if models.py changes.
LIST_FIELDS = {
    name
    for name, field in Transaction.model_fields.items()
    if getattr(field.annotation, "__origin__", None) is list
}

NUMERIC_LIST_FIELDS = {"input_amounts", "output_amounts"}

_DELIMITERS = ("|", ";", ",")


# --------------------------------------------------------------------------
# Coercion helpers
# --------------------------------------------------------------------------


def _parse_list_cell(value: Any, field: str) -> Any:
    """Turn a flat CSV/XML cell into a list.

    Left alone if it is already a list (the JSON reader path).
    """
    if isinstance(value, list):
        parsed = value
    elif value is None:
        return []
    else:
        text = str(value).strip()
        if not text:
            return []
        if text.startswith("[") and text.endswith("]"):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                # Tolerate single-quoted Python-style lists, which is what you
                # get when someone str()s a list into a spreadsheet.
                try:
                    parsed = ast.literal_eval(text)
                except (ValueError, SyntaxError) as exc:
                    raise ValueError(
                        f"field '{field}' looks like a list but could not be parsed: {text!r}"
                    ) from exc
        else:
            for delim in _DELIMITERS:
                if delim in text:
                    parsed = [p.strip() for p in text.split(delim) if p.strip()]
                    break
            else:
                parsed = [text]

    if field in NUMERIC_LIST_FIELDS:
        try:
            return [float(p) for p in parsed]
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"field '{field}' must contain numbers, got {parsed!r}"
            ) from exc
    return [str(p) for p in parsed]


def _normalise_record(record: dict[str, Any]) -> dict[str, Any]:
    """Apply list-cell parsing to a raw record from any reader."""
    out = dict(record)
    for field in LIST_FIELDS:
        if field in out:
            out[field] = _parse_list_cell(out[field], field)
    return out


# --------------------------------------------------------------------------
# Format readers - each returns a list of raw dicts, no validation yet
# --------------------------------------------------------------------------


def _read_json(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        # Tolerate a wrapper object like {"transactions": [...]}.
        for key in ("transactions", "data", "records", "items"):
            if key in data and isinstance(data[key], list):
                return data[key]
        raise ValueError(
            f"{path.name}: expected a JSON array of transactions, or an object "
            f"with a 'transactions' key, got keys {sorted(data)[:8]}"
        )
    if not isinstance(data, list):
        raise ValueError(f"{path.name}: expected a JSON array, got {type(data).__name__}")
    return data


def _read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise ValueError(f"{path.name}: file is empty, no CSV header row found")
        return [dict(row) for row in reader]


def _read_xml(path: Path) -> list[dict[str, Any]]:
    """Read an XML document of transactions.

    Accepts both a flat encoding, where a list field is one delimited element:

        <input_addresses>1abc|1def</input_addresses>

    and a nested encoding, where it wraps repeated children:

        <input_addresses><address>1abc</address><address>1def</address></input_addresses>
    """
    root = ET.parse(path).getroot()
    # The transaction elements are either the root's children, or nested one
    # level under a wrapper such as <transactions>.
    candidates = root.findall("transaction") or list(root)

    records: list[dict[str, Any]] = []
    for element in candidates:
        record: dict[str, Any] = {}
        for child in element:
            grandchildren = list(child)
            if grandchildren:
                record[child.tag] = [(g.text or "").strip() for g in grandchildren]
            else:
                record[child.tag] = (child.text or "").strip()
        records.append(record)
    return records


_READERS = {".json": _read_json, ".csv": _read_csv, ".xml": _read_xml}


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def _describe_validation_error(index: int, exc: ValidationError) -> str:
    """Turn a pydantic error into a message that names the record and fields."""
    missing: list[str] = []
    invalid: list[str] = []
    for err in exc.errors():
        location = ".".join(str(p) for p in err["loc"]) or "<record>"
        if err["type"] in ("missing", "value_error.missing"):
            missing.append(location)
        else:
            invalid.append(f"{location} ({err['msg']})")

    parts = [f"record #{index}"]
    if missing:
        parts.append(f"missing required field(s): {', '.join(sorted(missing))}")
    if invalid:
        parts.append(f"invalid field(s): {', '.join(sorted(invalid))}")
    return " - ".join(parts)


def load_transactions(file_path: str | Path) -> list[Transaction]:
    """Load and validate transactions from a JSON, CSV or XML file.

    The format is chosen from the file extension.

    Raises:
        FileNotFoundError: the path does not exist.
        ValueError: the extension is unsupported, the file is structurally
            malformed, or one or more records fail schema validation. The
            message names each offending record by index and lists exactly
            which fields are missing or invalid.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"No such transaction file: {path}")

    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"Unsupported file type '{suffix or path.name}'. "
            f"Expected one of: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )

    raw_records = _READERS[suffix](path)
    if not raw_records:
        raise ValueError(f"{path.name}: contains no transaction records")

    transactions: list[Transaction] = []
    problems: list[str] = []

    for index, raw in enumerate(raw_records):
        if not isinstance(raw, dict):
            problems.append(f"record #{index} - expected an object, got {type(raw).__name__}")
            continue
        try:
            transactions.append(Transaction.model_validate(_normalise_record(raw)))
        except ValidationError as exc:
            problems.append(_describe_validation_error(index, exc))
        except ValueError as exc:
            problems.append(f"record #{index} - {exc}")

    if problems:
        shown = problems[:10]
        suffix_note = (
            f"\n  ... and {len(problems) - len(shown)} further problem(s)"
            if len(problems) > len(shown)
            else ""
        )
        raise ValueError(
            f"{path.name}: {len(problems)} of {len(raw_records)} records failed "
            f"validation.\n  " + "\n  ".join(shown) + suffix_note
        )

    return transactions
