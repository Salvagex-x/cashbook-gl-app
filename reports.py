"""
Reports built from the posted lines (pure pandas - no database, no Streamlit).

Sign rule used for running balances:
  Asset & Expense accounts   -> balance = debits  - credits
  Liability, Equity, Revenue -> balance = credits - debits
"""
import io

import pandas as pd

DEBIT_NORMAL = {"Asset", "Expense"}


def _sign(acc_type: str) -> int:
    return 1 if acc_type in DEBIT_NORMAL else -1


def _ts(d) -> pd.Timestamp:
    return pd.Timestamp(d)


def _naira(series) -> pd.Series:
    return series.astype("float64") / 100.0


def _day(series) -> pd.Series:
    return pd.to_datetime(series).dt.strftime("%Y-%m-%d")


def with_contra(lines: pd.DataFrame) -> pd.DataFrame:
    """Add a 'contra' column = the other account(s) of the same voucher."""
    df = lines.copy()
    if df.empty:
        df["contra"] = pd.Series(dtype="object")
        return df
    by_voucher = df.groupby("voucher_id")["acc_label"].agg(lambda s: sorted(set(s))).to_dict()
    contra = []
    for vid, own in zip(df["voucher_id"], df["acc_label"]):
        others = [x for x in by_voucher[vid] if x != own]
        contra.append(others[0] if len(others) == 1 else (f"Various ({len(others)})" if others else ""))
    df["contra"] = contra
    return df


# ---------------------------------------------------------------- header numbers
def header_metrics(lines: pd.DataFrame, start, end) -> dict:
    """Opening / receipts / payments / closing cash, plus the real Dr = Cr check."""
    s, e = _ts(start), _ts(end)
    cash = lines[lines["is_cash"] == 1]
    before = cash[cash["tx_date"] < s]
    period = cash[(cash["tx_date"] >= s) & (cash["tx_date"] <= e)]
    upto = cash[cash["tx_date"] <= e]

    opening = int(before["debit_k"].sum() - before["credit_k"].sum())
    # A reversal offsets the kind of voucher it cancelled, so correcting a payment never
    # makes the Payments total look doubled.
    rev = period["v_type"] == "REVERSAL"
    receipts = int(period.loc[period["v_type"] == "RECEIPT", "debit_k"].sum()
                   - period.loc[rev & (period["reverses_type"] == "RECEIPT"), "credit_k"].sum())
    payments = int(period.loc[period["v_type"] == "PAYMENT", "credit_k"].sum()
                   - period.loc[rev & (period["reverses_type"] == "PAYMENT"), "debit_k"].sum())
    closing = int(upto["debit_k"].sum() - upto["credit_k"].sum())
    other = closing - opening - receipts + payments  # transfers, opening balances, reversals

    everything = lines[lines["tx_date"] <= e]
    total_dr, total_cr = int(everything["debit_k"].sum()), int(everything["credit_k"].sum())
    return {"opening": opening, "receipts": receipts, "payments": payments, "other": other,
            "closing": closing, "total_dr": total_dr, "total_cr": total_cr,
            "balanced": total_dr == total_cr}


# ------------------------------------------------------------------- cash register
def cash_register(lines: pd.DataFrame, start, end, acc_code: str | None = None):
    """Cashbook register with running balance. Returns (display_df, opening_k, closing_k)."""
    s, e = _ts(start), _ts(end)
    cash = with_contra(lines)
    cash = cash[cash["is_cash"] == 1]
    if acc_code:
        cash = cash[cash["acc_code"] == acc_code]
    before = cash[cash["tx_date"] < s]
    period = cash[(cash["tx_date"] >= s) & (cash["tx_date"] <= e)].copy()
    opening = int(before["debit_k"].sum() - before["credit_k"].sum())
    period["balance_k"] = opening + (period["debit_k"] - period["credit_k"]).cumsum()
    closing = int(period["balance_k"].iloc[-1]) if len(period) else opening

    body = pd.DataFrame({
        "Date": _day(period["tx_date"]), "Voucher": period["ref_no"], "Type": period["v_type"],
        "Department": period["unit_tag"], "Bank / Cash": period["acc_label"],
        "Description": period["desc"], "Contra account": period["contra"],
        "Receipt (In)": _naira(period["debit_k"]), "Payment (Out)": _naira(period["credit_k"]),
        "Balance": _naira(period["balance_k"]), "Status": period["status"],
    })
    first = pd.DataFrame([{
        "Date": s.strftime("%Y-%m-%d"), "Voucher": "", "Type": "", "Department": "",
        "Bank / Cash": "", "Description": "Balance brought forward", "Contra account": "",
        "Receipt (In)": float("nan"), "Payment (Out)": float("nan"), "Balance": opening / 100.0, "Status": "",
    }])
    return pd.concat([first, body], ignore_index=True), opening, closing


# --------------------------------------------------------------- general ledger
def account_ledger(lines: pd.DataFrame, code: str, start, end):
    """One account's ledger. Returns dict(code, name, acc_type, opening_k, closing_k, df, n)."""
    s, e = _ts(start), _ts(end)
    sub = with_contra(lines[lines["acc_code"] == code])
    if sub.empty:
        return None
    acc_type = sub["acc_type"].iloc[0]
    sign = _sign(acc_type)
    before = sub[sub["tx_date"] < s]
    period = sub[(sub["tx_date"] >= s) & (sub["tx_date"] <= e)].copy()
    opening = int(sign * (before["debit_k"].sum() - before["credit_k"].sum()))
    period["balance_k"] = opening + (sign * (period["debit_k"] - period["credit_k"])).cumsum()
    closing = int(period["balance_k"].iloc[-1]) if len(period) else opening

    body = pd.DataFrame({
        "Date": _day(period["tx_date"]), "Voucher": period["ref_no"], "Type": period["v_type"],
        "Department": period["unit_tag"], "Description": period["desc"],
        "Contra account": period["contra"], "Debit": _naira(period["debit_k"]),
        "Credit": _naira(period["credit_k"]), "Balance": _naira(period["balance_k"]),
        "Status": period["status"],
    })
    first = pd.DataFrame([{
        "Date": s.strftime("%Y-%m-%d"), "Voucher": "", "Type": "", "Department": "",
        "Description": "Balance brought forward", "Contra account": "", "Debit": float("nan"),
        "Credit": float("nan"), "Balance": opening / 100.0, "Status": "",
    }])
    return {"code": code, "name": str(sub["acc_name"].iloc[0]), "acc_type": acc_type,
            "opening_k": opening, "closing_k": closing, "n": len(period),
            "df": pd.concat([first, body], ignore_index=True)}


def all_ledgers(lines: pd.DataFrame, start, end, only_code: str | None = None) -> list[dict]:
    """Ledgers for every account that has a balance b/f or activity in the period."""
    out = []
    codes = [only_code] if only_code else sorted(lines["acc_code"].unique())
    for code in codes:
        led = account_ledger(lines, code, start, end)
        if led is None:
            continue
        if only_code or led["n"] > 0 or led["opening_k"] != 0:
            out.append(led)
    return out


# ------------------------------------------------------------------ trial balance
def trial_balance(lines: pd.DataFrame, end, hide_zero: bool = True):
    """
    Trial balance as at `end`.  Returns (display_df, total_dr_k, total_cr_k).
    Each account's net balance is shown in either the Debit or the Credit column.
    """
    sub = lines[lines["tx_date"] <= _ts(end)]
    cols = ["Code", "Account", "Type", "Debit", "Credit"]
    if sub.empty:
        return pd.DataFrame(columns=cols), 0, 0
    g = sub.groupby(["acc_code", "acc_name", "acc_type"], as_index=False)[["debit_k", "credit_k"]].sum()
    g["net_k"] = g["debit_k"] - g["credit_k"]
    if hide_zero:
        g = g[g["net_k"] != 0]
    g["dr_k"] = g["net_k"].clip(lower=0)
    g["cr_k"] = (-g["net_k"]).clip(lower=0)
    total_dr, total_cr = int(g["dr_k"].sum()), int(g["cr_k"].sum())
    body = pd.DataFrame({"Code": g["acc_code"], "Account": g["acc_name"], "Type": g["acc_type"],
                         "Debit": _naira(g["dr_k"]), "Credit": _naira(g["cr_k"])})
    total = pd.DataFrame([{"Code": "", "Account": "TOTAL", "Type": "",
                           "Debit": total_dr / 100.0, "Credit": total_cr / 100.0}])
    return pd.concat([body, total], ignore_index=True), total_dr, total_cr


# --------------------------------------------------------------------- vouchers
def voucher_summary(lines: pd.DataFrame, start, end) -> pd.DataFrame:
    s, e = _ts(start), _ts(end)
    per = lines[(lines["tx_date"] >= s) & (lines["tx_date"] <= e)]
    cols = ["voucher_id", "Date", "Voucher", "Type", "Department", "Narration", "Amount",
            "Status", "Posted by", "Note"]
    if per.empty:
        return pd.DataFrame(columns=cols)
    g = per.groupby("voucher_id", as_index=False).agg(
        tx_date=("tx_date", "first"), ref_no=("ref_no", "first"), v_type=("v_type", "first"),
        unit_tag=("unit_tag", "first"), narration=("narration", "first"),
        status=("status", "first"), created_by=("created_by", "first"),
        reverses_ref=("reverses_ref", "first"), corrects_ref=("corrects_ref", "first"),
        total_k=("debit_k", "sum"))
    g = g.sort_values(["tx_date", "voucher_id"], ascending=[False, False])
    return pd.DataFrame({
        "voucher_id": g["voucher_id"], "Date": _day(g["tx_date"]), "Voucher": g["ref_no"],
        "Type": g["v_type"], "Department": g["unit_tag"], "Narration": g["narration"],
        "Amount": _naira(g["total_k"]), "Status": g["status"], "Posted by": g["created_by"],
        "Note": [f"Reverses {a}" if a else (f"Corrects {b}" if b else "")
                 for a, b in zip(g["reverses_ref"], g["corrects_ref"])],
    }).reset_index(drop=True)


def voucher_detail(lines: pd.DataFrame, voucher_id: int) -> pd.DataFrame:
    sub = lines[lines["voucher_id"] == voucher_id]
    return pd.DataFrame({"Account": sub["acc_label"], "Line note": sub["note"],
                         "Debit": _naira(sub["debit_k"]),
                         "Credit": _naira(sub["credit_k"])}).reset_index(drop=True)


# ----------------------------------------------------------------------- exports
def _safe_cell(value):
    """Stop spreadsheet 'formula injection': text starting with = + - @ is made harmless."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
        return "'" + value
    return value


def sanitize(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in out.columns:
        if not (pd.api.types.is_numeric_dtype(out[col]) or pd.api.types.is_datetime64_any_dtype(out[col])):
            out[col] = out[col].map(_safe_cell)
    return out


def to_csv_bytes(df: pd.DataFrame) -> bytes:
    return sanitize(df).to_csv(index=False).encode("utf-8-sig")


def to_excel_bytes(sheets: dict) -> bytes:
    """sheets = {'Sheet name': DataFrame, ...} -> .xlsx file as bytes."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        for name, df in sheets.items():
            safe = sanitize(df)
            safe.to_excel(xw, sheet_name=str(name)[:31], index=False)
            ws = xw.sheets[str(name)[:31]]
            for idx, col in enumerate(safe.columns, start=1):
                width = max([len(str(col))] + [len(str(v)) for v in safe[col].head(200)])
                ws.column_dimensions[ws.cell(row=1, column=idx).column_letter].width = min(width + 2, 60)
                if pd.api.types.is_float_dtype(safe[col]):
                    for cell in ws.iter_cols(min_col=idx, max_col=idx, min_row=2):
                        for c in cell:
                            c.number_format = "#,##0.00"
            ws.freeze_panes = "A2"
    return buf.getvalue()
