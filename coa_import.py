"""
Read a chart of accounts from an Excel / CSV file.

Two layouts are understood:
  1. A neat table with a header row containing "Code" and "Name"
     (optional columns: Type, Cash).
  2. A messy accountant sheet: section headings such as "ASSETS" / "EXPENSES"
     with rows of  <code> <account name>  underneath.

The result is only a PREVIEW - the user can correct any type or bank/cash
setting before anything is saved.
"""
import re

import pandas as pd

from coa_defaults import ACCOUNT_TYPES

SECTION_WORDS = [
    ("liabilit", "Liability"), ("equity", "Equity"), ("capital", "Equity"),
    ("asset", "Asset"), ("revenue", "Revenue"), ("income", "Revenue"),
    ("sales", "Revenue"), ("expense", "Expense"), ("cost", "Expense"),
]
EQUITY_HINTS = ("share capital", "retained", "reserve")


def _cell(value) -> str:
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return ""
    text = str(value).strip()
    return text[:-2] if re.fullmatch(r"\d+\.0", text) else text


def _is_code(text: str) -> bool:
    return bool(re.fullmatch(r"\d{3,12}", text))


def _type_from_code(code: str, name: str) -> str:
    if any(h in name.lower() for h in EQUITY_HINTS):
        return "Equity"
    return {"1": "Asset", "2": "Liability", "3": "Revenue"}.get(code[0], "Expense")


def _type_from_text(text: str):
    low = text.lower()
    for word, acc_type in SECTION_WORDS:
        if word in low:
            return acc_type
    return None


def read_table(uploaded_file) -> pd.DataFrame:
    """Read the uploaded .xlsx / .xls / .csv into a raw, header-less table of text."""
    name = getattr(uploaded_file, "name", "").lower()
    if name.endswith(".csv"):
        return pd.read_csv(uploaded_file, header=None, dtype=str, keep_default_na=False)
    return pd.read_excel(uploaded_file, header=None, dtype=str)


def parse_coa(raw: pd.DataFrame) -> pd.DataFrame:
    """Return a preview table with columns Code, Name, Type, Cash (bool)."""
    rows = [[_cell(v) for v in r] for r in raw.itertuples(index=False)]

    # ---- layout 1: neat table with a header row
    for i, row in enumerate(rows[:6]):
        low = [c.lower() for c in row]
        code_i = next((k for k, c in enumerate(low) if c in ("code", "account code", "acc_code")), None)
        name_i = next((k for k, c in enumerate(low) if c in ("name", "account", "account name", "description")), None)
        if code_i is not None and name_i is not None:
            type_i = next((k for k, c in enumerate(low) if c in ("type", "acc_type", "category", "account type")), None)
            cash_i = next((k for k, c in enumerate(low) if c in ("cash", "is_cash", "bank/cash", "is cash")), None)
            out = []
            for r in rows[i + 1:]:
                code, name = r[code_i], r[name_i]
                if not (code and name):
                    continue
                acc_type = r[type_i].strip().title() if type_i is not None else ""
                if acc_type not in ACCOUNT_TYPES:
                    acc_type = _type_from_code(code, name)
                is_cash = (r[cash_i].strip().lower() in ("1", "yes", "y", "true")) if cash_i is not None \
                    else code.startswith("14")
                out.append((code, name, acc_type, bool(is_cash)))
            return _finish(out)

    # ---- layout 2: messy sheet with section headings
    out, section, seen_section = [], None, False
    for r in rows:
        cells = [c for c in r if c]
        if not cells:
            continue
        code = next((c for c in cells if _is_code(c)), None)
        if code is None:
            if len(cells) <= 2:
                found = _type_from_text(" ".join(cells))
                if found:
                    section, seen_section = found, True
            continue
        text_cells = [c for c in cells if c != code and re.search(r"[A-Za-z]", c)]
        if not text_cells:
            continue
        name = text_cells[0]
        acc_type = section if seen_section else _type_from_code(code, name)
        out.append((code, name, acc_type, code.startswith("14") and acc_type == "Asset"))
    return _finish(out)


def _finish(rows) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["Code", "Name", "Type", "Cash"])
    return df.drop_duplicates(subset="Code", keep="last").reset_index(drop=True)
