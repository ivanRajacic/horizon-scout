"""Robust reader for CORDIS DET semicolon-delimited CSV exports.

The dumps are not RFC-4180 compliant: fields containing unescaped quotes and
semicolons (notably project.objective) break standard CSV parsers, and
organization.csv writes some empty fields unquoted (`;;`). Two facts make a
deterministic parse possible anyway:
  - every record is exactly one physical line (no embedded newlines), and
  - every non-empty field is wrapped in double quotes,
so the literal sequence `";"` only ever occurs as a field delimiter.
"""

from pathlib import Path
from typing import Iterator


def read_rows(path: Path) -> Iterator[list[str]]:
    """Yield header first, then each record, as a list of field strings."""
    with open(path, encoding="utf-8-sig") as f:
        header = _split(f.readline())
        ncols = len(header)
        yield header
        for lineno, line in enumerate(f, start=2):
            fields = _split(line)
            if len(fields) != ncols:
                raise ValueError(
                    f"{path.name}:{lineno}: expected {ncols} fields, "
                    f"got {len(fields)}"
                )
            yield fields


def _split(line: str) -> list[str]:
    line = line.rstrip("\r\n")
    # Normalize unquoted empty fields (`;;`) to quoted ones so the whole
    # line becomes "f1";"f2";...;"fN".
    while ";;" in line:
        line = line.replace(";;", ';"";')
    if line.startswith(";"):
        line = '""' + line
    if line.endswith(";"):
        line = line + '""'
    if not (line.startswith('"') and line.endswith('"')):
        raise ValueError(f"line does not start/end with quote: {line[:100]!r}")
    parts = line[1:-1].split('";"')
    # CSV-style doubled quotes inside fields represent literal quotes.
    return [p.replace('""', '"') for p in parts]
