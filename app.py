"""
KONEK Cashbook -> Automated General Ledger   (version 2)

Screens:  1 Cashbook  2 Journal & Opening balances  3 General ledger
          4 Trial balance  5 Vouchers & reversals  6 Chart of accounts  7 Audit log
Files:    db.py (database)  service.py (rules)  reports.py (reports)
          auth.py (login)   coa_import.py (upload reader)  coa_defaults.py (starter data)
"""
import os
import time
from datetime import timedelta

import pandas as pd
import streamlit as st

import auth
import coa_import
import reports
import service as S
from coa_defaults import ACCOUNT_TYPES, DEFAULT_COA
from db import Database
from money import fmt, to_kobo
from service import LedgerError

APP_TITLE = "KONEK Cashbook \u2192 Automated General Ledger"
COMPANY = "KONEK Investment & Services Ltd"
VERSION = "2.1"
CUR = "\u20a6"
ALL_DEPTS = "All Departments (Consolidated)"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

st.set_page_config(page_title="KONEK Cashbook & GL", page_icon="\U0001F4D8", layout="wide")


# ============================================================ small helpers
def secret(key, default=None):
    """Read a setting from Streamlit secrets, then from environment variables."""
    try:
        return st.secrets[key]
    except Exception:
        return os.environ.get(key, default)


def show_table(df: pd.DataFrame, money_cols=(), height=None):
    styled = df.style.format({c: "{:,.2f}" for c in money_cols if c in df.columns}, na_rep="")
    extra = {"height": height} if height else {}
    st.dataframe(styled, hide_index=True, width="stretch", **extra)


def download_excel(label: str, sheets: dict, filename: str, key: str):
    st.download_button(label, data=reports.to_excel_bytes(sheets), file_name=filename,
                       mime=XLSX, key=key)


def form_version() -> int:
    return st.session_state.setdefault("form_ver", 0)


def finish_and_refresh(kind: str, message: str):
    """Show a message on the next screen, give the forms fresh empty fields, reload."""
    if kind == "success" and "\u26a0" in message:
        kind = "warning"
    st.session_state["_flash"] = (kind, message)
    st.session_state["form_ver"] = form_version() + 1
    st.rerun()


def show_flash():
    item = st.session_state.pop("_flash", None)
    if item:
        {"success": st.success, "warning": st.warning, "error": st.error}[item[0]](item[1])


LABEL_STYLES = {
    "Code + type (compact)": "code_type",
    "Code only": "code",
    "Code + name + type": "full",
}


def options_for(df: pd.DataFrame, style: str = "full") -> dict:
    """Dropdown choices {label: code}. style: 'code', 'code_type' or 'full'."""
    if style == "code":
        return {f"{r.code}": r.code for r in df.itertuples()}
    if style == "code_type":
        return {f"{r.code} \u00b7 {r.acc_type}": r.code for r in df.itertuples()}
    return {f"{r.code} - {r.name}  [{r.acc_type}]": r.code for r in df.itertuples()}


def audit_event(user: str, action: str, detail: str = ""):
    try:
        with db.tx() as t:
            S.log_audit(t, user, action, detail)
    except Exception:
        pass  # never block a login because the log could not be written


# ============================================================== database
DB_URL = secret("DATABASE_URL")


@st.cache_resource(show_spinner="Connecting to the database\u2026")
def get_db(url):
    return Database(url or None).init_schema()


try:
    db = get_db(DB_URL)
except Exception as exc:
    st.title("\U0001F4D8 " + APP_TITLE)
    st.error("Could not connect to the database. Check the DATABASE_URL in the app secrets "
             "and that your Supabase project is not paused.")
    st.caption(f"Technical detail: {type(exc).__name__}")
    st.stop()


# ================================================================== login
def login_screen(users: dict):
    st.title("\U0001F4D8 " + APP_TITLE)
    st.caption(COMPANY)
    if not users:
        st.error("No users are set up yet, so the app is locked.")
        st.markdown("Add at least one `[users.yourname]` section (with `name`, `role` and "
                    "`password_hash`) to the app **Secrets**, then reload. "
                    "The step-by-step guide explains how.")
        st.stop()
    _, mid, _ = st.columns([1, 1.2, 1])
    with mid.form("login"):
        st.subheader("Sign in")
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        go = st.form_submit_button("Sign in", type="primary")
    if go:
        wait = st.session_state.get("locked_until", 0) - time.time()
        if wait > 0:
            mid.error(f"Too many wrong attempts. Please wait {int(wait) + 1} seconds.")
        else:
            user = auth.authenticate(users, username, password)
            if user:
                st.session_state.update(user=user, fails=0)
                audit_event(user["username"], "LOGIN")
                st.rerun()
            fails = st.session_state.get("fails", 0) + 1
            st.session_state["fails"] = fails
            if fails >= 5:
                st.session_state.update(locked_until=time.time() + 60, fails=0)
            if (username or "").strip().lower() in users:
                audit_event((username or "").strip().lower(), "LOGIN_FAILED")
            mid.error("Wrong username or password.")
    st.stop()


def require_login() -> dict:
    if st.session_state.get("user"):
        return st.session_state["user"]
    users = auth.clean_users(secret("users", {}))
    if not users and not db.is_pg:   # local testing only: no login needed
        st.session_state["user"] = {"username": "local", "name": "Local tester", "role": "admin"}
        return st.session_state["user"]
    login_screen(users)


user = require_login()
USER = user["username"]
CAN_POST, CAN_REVERSE = auth.can(user, "post"), auth.can(user, "reverse")
IS_ADMIN = auth.can(user, "coa")

# ================================================================ sidebar
today = S.today_local()
with st.sidebar:
    st.markdown(f"**{user['name']}**  \n`{user['role']}`")
    if st.button("Sign out"):
        audit_event(USER, "LOGOUT")
        st.session_state.pop("user", None)
        st.rerun()
    st.divider()
    st.header("Reporting period")
    picked = st.date_input("From \u2013 To", value=(today.replace(month=1, day=1), today),
                           format="YYYY-MM-DD")
    start, end = (picked[0], picked[-1]) if isinstance(picked, (tuple, list)) and picked else (picked, picked)
    if start > end:
        start, end = end, start
    all_depts = S.departments_list(db)
    dept_pick = st.selectbox("\U0001F3E2 Project / department", [ALL_DEPTS] + all_depts)
    dept_filter = None if dept_pick == ALL_DEPTS else dept_pick
    st.divider()
    style_pick = st.radio("Account dropdown shows", list(LABEL_STYLES), key="label_style",
                          help="In the entry forms. Reports always show the full account names. "
                               "With 'Code only' you can no longer search by name.")
    LABEL_STYLE = LABEL_STYLES[style_pick]
    st.divider()
    st.caption(f"Version {VERSION}")

# ================================================================== data
accounts = S.accounts_df(db)
active = accounts[accounts["is_active"] == 1]
cash_active = options_for(active[active["is_cash"] == 1], LABEL_STYLE)
cash_all = options_for(accounts[accounts["is_cash"] == 1])
head_active = options_for(active[active["is_cash"] == 0], LABEL_STYLE)
lines = S.load_lines(db, end, dept_filter)
m = reports.header_metrics(lines, start, end)
default_dept = all_depts.index("General / Head Office") if "General / Head Office" in all_depts else 0

# ================================================================== header
st.title("\U0001F4D8 " + APP_TITLE)
badge = "\U0001F7E2 Cloud database connected" if db.is_pg else \
    "\U0001F7E1 LOCAL TEST MODE (data is not saved permanently)"
st.caption(f"{COMPANY} \u2014 Daily cashbook & double-entry general ledger | {badge}")
if db.init_warnings and IS_ADMIN:
    st.warning("Some database safety steps could not be applied:\n\n- " + "\n- ".join(db.init_warnings))
show_flash()

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Opening Cash & Bank", fmt(m["opening"]))
c2.metric("Receipts (Money In)", fmt(m["receipts"]))
c3.metric("Payments (Money Out)", fmt(m["payments"]))
c4.metric("Closing Cash & Bank", fmt(m["closing"]))
if m["balanced"]:
    c5.metric("Ledger Status", "BALANCED \u2713", f"Dr = Cr ({fmt(m['total_dr'])})", delta_color="off")
else:
    c5.metric("Ledger Status", "OUT OF BALANCE \u2717",
              f"Dr \u2212 Cr = {fmt(m['total_dr'] - m['total_cr'])}", delta_color="inverse")
st.caption(f"Period {start} to {end} \u00b7 {dept_pick}"
           + (f" \u00b7 Other cash movements (transfers, opening balances and their reversals): {fmt(m['other'])}"
              if m["other"] else ""))


# ============================================================ 1. cashbook
def voucher_form(kind: str):
    prefix = "RC-" if kind == "RECEIPT" else "PV-"
    v = form_version()
    n = st.number_input("Number of account lines", 1, 10, 1, key=f"n_{kind}_{v}",
                        help="Use 1 for a normal entry. Use 2 or more to split one voucher "
                             "across several account heads.")
    with st.form(f"form_{kind}_{v}"):
        a, b, c, d = st.columns([1.2, 1.2, 2, 2])
        tx_date = a.date_input("Date", value=S.today_local(), key=f"d_{kind}_{v}")
        ref = b.text_input("Voucher no.", value=S.next_ref(db, prefix), max_chars=30, key=f"r_{kind}_{v}")
        cash_label = c.selectbox("Bank / cash account", list(cash_active), key=f"c_{kind}_{v}")
        dept = d.selectbox("Project / department", all_depts, index=default_dept, key=f"p_{kind}_{v}")
        narration = st.text_input("Narration / particulars (what is this for?)", max_chars=300,
                                  key=f"nar_{kind}_{v}")
        st.markdown("**Account lines** \u2014 " + ("where the money came from"
                    if kind == "RECEIPT" else "what the money was spent on"))
        items = []
        for i in range(int(n)):
            x, y, z = st.columns([4, 3, 2])
            head = x.selectbox(f"Account head {i + 1}", list(head_active), index=None,
                               placeholder="Type to search\u2026", key=f"h_{kind}_{v}_{i}")
            note = y.text_input(f"Line note {i + 1} (optional)", max_chars=200, key=f"ln_{kind}_{v}_{i}")
            amt = z.number_input(f"Amount {i + 1} ({CUR})", min_value=0.0, step=100.0, format="%.2f",
                                 key=f"a_{kind}_{v}_{i}")
            items.append({"acc_code": head_active.get(head), "note": note, "amount_k": to_kobo(amt)})
        go = st.form_submit_button(f"Post {kind.title()}", type="primary")
    if go:
        try:
            vid, warns = S.post_cash_voucher(
                db, kind=kind, tx_date=tx_date, ref_no=ref, cash_acc=cash_active[cash_label],
                items=items, unit_tag=dept, narration=narration, user=USER)
        except LedgerError as exc:
            st.error(str(exc))
            return
        msg = f"\u2705 {kind.title()} {ref.strip().upper()} posted."
        msg += outside_view_note(tx_date, dept) + month_note(tx_date)
        finish_and_refresh("warning" if warns else "success", msg + ("\n\n\u26a0\ufe0f " + " ".join(warns) if warns else ""))


def month_note(tx_date) -> str:
    t = S.today_local()
    if (tx_date.year, tx_date.month) == (t.year, t.month):
        return ""
    return (f" \u26a0\ufe0f It is dated {tx_date:%B %Y}, not the current month. If that is a mistake, "
            "use 'Correct a posted entry' to fix it.")


def outside_view_note(tx_date, dept) -> str:
    notes = []
    if not (start <= tx_date <= end):
        notes.append("its date is outside the reporting period in the sidebar")
    if dept_filter and dept != dept_filter:
        notes.append(f"it belongs to '{dept}', not the department you are viewing")
    return (" Note: " + " and ".join(notes) + ", so change the sidebar filters to see it.") if notes else ""


def transfer_form():
    v = form_version()
    st.caption("Use this to move money between your own bank/cash accounts \u2014 e.g. bank to bank, "
               "a cash withdrawal (bank \u2192 petty cash) or a deposit (petty cash \u2192 bank).")
    with st.form(f"form_TR_{v}"):
        a, b, c = st.columns([1.2, 1.2, 2])
        tx_date = a.date_input("Date", value=S.today_local(), key=f"td_{v}")
        ref = b.text_input("Voucher no.", value=S.next_ref(db, "TR-"), max_chars=30, key=f"tr_{v}")
        dept = c.selectbox("Project / department", all_depts, index=default_dept, key=f"tp_{v}")
        x, y, z = st.columns([2, 2, 1.5])
        src = x.selectbox("From account (money leaves)", list(cash_active), index=None,
                          placeholder="Choose\u2026", key=f"tf_{v}")
        dst = y.selectbox("To account (money arrives)", list(cash_active), index=None,
                          placeholder="Choose\u2026", key=f"tt_{v}")
        amt = z.number_input(f"Amount ({CUR})", min_value=0.0, step=100.0, format="%.2f", key=f"ta_{v}")
        narration = st.text_input("Narration / particulars", max_chars=300, key=f"tn_{v}")
        go = st.form_submit_button("Post Transfer", type="primary")
    if go:
        try:
            S.post_transfer(db, tx_date=tx_date, ref_no=ref, from_acc=cash_active.get(src),
                            to_acc=cash_active.get(dst), amount_k=to_kobo(amt), unit_tag=dept,
                            narration=narration, user=USER)
        except LedgerError as exc:
            st.error(str(exc))
            return
        finish_and_refresh("success", f"\u2705 Transfer {ref.strip().upper()} posted."
                           + outside_view_note(tx_date, dept) + month_note(tx_date))


# ============================================ correcting a posted voucher
def start_correction(where: str, vid: int):
    st.session_state["correcting"] = (where, int(vid))
    st.rerun()


def correction_panel(where: str):
    """A box that opens at the top of the tab: change what is wrong, save, done."""
    state = st.session_state.get("correcting")
    if not (state and state[0] == where):
        return
    vid = state[1]
    orig = S.get_voucher(db, vid)
    with st.container(border=True):
        if orig is None or orig["status"] != "ACTIVE" or orig["v_type"] == "REVERSAL":
            st.error("This voucher can no longer be corrected (it was already reversed or corrected).")
            if st.button("Close", key=f"cclose_{vid}"):
                st.session_state.pop("correcting", None)
                st.rerun()
            return
        st.markdown(f"### \u270f\ufe0f Correct voucher {orig['ref_no']}")
        st.caption("Change what is wrong and save. The app cancels the old voucher (a reversal dated on the "
                   "old voucher's own date) and posts your corrected one, in one step. Both stay on record "
                   "and nothing is deleted. Debits must equal credits.")
        used = {x["acc_code"] for x in orig["lines"]}
        choices = options_for(accounts[(accounts["is_active"] == 1) | accounts["code"].isin(used)], LABEL_STYLE)
        label_of = {code: label for label, code in choices.items()}
        depts = all_depts if orig["unit_tag"] in all_depts else all_depts + [orig["unit_tag"]]
        n = st.number_input("Number of lines", 2, 20, max(2, len(orig["lines"])), key=f"cn_{vid}")
        with st.form(f"cform_{vid}"):
            a, b, c = st.columns([1.2, 1.2, 2.5])
            new_date = a.date_input("Date", value=orig["tx_date"], key=f"cd_{vid}")
            new_ref = b.text_input("New voucher no.", value=S.suggest_correction_ref(db, orig["ref_no"]),
                                   max_chars=30, key=f"cr_{vid}")
            dept = c.selectbox("Project / department", depts, index=depts.index(orig["unit_tag"]),
                               key=f"cp_{vid}")
            narration = st.text_input("Narration / particulars", value=orig["narration"], max_chars=300,
                                      key=f"cnar_{vid}")
            rows = []
            for i in range(int(n)):
                old = orig["lines"][i] if i < len(orig["lines"]) else None
                w, x, y, z = st.columns([4, 3, 1.6, 1.6])
                idx = list(choices).index(label_of[old["acc_code"]]) if old and old["acc_code"] in label_of else None
                acc = w.selectbox(f"Account {i + 1}", list(choices), index=idx, placeholder="Type to search\u2026",
                                  key=f"ca_{vid}_{i}")
                note = x.text_input(f"Line note {i + 1}", value=old["note"] if old else "", max_chars=200,
                                    key=f"cl_{vid}_{i}")
                dr = y.number_input(f"Debit {i + 1}", min_value=0.0, value=(old["debit_k"] / 100) if old else 0.0,
                                    step=100.0, format="%.2f", key=f"cdr_{vid}_{i}")
                cr = z.number_input(f"Credit {i + 1}", min_value=0.0, value=(old["credit_k"] / 100) if old else 0.0,
                                    step=100.0, format="%.2f", key=f"ccr_{vid}_{i}")
                rows.append((acc, note, dr, cr))
            reason = st.text_input("Reason for the correction (required)", max_chars=200, key=f"cre_{vid}",
                                   placeholder="e.g. Wrong month typed")
            go = st.form_submit_button("Save correction", type="primary")
        cancel = st.button("Cancel", key=f"ccan_{vid}")
    if cancel:
        st.session_state.pop("correcting", None)
        st.rerun()
    if go:
        try:
            if any((dr or cr) and not acc for acc, _, dr, cr in rows):
                raise LedgerError("Please choose an account for every line that has an amount.")
            S.correct_voucher(
                db, vid, tx_date=new_date, ref_no=new_ref, unit_tag=dept, narration=narration,
                reason=reason, user=USER,
                lines=[{"acc_code": choices[acc], "note": note, "debit_k": to_kobo(dr), "credit_k": to_kobo(cr)}
                       for acc, note, dr, cr in rows if acc])
        except LedgerError as exc:
            st.error(str(exc))
            return
        st.session_state.pop("correcting", None)
        finish_and_refresh("success", f"\u2705 {orig['ref_no']} corrected. The new voucher is "
                           f"{new_ref.strip().upper()}." + outside_view_note(new_date, dept) + month_note(new_date))


def correct_picker(where: str):
    """Pick any recent voucher and open the correction box for it."""
    if not CAN_REVERSE:
        return
    summ = reports.voucher_summary(lines, start, end)
    summ = summ[(summ["Status"] == "ACTIVE") & (summ["Type"] != "REVERSAL")].head(60)
    if summ.empty:
        return
    st.markdown("#### Made a mistake? Correct a posted entry")
    st.caption("Wrong month, wrong amount or wrong account? Pick the voucher, fix it and save. "
               "Nothing is deleted; the old entry is cancelled and the corrected one is posted.")
    opts = {f"{r.Voucher} | {r.Date} | {r.Type} | {fmt(round(r.Amount * 100))} | {str(r.Narration)[:35]}":
            int(r.voucher_id) for r in summ.itertuples()}
    c1, c2 = st.columns([4, 1])
    pick = c1.selectbox("Voucher to correct", list(opts), index=None, placeholder="Choose a voucher\u2026",
                        key=f"cpick_{where}")
    if c2.button("\u270f\ufe0f Correct", key=f"cbtn_{where}", disabled=pick is None):
        start_correction(where, opts[pick])


def tab_cashbook():
    correction_panel("cashbook")
    st.subheader("Record cash / bank transaction")
    if not CAN_POST:
        st.info("Your login is read-only, so entry forms are hidden.")
    elif not cash_active:
        st.warning("There is no active bank/cash account. An admin must add one in the Chart of Accounts tab.")
    else:
        kind = st.radio("What are you recording?",
                        ["Receipt (money in)", "Payment (money out)", "Transfer between bank/cash accounts"],
                        horizontal=True, key="cb_kind")
        if kind.startswith("Transfer"):
            transfer_form()
        else:
            voucher_form("RECEIPT" if kind.startswith("Receipt") else "PAYMENT")
    st.divider()
    st.subheader("Cashbook register")
    pick = st.selectbox("Show", ["All bank & cash accounts"] + list(cash_all), key="reg_acc")
    code = cash_all.get(pick)
    reg, opening, closing = reports.cash_register(lines, start, end, code)
    if len(reg) == 1:
        st.info("No cashbook transactions in this period yet. Use the form above to record the first one.")
    show_table(reg, ["Receipt (In)", "Payment (Out)", "Balance"])
    st.caption(f"Opening balance {fmt(opening)} \u2192 closing balance {fmt(closing)}")
    download_excel("\u2b07\ufe0f Download cashbook (Excel)", {"Cashbook": reg},
                   f"cashbook_{start}_{end}.xlsx", "dl_cashbook")
    st.divider()
    correct_picker("cashbook")


# ============================================================ 2. journal
def tab_journal():
    if not CAN_POST:
        st.info("Your login is read-only, so entry forms are hidden.")
        return
    st.subheader("Journal & opening balances")
    mode = st.radio("What are you recording?",
                    ["Journal (non-cash adjustment: depreciation, accruals, corrections)",
                     "Opening balances (balances brought forward when you start using the app)"],
                    key="jv_mode")
    opening = mode.startswith("Opening")
    if opening:
        st.caption("Enter each account's balance on the day before you start. Debits must equal "
                   "credits \u2014 put the difference in an equity account such as Retained Earnings. "
                   "Bank/cash accounts are allowed here.")
        choices = options_for(active, LABEL_STYLE)
    else:
        st.caption("For entries that do not move money in or out of the bank (so bank/cash accounts "
                   "are not offered here). Debits must equal credits.")
        choices = head_active
    v = form_version()
    n = st.number_input("Number of lines", 2, 20, 2, key=f"jn_{v}_{opening}")
    with st.form(f"jform_{v}_{opening}"):
        a, b, c = st.columns([1.2, 1.2, 2.5])
        d_default = (start - timedelta(days=1)) if opening else S.today_local()
        tx_date = a.date_input("Date", value=d_default, key=f"jd_{v}_{opening}")
        ref = b.text_input("Voucher no.", value=S.next_ref(db, "OB-" if opening else "JV-"),
                           max_chars=30, key=f"jr_{v}_{opening}")
        dept = c.selectbox("Project / department", all_depts, index=default_dept, key=f"jp_{v}_{opening}")
        narration = st.text_input("Narration / particulars", max_chars=300, key=f"jnar_{v}_{opening}")
        rows = []
        for i in range(int(n)):
            w, x, y, z = st.columns([4, 3, 1.6, 1.6])
            acc = w.selectbox(f"Account {i + 1}", list(choices), index=None,
                              placeholder="Type to search\u2026", key=f"ja_{v}_{opening}_{i}")
            note = x.text_input(f"Line note {i + 1} (optional)", max_chars=200, key=f"jl_{v}_{opening}_{i}")
            dr = y.number_input(f"Debit {i + 1}", min_value=0.0, step=100.0, format="%.2f", key=f"jdr_{v}_{opening}_{i}")
            cr = z.number_input(f"Credit {i + 1}", min_value=0.0, step=100.0, format="%.2f", key=f"jcr_{v}_{opening}_{i}")
            rows.append((acc, note, dr, cr))
        go = st.form_submit_button("Post opening balances" if opening else "Post journal", type="primary")
    if go:
        try:
            if any((dr or cr) and not acc for acc, _, dr, cr in rows):
                raise LedgerError("Please choose an account for every line that has an amount.")
            S.post_journal(db, tx_date=tx_date, ref_no=ref, unit_tag=dept, narration=narration,
                           user=USER, opening=opening,
                           lines=[{"acc_code": choices[acc], "note": note, "debit_k": to_kobo(dr),
                                   "credit_k": to_kobo(cr)} for acc, note, dr, cr in rows if acc])
        except LedgerError as exc:
            st.error(str(exc))
            return
        finish_and_refresh("success", f"\u2705 {'Opening balances' if opening else 'Journal'} "
                           f"{ref.strip().upper()} posted." + outside_view_note(tx_date, dept) + month_note(tx_date))


# ============================================================ 3. ledger
def tab_ledger():
    st.subheader("General ledger")
    present = accounts[accounts["code"].isin(lines["acc_code"].unique())]
    pick = st.selectbox("Account", ["All accounts with activity"] + list(options_for(present)),
                        key="gl_pick")
    code = options_for(present).get(pick)
    ledgers = reports.all_ledgers(lines, start, end, code)
    if not ledgers:
        st.info("No ledger activity for this period yet.")
        return
    combined = pd.concat([led["df"].assign(Account=f"{led['code']} - {led['name']}") for led in ledgers],
                         ignore_index=True)
    combined = combined[["Account"] + [c for c in combined.columns if c != "Account"]]
    download_excel("\u2b07\ufe0f Download ledger (Excel)", {"General Ledger": combined},
                   f"general_ledger_{start}_{end}.xlsx", "dl_gl")
    shown = ledgers if code else ledgers[:60]
    if len(ledgers) > len(shown):
        st.caption(f"Showing the first {len(shown)} of {len(ledgers)} accounts \u2014 pick one account "
                   "above, or download the Excel file, to see the rest.")
    for led in shown:
        title = (f"{led['code']} - {led['name']}  ({led['acc_type']})   |   b/f {fmt(led['opening_k'])}  "
                 f"\u2192  c/f {fmt(led['closing_k'])}   |   {led['n']} entries")
        with st.expander(title, expanded=bool(code)):
            show_table(led["df"], ["Debit", "Credit", "Balance"])


# ============================================================ 4. trial balance
def tab_trial_balance():
    st.subheader(f"Trial balance as at {end}")
    hide = st.checkbox("Hide accounts with a zero balance", True, key="tb_hide")
    tb, dr, cr = reports.trial_balance(lines, end, hide)
    if tb.empty:
        st.info("Nothing posted yet, so the trial balance is empty.")
        return
    show_table(tb, ["Debit", "Credit"])
    if dr == cr:
        st.success(f"Trial balance agrees: total debits = total credits = {fmt(dr)}")
    else:
        st.error(f"Trial balance does NOT agree. Debits {fmt(dr)} vs credits {fmt(cr)}. "
                 "Please contact the administrator.")
    download_excel("\u2b07\ufe0f Download trial balance (Excel)", {"Trial Balance": tb},
                   f"trial_balance_{end}.xlsx", "dl_tb")


# ============================================================ 5. vouchers
def tab_vouchers():
    correction_panel("vouchers")
    st.subheader("Vouchers & reversals")
    st.caption("A posted voucher is never edited or deleted. To fix a mistake use 'Correct this voucher' "
               "(it cancels the old entry and posts the corrected one in one step), or reverse it to cancel it.")
    summ = reports.voucher_summary(lines, start, end)
    if summ.empty:
        st.info("No vouchers in this period.")
        return
    show_table(summ.drop(columns="voucher_id"), ["Amount"], height=300)
    options = {f"{r.Voucher} | {r.Date} | {r.Type} | {fmt(round(r.Amount * 100))} | {r.Status}": int(r.voucher_id)
               for r in summ.itertuples()}
    pick = st.selectbox("Open a voucher to see its lines or reverse it", list(options), index=None,
                        placeholder="Choose a voucher\u2026", key="vp")
    if not pick:
        return
    vid = options[pick]
    row = summ[summ["voucher_id"] == vid].iloc[0]
    st.markdown(f"**{row['Voucher']}** \u2014 {row['Narration']}  \n"
                f"{row['Date']} \u00b7 {row['Type']} \u00b7 {row['Department']} \u00b7 "
                f"posted by {row['Posted by']} \u00b7 status **{row['Status']}**")
    show_table(reports.voucher_detail(lines, vid), ["Debit", "Credit"])
    if not CAN_REVERSE:
        st.info("Your login cannot reverse vouchers.")
    elif row["Type"] == "REVERSAL":
        st.info("This is a reversal entry and cannot itself be reversed.")
    elif row["Status"] != "ACTIVE":
        st.info("This voucher has already been reversed.")
    else:
        if st.button("\u270f\ufe0f Correct this voucher (fix the date, amount or account)", key=f"vcor_{vid}"):
            start_correction("vouchers", vid)
        with st.form(f"rev_{vid}"):
            st.markdown("**Or just cancel it (reverse) without a replacement**")
            a, b = st.columns([1, 3])
            rdate = a.date_input("Reversal date", value=max(S.today_local(), pd.Timestamp(row["Date"]).date()))
            reason = b.text_input("Reason (required)", max_chars=200)
            sure = st.checkbox("I understand this posts an opposite entry and cannot be undone")
            go = st.form_submit_button("Reverse voucher", type="primary")
        if go:
            if not sure:
                st.error("Please tick the box to confirm.")
            else:
                try:
                    S.reverse_voucher(db, vid, rdate, reason, USER)
                except LedgerError as exc:
                    st.error(str(exc))
                else:
                    finish_and_refresh("success", f"\u2705 {row['Voucher']} reversed.")


# ============================================================ 6. chart of accounts
def tab_coa():
    st.subheader(f"Chart of accounts \u2014 {len(accounts)} accounts ({len(active)} active)")
    view = pd.DataFrame({"Code": accounts["code"], "Name": accounts["name"], "Type": accounts["acc_type"],
                         "Bank/Cash": accounts["is_cash"].map({1: "Yes", 0: ""}),
                         "Status": accounts["is_active"].map({1: "Active", 0: "Hidden"})})
    show_table(view, height=320)
    download_excel("\u2b07\ufe0f Download chart of accounts (Excel)", {"Chart of Accounts": view},
                   "chart_of_accounts.xlsx", "dl_coa")
    if not IS_ADMIN:
        st.info("Only an admin can change the chart of accounts.")
        return

    st.divider()
    st.markdown("#### Add or edit one account")
    st.caption("If the code already exists its name is updated. Type and bank/cash setting can only be "
               "changed while no entries use the account.")
    with st.form("coa_one"):
        a, b, c, d = st.columns([1, 3, 1.5, 1])
        code = a.text_input("Code")
        name = b.text_input("Account name")
        acc_type = c.selectbox("Type", ACCOUNT_TYPES)
        is_cash = d.checkbox("Bank / cash")
        go = st.form_submit_button("Save account")
    if go:
        try:
            res = S.upsert_accounts(db, [(code, name, acc_type, int(is_cash))], USER)
        except LedgerError as exc:
            st.error(str(exc))
        else:
            note = " (its type/cash setting was kept because entries already use it)" if res["locked"] else ""
            finish_and_refresh("success", f"\u2705 Account {code.strip()} saved{note}.")

    st.markdown("#### Hide or show an account")
    st.caption("Accounts are never deleted. A hidden account disappears from the entry forms but stays "
               "in all reports. It needs a zero balance first.")
    labels = options_for(accounts)
    pick = st.selectbox("Account", list(labels), index=None, placeholder="Choose\u2026", key="coa_hide")
    if pick:
        cur_active = int(accounts.loc[accounts["code"] == labels[pick], "is_active"].iloc[0])
        if st.button("Hide this account" if cur_active else "Show this account again"):
            try:
                S.set_account_active(db, labels[pick], not cur_active, USER)
            except LedgerError as exc:
                st.error(str(exc))
            else:
                finish_and_refresh("success", "\u2705 Done.")

    st.markdown("#### Upload a chart of accounts (Excel or CSV)")
    st.caption("This is a safe merge: new accounts are added and existing ones are updated. "
               "Nothing is ever deleted. You can correct the preview before saving.")
    up = st.file_uploader("Choose a file", type=["xlsx", "xls", "csv"], key="coa_up")
    if up is not None:
        try:
            preview = coa_import.parse_coa(coa_import.read_table(up))
        except Exception as exc:
            st.error(f"Could not read that file: {exc}")
            preview = pd.DataFrame()
        if preview.empty:
            st.warning("No accounts were found. Each row needs a numeric code (3-12 digits) and a name.")
        else:
            st.caption(f"{len(preview)} accounts found. Fix any wrong Type or Bank/Cash tick below.")
            edited = st.data_editor(
                preview, hide_index=True, disabled=["Code"], key=f"coa_prev_{up.name}",
                column_config={"Type": st.column_config.SelectboxColumn("Type", options=ACCOUNT_TYPES, required=True),
                               "Cash": st.column_config.CheckboxColumn("Bank / cash")})
            if st.button("Add / update these accounts", type="primary"):
                try:
                    res = S.upsert_accounts(db, [(r.Code, r.Name, r.Type, int(r.Cash)) for r in edited.itertuples()], USER)
                except LedgerError as exc:
                    st.error(str(exc))
                else:
                    finish_and_refresh("success", f"\u2705 {res['added']} added, {res['updated']} updated, "
                                       f"{res['unchanged']} unchanged"
                                       + (f", type kept for {len(res['locked'])} in use" if res["locked"] else "") + ".")

    missing = [r for r in DEFAULT_COA if r[0] not in set(accounts["code"])]
    if missing and st.button(f"Add the {len(missing)} missing KONEK default accounts"):
        S.upsert_accounts(db, missing, USER)
        finish_and_refresh("success", f"\u2705 {len(missing)} default accounts added.")

    st.markdown("#### Projects / departments")
    st.write(", ".join(all_depts))
    with st.form("dept_add"):
        new_dept = st.text_input("New project / department name", max_chars=80)
        if st.form_submit_button("Add department"):
            try:
                S.add_department(db, new_dept, USER)
            except LedgerError as exc:
                st.error(str(exc))
            else:
                finish_and_refresh("success", f"\u2705 Department '{new_dept.strip()}' added.")


# ============================================================ 7. audit
def tab_audit():
    st.subheader("Audit log (latest 500 actions)")
    show_table(S.audit_df(db), height=500)


names = ["1. Cashbook", "2. Journal & Opening Balances", "3. General Ledger", "4. Trial Balance",
         "5. Vouchers & Reversals", "6. Chart of Accounts"] + (["7. Audit Log"] if auth.can(user, "audit") else [])
tabs = st.tabs(names)
for tab, fn in zip(tabs, [tab_cashbook, tab_journal, tab_ledger, tab_trial_balance, tab_vouchers,
                          tab_coa, tab_audit]):
    with tab:
        fn()
