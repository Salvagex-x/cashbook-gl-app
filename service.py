"""
Business rules for the ledger: posting vouchers, reversing them, managing the
chart of accounts, and the audit log.

Rules that protect the books (all checked here, and again by the database):
  * every voucher balances (total debits = total credits)
  * a posted voucher is never edited or deleted - it is REVERSED instead
  * voucher numbers are unique
  * accounts are never deleted, only deactivated (and only at zero balance)
  * money moves between bank/cash accounts only through the Transfer form
"""
import re
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from coa_defaults import ACCOUNT_TYPES
from db import INTEGRITY_ERRORS, Database
from money import fmt, kobo_to_db, series_to_kobo, to_kobo

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

MAX_REF = 40
MAX_TEXT = 300


class LedgerError(Exception):
    """A problem the user can fix. The message is shown on screen as-is."""


def today_local(tz_name: str = "Africa/Lagos") -> date:
    """Today's date in Nigeria (Streamlit Cloud servers run on UTC)."""
    try:
        return datetime.now(ZoneInfo(tz_name)).date()
    except Exception:
        return (datetime.now(timezone.utc) + timedelta(hours=1)).date()


# --------------------------------------------------------------------- helpers
def _clean_text(value, label: str, required: bool = True, max_len: int = MAX_TEXT) -> str:
    text = " ".join(str(value or "").split())
    if required and not text:
        raise LedgerError(f"{label} is required.")
    if len(text) > max_len:
        raise LedgerError(f"{label} is too long (maximum {max_len} characters).")
    return text


def _clean_ref(value) -> str:
    ref = _clean_text(value, "Voucher number", max_len=MAX_REF).upper()
    return ref


def _to_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def _check_date(d) -> date:
    if not isinstance(d, date):
        raise LedgerError("Please choose a valid date.")
    if d < date(2000, 1, 1) or d > date.today() + timedelta(days=366):
        raise LedgerError(f"The date {d.isoformat()} looks wrong. Please check it.")
    return d


def log_audit(tx, user: str, action: str, detail: str = ""):
    tx.execute("INSERT INTO audit_log (user_name, action, detail) VALUES (?, ?, ?)",
               (user, action, detail[:1000]))


def _accounts_map(tx, codes) -> dict:
    codes = sorted(set(codes))
    if not codes:
        return {}
    marks = ", ".join("?" for _ in codes)
    rows = tx.fetchall(
        f"SELECT code, name, acc_type, is_cash, is_active FROM accounts WHERE code IN ({marks})",
        codes)
    return {r[0]: {"name": r[1], "acc_type": r[2], "is_cash": int(r[3]),
                   "is_active": int(r[4])} for r in rows}


# ------------------------------------------------------------- core posting
def _post_in_tx(tx, *, tx_date, ref_no, v_type, unit_tag, narration, lines, user,
                reverses_id=None, corrects_id=None, check_active=True, cash_rule=None):
    """Validate and insert one voucher + its lines inside an open transaction."""
    db = tx.db
    d = _check_date(tx_date)
    ref = _clean_ref(ref_no)
    narration = _clean_text(narration, "Narration / particulars")

    clean = []
    for ln in lines:
        dk, ck = int(ln.get("debit_k", 0)), int(ln.get("credit_k", 0))
        if dk < 0 or ck < 0:
            raise LedgerError("Amounts cannot be negative.")
        if dk == 0 and ck == 0:
            continue
        if dk > 0 and ck > 0:
            raise LedgerError("A line cannot have both a debit and a credit amount.")
        clean.append({"acc_code": str(ln["acc_code"]).strip(), "debit_k": dk, "credit_k": ck,
                      "note": _clean_text(ln.get("note", ""), "Line note", required=False)})
    if len(clean) < 2:
        raise LedgerError("A voucher needs at least two lines (a debit side and a credit side).")
    total_d = sum(x["debit_k"] for x in clean)
    total_c = sum(x["credit_k"] for x in clean)
    if total_d != total_c:
        raise LedgerError(f"Debits ({fmt(total_d)}) must equal credits ({fmt(total_c)}). "
                          f"Difference: {fmt(abs(total_d - total_c))}.")

    dept = tx.fetchone("SELECT is_active FROM departments WHERE name = ?", (unit_tag,))
    if dept is None:
        raise LedgerError(f"Project / department '{unit_tag}' does not exist.")

    accmap = _accounts_map(tx, [x["acc_code"] for x in clean])
    for x in clean:
        info = accmap.get(x["acc_code"])
        if info is None:
            raise LedgerError(f"Account {x['acc_code']} does not exist.")
        if check_active and not info["is_active"]:
            raise LedgerError(f"Account {x['acc_code']} - {info['name']} is inactive.")
    if cash_rule:
        cash_rule(accmap, clean)

    if tx.fetchone("SELECT 1 FROM vouchers WHERE ref_no = ?", (ref,)):
        raise LedgerError(f"Voucher number {ref} has already been used. Please use a new number.")

    vid = tx.insert_id(
        "INSERT INTO vouchers (tx_date, ref_no, v_type, unit_tag, narration, reverses_id, "
        "corrects_id, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (d.isoformat(), ref, v_type, unit_tag, narration, reverses_id, corrects_id, user))
    for x in clean:
        tx.execute(
            "INSERT INTO voucher_lines (voucher_id, acc_code, description, debit, credit) "
            "VALUES (?, ?, ?, ?, ?)",
            (vid, x["acc_code"], x["note"],
             kobo_to_db(x["debit_k"], db.is_pg), kobo_to_db(x["credit_k"], db.is_pg)))
    log_audit(tx, user, f"POST_{v_type}", f"{ref} | {d.isoformat()} | {fmt(total_d)} | {unit_tag}")
    return vid, accmap


def _post(db: Database, **kw):
    try:
        with db.tx() as t:
            return _post_in_tx(t, **kw)
    except INTEGRITY_ERRORS as exc:
        raise LedgerError("The database refused this entry (for example a duplicate voucher "
                          "number). Please check it and try again.") from exc


def post_cash_voucher(db, *, kind, tx_date, ref_no, cash_acc, items, unit_tag, narration, user):
    """
    kind = 'RECEIPT' (money in) or 'PAYMENT' (money out).
    items = [{'acc_code', 'note', 'amount_k'}, ...]  - the account heads (can be several = split).
    Returns (voucher_id, [warnings]).
    """
    if kind not in ("RECEIPT", "PAYMENT"):
        raise LedgerError("Unknown voucher kind.")
    items = [i for i in items if int(i.get("amount_k", 0)) > 0]
    if not items:
        raise LedgerError("Enter at least one account line with an amount greater than zero.")
    if any(not i.get("acc_code") for i in items):
        raise LedgerError("Please choose an account head for every line that has an amount.")
    total = sum(int(i["amount_k"]) for i in items)

    if kind == "RECEIPT":
        lines = [{"acc_code": cash_acc, "debit_k": total, "credit_k": 0}]
        lines += [{"acc_code": i["acc_code"], "debit_k": 0, "credit_k": int(i["amount_k"]),
                   "note": i.get("note", "")} for i in items]
    else:
        lines = [{"acc_code": i["acc_code"], "debit_k": int(i["amount_k"]), "credit_k": 0,
                  "note": i.get("note", "")} for i in items]
        lines.append({"acc_code": cash_acc, "debit_k": 0, "credit_k": total})

    def rule(acc, _lines=None):
        if not acc[cash_acc]["is_cash"]:
            raise LedgerError(f"{cash_acc} is not a bank or cash account.")
        for i in items:
            if acc[i["acc_code"]]["is_cash"]:
                raise LedgerError(
                    f"{i['acc_code']} - {acc[i['acc_code']]['name']} is a bank/cash account. "
                    "To move money between bank/cash accounts use the Transfer form.")

    vid, accmap = _post(db, tx_date=tx_date, ref_no=ref_no, v_type=kind, unit_tag=unit_tag,
                        narration=narration, lines=lines, user=user, cash_rule=rule)
    warnings = []
    for i in items:
        t = accmap[i["acc_code"]]["acc_type"]
        if kind == "RECEIPT" and t == "Expense":
            warnings.append(f"Receipt credited to an expense account ({i['acc_code']}). "
                            "That is fine for a refund; otherwise double-check the account.")
        if kind == "PAYMENT" and t == "Revenue":
            warnings.append(f"Payment debited to a revenue account ({i['acc_code']}). "
                            "That is fine for a refund to a customer; otherwise double-check.")
    return vid, warnings


def post_transfer(db, *, tx_date, ref_no, from_acc, to_acc, amount_k, unit_tag, narration, user):
    """Move money between two bank/cash accounts (bank-to-bank, withdrawal, deposit)."""
    amount_k = int(amount_k)
    if amount_k <= 0:
        raise LedgerError("Enter an amount greater than zero.")
    if not from_acc or not to_acc:
        raise LedgerError("Choose both the 'from' and the 'to' account.")
    if from_acc == to_acc:
        raise LedgerError("The 'from' and 'to' accounts must be different.")

    def rule(acc, _lines=None):
        for code in (from_acc, to_acc):
            if not acc[code]["is_cash"]:
                raise LedgerError(f"{code} - {acc[code]['name']} is not a bank or cash account.")

    vid, _ = _post(db, tx_date=tx_date, ref_no=ref_no, v_type="TRANSFER", unit_tag=unit_tag,
                   narration=narration, user=user, cash_rule=rule,
                   lines=[{"acc_code": to_acc, "debit_k": amount_k, "credit_k": 0},
                          {"acc_code": from_acc, "debit_k": 0, "credit_k": amount_k}])
    return vid


def post_journal(db, *, tx_date, ref_no, lines, unit_tag, narration, user, opening=False):
    """
    A journal voucher for non-cash adjustments (depreciation, accruals, corrections).
    With opening=True it records OPENING BALANCES and may include bank/cash accounts.
    lines = [{'acc_code', 'debit_k', 'credit_k', 'note'}, ...]
    """
    def rule(acc, _lines=None):
        if opening:
            return
        for code, info in acc.items():
            if info["is_cash"]:
                raise LedgerError(
                    f"{code} - {info['name']} is a bank/cash account. Money in or out of bank/cash "
                    "must go through the Cashbook tab so the cashbook stays complete.")

    vid, _ = _post(db, tx_date=tx_date, ref_no=ref_no, v_type="OPENING" if opening else "JOURNAL",
                   unit_tag=unit_tag, narration=narration, lines=lines, user=user, cash_rule=rule)
    return vid


def reverse_voucher(db: Database, voucher_id: int, reverse_date, reason: str, user: str) -> int:
    """Cancel a posted voucher by posting an equal-and-opposite one. Nothing is deleted."""
    reason = _clean_text(reason, "Reason for reversal", max_len=200)
    if len(reason) < 5:
        raise LedgerError("Please give a short reason for the reversal (at least 5 characters).")
    rd = _check_date(reverse_date)
    try:
        with db.tx() as t:
            v = t.fetchone("SELECT id, tx_date, ref_no, v_type, unit_tag, status "
                           "FROM vouchers WHERE id = ?", (int(voucher_id),))
            if v is None:
                raise LedgerError("Voucher not found.")
            _, orig_date, orig_ref, v_type, unit_tag, status = v
            if v_type == "REVERSAL":
                raise LedgerError("A reversal voucher cannot itself be reversed. "
                                  "Post a new voucher instead.")
            if status != "ACTIVE":
                raise LedgerError(f"Voucher {orig_ref} has already been reversed.")
            if rd < _to_date(orig_date):
                raise LedgerError("The reversal date cannot be earlier than the original voucher date.")

            rows = t.fetchall("SELECT acc_code, description, debit, credit FROM voucher_lines "
                              "WHERE voucher_id = ? ORDER BY id", (int(voucher_id),))
            swapped = [{"acc_code": r[0], "note": r[1],
                        "debit_k": to_kobo(r[3]), "credit_k": to_kobo(r[2])} for r in rows]

            base, ref, n = f"REV-{orig_ref}", f"REV-{orig_ref}", 1
            while t.fetchone("SELECT 1 FROM vouchers WHERE ref_no = ?", (ref,)):
                n += 1
                ref = f"{base}-{n}"

            new_id, _ = _post_in_tx(
                t, tx_date=rd, ref_no=ref, v_type="REVERSAL", unit_tag=unit_tag,
                narration=f"Reversal of {orig_ref}: {reason}"[:MAX_TEXT], lines=swapped,
                user=user, reverses_id=int(voucher_id), check_active=False)
            res = t.execute("UPDATE vouchers SET status = 'REVERSED' WHERE id = ? AND status = 'ACTIVE'",
                            (int(voucher_id),))
            if res.rowcount != 1:
                raise LedgerError("This voucher was reversed by someone else a moment ago.")
            log_audit(t, user, "REVERSE_VOUCHER", f"{orig_ref} -> {ref} | {reason}")
    except INTEGRITY_ERRORS as exc:
        raise LedgerError("The database refused the reversal. Please try again.") from exc
    return new_id


# ----------------------------------------------------------- correcting a voucher
def _rule_for_type(v_type: str):
    """The bank/cash rules a voucher of this type must keep when it is corrected."""
    def rule(acc, lines):
        cash_codes = {c for c, i in acc.items() if i["is_cash"]}
        if v_type == "JOURNAL":
            if cash_codes:
                raise LedgerError("A journal cannot use bank/cash accounts. Money in or out of bank/cash "
                                  "must stay a receipt, payment or transfer.")
        elif v_type in ("RECEIPT", "PAYMENT"):
            cash_lines = [x for x in lines if x["acc_code"] in cash_codes]
            other = [x for x in lines if x["acc_code"] not in cash_codes]
            if not cash_lines or not other:
                raise LedgerError(f"A {v_type.lower()} needs one bank/cash account and at least one other account.")
            if len(cash_codes) > 1:
                raise LedgerError("A receipt or payment can use only one bank/cash account. "
                                  "Moving money between bank/cash accounts is a Transfer.")
            if v_type == "RECEIPT" and any(x["credit_k"] for x in cash_lines):
                raise LedgerError("In a receipt the bank/cash account must be DEBITED (money in).")
            if v_type == "PAYMENT" and any(x["debit_k"] for x in cash_lines):
                raise LedgerError("In a payment the bank/cash account must be CREDITED (money out).")
        elif v_type == "TRANSFER":
            if len(cash_codes) != len(acc) or len(lines) != 2:
                raise LedgerError("A transfer has exactly two lines, both bank/cash accounts.")
    return rule


def get_voucher(db: Database, voucher_id: int):
    """Header and lines of one voucher (amounts in kobo), or None if it does not exist."""
    with db.tx() as t:
        v = t.fetchone("SELECT id, tx_date, ref_no, v_type, unit_tag, narration, status "
                       "FROM vouchers WHERE id = ?", (int(voucher_id),))
        if v is None:
            return None
        rows = t.fetchall("SELECT acc_code, description, debit, credit FROM voucher_lines "
                          "WHERE voucher_id = ? ORDER BY id", (int(voucher_id),))
    return {"id": v[0], "tx_date": _to_date(v[1]), "ref_no": v[2], "v_type": v[3], "unit_tag": v[4],
            "narration": v[5], "status": v[6],
            "lines": [{"acc_code": r[0], "note": r[1], "debit_k": to_kobo(r[2]), "credit_k": to_kobo(r[3])}
                      for r in rows]}


def suggest_correction_ref(db: Database, ref_no: str) -> str:
    """RC-0007 -> RC-0007-C, then RC-0007-C2 ... (the old number stays on record)."""
    base = re.sub(r"-C\d*$", "", ref_no)
    ref, n = f"{base}-C", 1
    while db.scalar("SELECT 1 FROM vouchers WHERE ref_no = ?", (ref,)):
        n += 1
        ref = f"{base}-C{n}"
    return ref


def correct_voucher(db: Database, voucher_id: int, *, tx_date, ref_no, unit_tag, narration, lines,
                    reason: str, user: str) -> int:
    """
    Fix a posted voucher (wrong date, amount, account ...) in ONE step:
      1. the old voucher is cancelled by a reversal dated on the old voucher's own date
         (so a wrong-month entry nets to zero in the wrong month), and
      2. the corrected voucher is posted with the date and details you give.
    Both steps succeed together or neither happens. Nothing is deleted.
    lines = [{'acc_code', 'debit_k', 'credit_k', 'note'}, ...]
    """
    reason = _clean_text(reason, "Reason for correction", max_len=200)
    if len(reason) < 5:
        raise LedgerError("Please give a short reason for the correction (at least 5 characters).")
    try:
        with db.tx() as t:
            v = t.fetchone("SELECT id, tx_date, ref_no, v_type, unit_tag, status FROM vouchers WHERE id = ?",
                           (int(voucher_id),))
            if v is None:
                raise LedgerError("Voucher not found.")
            _, orig_date, orig_ref, v_type, orig_unit, status = v
            if v_type == "REVERSAL":
                raise LedgerError("A reversal entry cannot be corrected. Correct the original voucher instead.")
            if status != "ACTIVE":
                raise LedgerError(f"Voucher {orig_ref} has already been reversed or corrected.")

            old = t.fetchall("SELECT acc_code, description, debit, credit FROM voucher_lines "
                             "WHERE voucher_id = ? ORDER BY id", (int(voucher_id),))
            swapped = [{"acc_code": r[0], "note": r[1], "debit_k": to_kobo(r[3]), "credit_k": to_kobo(r[2])}
                       for r in old]
            base, rev_ref, n = f"REV-{orig_ref}", f"REV-{orig_ref}", 1
            while t.fetchone("SELECT 1 FROM vouchers WHERE ref_no = ?", (rev_ref,)):
                n += 1
                rev_ref = f"{base}-{n}"
            rev_id, _ = _post_in_tx(
                t, tx_date=_to_date(orig_date), ref_no=rev_ref, v_type="REVERSAL", unit_tag=orig_unit,
                narration=f"Reversal of {orig_ref} (correction): {reason}"[:MAX_TEXT], lines=swapped,
                user=user, reverses_id=int(voucher_id), check_active=False)
            res = t.execute("UPDATE vouchers SET status = 'REVERSED' WHERE id = ? AND status = 'ACTIVE'",
                            (int(voucher_id),))
            if res.rowcount != 1:
                raise LedgerError("This voucher was changed by someone else a moment ago. Please reload.")

            new_id, _ = _post_in_tx(
                t, tx_date=tx_date, ref_no=ref_no, v_type=v_type, unit_tag=unit_tag, narration=narration,
                lines=lines, user=user, corrects_id=int(voucher_id), cash_rule=_rule_for_type(v_type))
            log_audit(t, user, "CORRECT_VOUCHER", f"{orig_ref} -> {_clean_ref(ref_no)} | {reason}")
    except INTEGRITY_ERRORS as exc:
        raise LedgerError("The database refused the correction. Please check it and try again.") from exc
    return new_id


# ------------------------------------------------------------ numbering / lists
def next_ref(db: Database, prefix: str) -> str:
    """Suggest the next voucher number, e.g. RC-0001, RC-0002 ..."""
    df = db.q("SELECT ref_no FROM vouchers WHERE ref_no LIKE ?", (prefix + "%",))
    nums = []
    for ref in df["ref_no"].tolist():
        m = re.fullmatch(re.escape(prefix) + r"(\d+)", str(ref))
        if m:
            nums.append(int(m.group(1)))
    return f"{prefix}{(max(nums) + 1 if nums else 1):04d}"


def departments_list(db: Database, active_only: bool = True) -> list[str]:
    sql = "SELECT name FROM departments" + (" WHERE is_active = 1" if active_only else "")
    return db.q(sql + " ORDER BY name")["name"].tolist()


def add_department(db: Database, name: str, user: str):
    name = _clean_text(name, "Department name", max_len=80)
    with db.tx() as t:
        if t.fetchone("SELECT 1 FROM departments WHERE name = ?", (name,)):
            raise LedgerError("That department already exists.")
        t.execute("INSERT INTO departments (name) VALUES (?)", (name,))
        log_audit(t, user, "ADD_DEPARTMENT", name)


# ------------------------------------------------------ chart of accounts
def accounts_df(db: Database, active_only: bool = False) -> pd.DataFrame:
    sql = "SELECT code, name, acc_type, is_cash, is_active FROM accounts"
    if active_only:
        sql += " WHERE is_active = 1"
    df = db.q(sql + " ORDER BY code")
    for col in ("is_cash", "is_active"):
        df[col] = pd.to_numeric(df[col]).astype(int)
    return df


def upsert_accounts(db: Database, rows, user: str) -> dict:
    """
    Add new accounts and update existing ones. NEVER deletes anything.
    An account that already has postings keeps its type and bank/cash setting
    (changing them would silently change old reports); only its name can change.
    rows = iterable of (code, name, acc_type, is_cash)
    """
    wanted = {}
    for code, name, acc_type, is_cash in rows:
        code = str(code).strip()
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,20}", code):
            raise LedgerError(f"Account code '{code}' is not valid (letters, digits, . _ - only, max 20).")
        if acc_type not in ACCOUNT_TYPES:
            raise LedgerError(f"Account {code}: type must be one of {', '.join(ACCOUNT_TYPES)}.")
        wanted[code] = (_clean_text(name, f"Name for account {code}", max_len=120),
                        acc_type, 1 if int(is_cash) else 0)

    out = {"added": 0, "updated": 0, "unchanged": 0, "locked": []}
    with db.tx() as t:
        for code, (name, acc_type, is_cash) in wanted.items():
            cur = t.fetchone("SELECT name, acc_type, is_cash FROM accounts WHERE code = ?", (code,))
            if cur is None:
                t.execute("INSERT INTO accounts (code, name, acc_type, is_cash) VALUES (?, ?, ?, ?)",
                          (code, name, acc_type, is_cash))
                out["added"] += 1
                continue
            if (cur[0], cur[1], int(cur[2])) == (name, acc_type, is_cash):
                out["unchanged"] += 1
                continue
            used = t.fetchone("SELECT 1 FROM voucher_lines WHERE acc_code = ?", (code,))
            if used and (cur[1], int(cur[2])) != (acc_type, is_cash):
                out["locked"].append(code)
                acc_type, is_cash = cur[1], int(cur[2])
            t.execute("UPDATE accounts SET name = ?, acc_type = ?, is_cash = ? WHERE code = ?",
                      (name, acc_type, is_cash, code))
            out["updated"] += 1
        log_audit(t, user, "COA_UPSERT",
                  f"added {out['added']}, updated {out['updated']}, "
                  f"unchanged {out['unchanged']}, kept-type {len(out['locked'])}")
    return out


def set_account_active(db: Database, code: str, active: bool, user: str):
    """Hide an account from new entries (or show it again). Needs a zero balance to hide."""
    with db.tx() as t:
        row = t.fetchone("SELECT name FROM accounts WHERE code = ?", (code,))
        if row is None:
            raise LedgerError("Account not found.")
        if not active:
            d, c = t.fetchone("SELECT COALESCE(SUM(debit), 0), COALESCE(SUM(credit), 0) "
                              "FROM voucher_lines WHERE acc_code = ?", (code,))
            bal = to_kobo(d) - to_kobo(c)
            if bal != 0:
                raise LedgerError(f"{code} - {row[0]} still has a balance of {fmt(abs(bal))}. "
                                  "An account can only be hidden when its balance is zero.")
        t.execute("UPDATE accounts SET is_active = ? WHERE code = ?", (1 if active else 0, code))
        log_audit(t, user, "ACCOUNT_ACTIVATE" if active else "ACCOUNT_DEACTIVATE", f"{code} - {row[0]}")


# ----------------------------------------------------------------- reading data
def load_lines(db: Database, end_date, unit_tag: str | None = None) -> pd.DataFrame:
    """Every posted line up to end_date (optionally one department), amounts in kobo."""
    sql = """
        SELECT l.id AS line_id, v.id AS voucher_id, v.tx_date, v.ref_no, v.v_type, v.unit_tag,
               v.narration, v.status, v.created_by, l.acc_code, a.name AS acc_name,
               a.acc_type, a.is_cash, l.description AS note, l.debit, l.credit,
               rv.ref_no AS reverses_ref, rv.v_type AS reverses_type, cv.ref_no AS corrects_ref
        FROM voucher_lines l
        JOIN vouchers v ON v.id = l.voucher_id
        JOIN accounts a ON a.code = l.acc_code
        LEFT JOIN vouchers rv ON rv.id = v.reverses_id
        LEFT JOIN vouchers cv ON cv.id = v.corrects_id
        WHERE v.tx_date <= ?"""
    params = [_to_date(end_date).isoformat()]
    if unit_tag:
        sql += " AND v.unit_tag = ?"
        params.append(unit_tag)
    df = db.q(sql + " ORDER BY v.tx_date, v.id, l.id", params)

    df["tx_date"] = pd.to_datetime(df["tx_date"])
    df["debit_k"] = series_to_kobo(df["debit"])
    df["credit_k"] = series_to_kobo(df["credit"])
    df["is_cash"] = pd.to_numeric(df["is_cash"]).fillna(0).astype(int)
    df["acc_code"] = df["acc_code"].astype(str)
    df["note"] = df["note"].fillna("").astype(str)
    df["reverses_ref"] = df["reverses_ref"].fillna("").astype(str)
    df["reverses_type"] = df["reverses_type"].fillna("").astype(str)
    df["corrects_ref"] = df["corrects_ref"].fillna("").astype(str)
    df["desc"] = df["note"].where(df["note"] != "", df["narration"])
    df["acc_label"] = df["acc_code"] + " - " + df["acc_name"].astype(str)
    return df.drop(columns=["debit", "credit"])


def audit_df(db: Database, limit: int = 500) -> pd.DataFrame:
    return db.q("SELECT id, at, user_name, action, detail FROM audit_log "
                "ORDER BY id DESC LIMIT ?", (int(limit),))
