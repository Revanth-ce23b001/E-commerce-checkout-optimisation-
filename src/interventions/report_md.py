"""Markdown rendering shared by both halves of the Phase 5 report.

Split out because ``report.py`` and ``report_findings.py`` both need it and
neither may import the other: the assembly lives in ``report.py``, which imports
the findings sections, so a back-import would close the cycle.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def md(frame: pd.DataFrame, index: bool = False) -> str:
    """Plain markdown pipe table, hand-rolled.

    ``DataFrame.to_markdown`` needs `tabulate`, which is outside CLAUDE.md's
    approved stack. A dependency is not worth a table renderer.
    """
    frame = frame.reset_index() if index else frame
    header = [str(c) for c in frame.columns]
    body = [[_cell(v) for v in row] for row in frame.itertuples(index=False)]
    lines = ["| " + " | ".join(header) + " |",
             "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(r) + " |" for r in body]
    return "\n".join(lines)


def _cell(value) -> str:
    if isinstance(value, (bool, np.bool_)):
        return "yes" if value else "no"
    if isinstance(value, float):
        if np.isnan(value):
            return "-"
        return "{:,.4f}".format(value).rstrip("0").rstrip(".") or "0"
    if isinstance(value, (int, np.integer)):
        return "{:,}".format(value)
    return str(value).replace("|", "\\|")


def _fill(template: str, tables: dict, **values) -> str:
    """Format the prose first, then splice the tables in.

    Doing it in this order is deliberate: a table cell containing a brace would
    otherwise be read as a format placeholder and raise, or worse, silently
    swallow text.
    """
    out = template.format(**values)
    for name, table in tables.items():
        out = out.replace("<<" + name + ">>", table)
    return out


def _total(cell: dict) -> pd.Series:
    summary = cell["summary"]
    return summary[summary["tier"] == "ALL"].iloc[0]


