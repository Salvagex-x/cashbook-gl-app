import os
import sqlite3
from datetime import date
import pandas as pd
import streamlit as st

try:
    import psycopg2
except ImportError:
    psycopg2 = None

st.set_page_config(page_title="Cashbook to General Ledger", layout="wide")


def get_db_url():
    try:
        if "DATABASE_URL" in st.secrets:
            return st.secrets["DATABASE_URL"]
    except Exception:
        pass
    return os.environ.get("DATABASE_URL")


DB_URL = get_db_url()
USE_POSTGRES = bool(DB_URL and psycopg2 is not None)
PH = "%s" if USE_POSTGRES else "?"


def get_conn():
    if USE_POSTGRES:
        return psycopg2.connect(DB_URL)
    return sqlite3.connect("ledger_data.db", check_same_thread=False)


def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            code TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            acc_type TEXT NOT NULL,
            is_cash INTEGER NOT NULL DEFAULT 0
        )
    """)

    if USE_POSTGRES:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS cashbook (
                id SERIAL PRIMARY KEY,
                tx_date TEXT NOT NULL,
                ref_no TEXT NOT NULL,
                tx_type TEXT NOT NULL,
                cash_acc TEXT NOT NULL,
                gl_acc TEXT NOT NULL,
                unit_tag TEXT NOT NULL,
                description TEXT NOT NULL,
                amount DOUBLE PRECISION NOT NULL
            )
        """)
    else:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS cashbook (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tx_date TEXT NOT NULL,
                ref_no TEXT NOT NULL,
                tx_type TEXT NOT NULL,
                cash_acc TEXT NOT NULL,
                gl_acc TEXT NOT NULL,
                unit_tag TEXT NOT NULL,
                description TEXT NOT NULL,
                amount REAL NOT NULL
            )
        """)

    cur.execute("SELECT COUNT(*) FROM accounts")
    if cur.fetchone()[0] == 0:
        default_accounts = [
            ("1010", "Main Bank Account", "Asset", 1),
            ("1020", "Office Petty Cash", "Asset", 1),
            ("3010", "Partner Capital / Funding", "Equity", 0),
            ("3020", "Partner Drawings / Withdrawals", "Equity", 0),
            ("4010", "Sales & Service Revenue", "Revenue", 0),
            ("5010", "Direct Project / Field Costs", "Expense", 0),
            ("6010", "Fuel & Transport Logistics", "Expense", 0),
            ("6020", "Office & Admin Expenses", "Expense", 0),
        ]
        cur.executemany(f"INSERT INTO accounts VALUES ({PH}, {PH}, {PH}, {PH})", default_accounts)

    cur.execute("SELECT COUNT(*) FROM cashbook")
    if cur.fetchone()[0] == 0:
        sample_tx = [
            ("2026-10-01", "RC-001", "IN", "1010", "3010", "Partner 1 Account", "Initial Operating Fund Allocation", 500000.0),
            ("2026-10-05", "RC-002", "IN", "1010", "4010", "Unit A - Operations", "Client Settlement Invoice #12", 275000.0),
            ("2026-10-08", "PV-001", "OUT", "1010", "5010", "Unit A - Operations", "Field Contractor Procurement", 140000.0),
            ("2026-10-12", "PV-002", "OUT", "1020", "6010", "Unit A - Operations", "Diesel & Vehicle Dispatch", 45000.0),
            ("2026-10-18", "PV-003", "OUT", "1010", "3020", "Partner 2 Account", "Partner Account Disbursement", 90000.0),
        ]
        cur.executemany(f"""
            INSERT INTO cashbook (tx_date, ref_no, tx_type, cash_acc, gl_acc, unit_tag, description, amount)
            VALUES ({PH}, {PH}, {PH}, {PH}, {PH}, {PH}, {PH}, {PH})
        """, sample_tx)

    conn.commit()
    cur.close()
    conn.close()


def fetch_df(query):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(query)
    rows = cur.fetchall()
    cols = [desc[0] for desc in cur.description]
    cur.close()
    conn.close()
    return pd.DataFrame(rows, columns=cols)


def build_double_entry_gl(cb_df, acc_df):
    if cb_df.empty:
        return pd.DataFrame(columns=[
            "tx_date", "ref_no", "unit_tag", "description",
            "acc_code", "acc_name", "acc_type", "contra_acc", "debit", "credit"
        ])

    acc_map = acc_df.set_index("code").to_dict("index")
    postings = []

    for _, row in cb_df.iterrows():
        c_code = row["cash_acc"]
        g_code = row["gl_acc"]
        c_info = acc_map.get(c_code, {"name": c_code, "acc_type": "Asset"})
        g_info = acc_map.get(g_code, {"name": g_code, "acc_type": "Expense"})

        if row["tx_type"] == "IN":
            postings.append({
                "tx_date": row["tx_date"], "ref_no": row["ref_no"], "unit_tag": row["unit_tag"],
                "description": row["description"], "acc_code": c_code, "acc_name": c_info["name"],
                "acc_type": c_info["acc_type"], "contra_acc": f"{g_code} - {g_info['name']}",
                "debit": float(row["amount"]), "credit": 0.0
            })
            postings.append({
                "tx_date": row["tx_date"], "ref_no": row["ref_no"], "unit_tag": row["unit_tag"],
                "description": row["description"], "acc_code": g_code, "acc_name": g_info["name"],
                "acc_type": g_info["acc_type"], "contra_acc": f"{c_code} - {c_info['name']}",
                "debit": 0.0, "credit": float(row["amount"])
            })
        else:
            postings.append({
                "tx_date": row["tx_date"], "ref_no": row["ref_no"], "unit_tag": row["unit_tag"],
                "description": row["description"], "acc_code": g_code, "acc_name": g_info["name"],
                "acc_type": g_info["acc_type"], "contra_acc": f"{c_code} - {c_info['name']}",
                "debit": float(row["amount"]), "credit": 0.0
            })
            postings.append({
                "tx_date": row["tx_date"], "ref_no": row["ref_no"], "unit_tag": row["unit_tag"],
                "description": row["description"], "acc_code": c_code, "acc_name": c_info["name"],
                "acc_type": c_info["acc_type"], "contra_acc": f"{g_code} - {g_info['name']}",
                "debit": 0.0, "credit": float(row["amount"])
            })

    return pd.DataFrame(postings)


init_db()
acc_df = fetch_df("SELECT * FROM accounts ORDER BY code")
cb_df = fetch_df("SELECT * FROM cashbook ORDER BY tx_date, id")

st.title("📘 Cashbook → Automated General Ledger App")
db_badge = "🟢 Connected to Cloud PostgreSQL (Supabase)" if USE_POSTGRES else "🟡 Local SQLite Mode"
st.caption(f"Single-Entry Cashbook with Real-Time Double-Entry General Ledger | {db_badge}")

unit_options = ["All Units / Partners (Consolidated)", "Unit A - Operations", "Partner 1 Account", "Partner 2 Account"]
selected_unit = st.sidebar.selectbox("🔍 Filter by Account / Partner Unit:", unit_options)

if selected_unit != "All Units / Partners (Consolidated)":
    filtered_cb = cb_df[cb_df["unit_tag"] == selected_unit].copy()
else:
    filtered_cb = cb_df.copy()

gl_df = build_double_entry_gl(filtered_cb, acc_df)

total_in = filtered_cb.loc[filtered_cb["tx_type"] == "IN", "amount"].sum() if not filtered_cb.empty else 0.0
total_out = filtered_cb.loc[filtered_cb["tx_type"] == "OUT", "amount"].sum() if not filtered_cb.empty else 0.0
closing_cash = total_in - total_out
total_dr = gl_df["debit"].sum() if not gl_df.empty else 0.0
total_cr = gl_df["credit"].sum() if not gl_df.empty else 0.0

col1, col2, col3, col4 = st.columns(4)
col1.metric("Closing Cash/Bank Balance", f"{closing_cash:,.2f}")
col2.metric("Total Receipts (Money In)", f"{total_in:,.2f}")
col3.metric("Total Payments (Money Out)", f"{total_out:,.2f}")
col4.metric("Ledger Status", "BALANCED ✓" if round(total_dr, 2) == round(total_cr, 2) else "UNBALANCED", f"Dr = Cr ({total_dr:,.2f})")

tab1, tab2, tab3 = st.tabs([
    "1. Daily Cashbook (Single Entry)",
    "2. Automated General Ledger",
    "3. Trial Balance & Chart of Accounts"
])

with tab1:
    st.subheader("Post New Cashbook Transaction")
    cash_accounts = acc_df[acc_df["is_cash"] == 1]
    gl_accounts = acc_df[acc_df["is_cash"] == 0]

    with st.form("cashbook_form", clear_on_submit=True):
        f1, f2, f3, f4 = st.columns(4)
        tx_date = f1.date_input("Date", value=date.today())
        ref_no = f2.text_input("Voucher / Ref No.", placeholder="e.g. PV-105")
        tx_type = f3.selectbox("Transaction Type", ["IN - Receipt (Money In)", "OUT - Payment (Money Out)"])
        unit_tag = f4.selectbox("Unit / Partner Account", ["Unit A - Operations", "Partner 1 Account", "Partner 2 Account"])

        f5, f6, f7, f8 = st.columns([2, 2, 3, 2])
        cash_choice = f5.selectbox("Cash / Bank Account", [f"{r['code']} - {r['name']}" for _, r in cash_accounts.iterrows()])
        gl_choice = f6.selectbox("GL Category (Counter-Account)", [f"{r['code']} - {r['name']}" for _, r in gl_accounts.iterrows()])
        description = f7.text_input("Particulars / Description", placeholder="Enter transaction details...")
        amount = f8.number_input("Amount", min_value=0.01, step=100.0, format="%.2f")

        submitted = st.form_submit_button("+ Save & Auto-Post to General Ledger")
        if submitted:
            if not ref_no.strip() or not description.strip():
                st.error("Please enter both a Reference Number and Description.")
            else:
                conn = get_conn()
                cur = conn.cursor()
                cur.execute(f"""
                    INSERT INTO cashbook (tx_date, ref_no, tx_type, cash_acc, gl_acc, unit_tag, description, amount)
                    VALUES ({PH}, {PH}, {PH}, {PH}, {PH}, {PH}, {PH}, {PH})
                """, (
                    str(tx_date),
                    ref_no.strip(),
                    "IN" if tx_type.startswith("IN") else "OUT",
                    cash_choice.split(" - ")[0],
                    gl_choice.split(" - ")[0],
                    unit_tag,
                    description.strip(),
                    float(amount)
                ))
                conn.commit()
                cur.close()
                conn.close()
                st.success("Transaction permanently saved to Cloud Database!")
                st.rerun()

    st.subheader("Chronological Cashbook Register")
    if not filtered_cb.empty:
        disp_cb = filtered_cb.copy()
        disp_cb["Receipt (In)"] = disp_cb.apply(lambda r: r["amount"] if r["tx_type"] == "IN" else 0.0, axis=1)
        disp_cb["Payment (Out)"] = disp_cb.apply(lambda r: r["amount"] if r["tx_type"] == "OUT" else 0.0, axis=1)
        disp_cb["Running Balance"] = (disp_cb["Receipt (In)"] - disp_cb["Payment (Out)"]).cumsum()
        st.dataframe(
            disp_cb[["id", "tx_date", "ref_no", "unit_tag", "description", "cash_acc", "gl_acc", "Receipt (In)", "Payment (Out)", "Running Balance"]],
            use_container_width=True,
            hide_index=True
        )

with tab2:
    st.subheader("Automated General Ledger by Account")
    if not gl_df.empty:
        st.download_button(
            "⬇️ Download Full General Ledger (CSV)",
            data=gl_df.to_csv(index=False).encode("utf-8"),
            file_name="General_Ledger_Export.csv",
            mime="text/csv"
        )
        account_filter = st.selectbox(
            "Select GL Account to Inspect:",
            ["All Accounts"] + [f"{r['code']} - {r['name']} ({r['acc_type']})" for _, r in acc_df.iterrows()]
        )
        for _, acc in acc_df.iterrows():
            if account_filter != "All Accounts" and not account_filter.startswith(acc["code"]):
                continue
            sub_gl = gl_df[gl_df["acc_code"] == acc["code"]].copy()
            if sub_gl.empty and account_filter == "All Accounts":
                continue

            if not sub_gl.empty:
                if acc["acc_type"] in ["Asset", "Expense"]:
                    sub_gl["Running Balance"] = (sub_gl["debit"] - sub_gl["credit"]).cumsum()
                else:
                    sub_gl["Running Balance"] = (sub_gl["credit"] - sub_gl["debit"]).cumsum()
                closing = sub_gl["Running Balance"].iloc[-1]
            else:
                closing = 0.0

            with st.expander(f"📂 {acc['code']} - {acc['name']} ({acc['acc_type']}) | Closing Balance: {closing:,.2f}", expanded=True):
                if not sub_gl.empty:
                    st.dataframe(
                        sub_gl[["tx_date", "ref_no", "unit_tag", "description", "contra_acc", "debit", "credit", "Running Balance"]],
                        use_container_width=True,
                        hide_index=True
                    )

with tab3:
    c_left, c_right = st.columns([2, 1])
    with c_left:
        st.subheader("Automated Trial Balance")
        if not gl_df.empty:
            tb = gl_df.groupby(["acc_code", "acc_name", "acc_type"], as_index=False)[["debit", "credit"]].sum()
            tb["Net Balance"] = (tb["debit"] - tb["credit"]).abs()
            tb["Dr / Cr"] = tb.apply(lambda r: "Dr" if r["debit"] >= r["credit"] else "Cr", axis=1)
            st.dataframe(tb, use_container_width=True, hide_index=True)

    with c_right:
        st.subheader("Add New GL Account")
        with st.form("new_acc_form", clear_on_submit=True):
            new_code = st.text_input("Account Code", placeholder="e.g. 6050")
            new_name = st.text_input("Account Name", placeholder="e.g. Equipment Maintenance")
            new_type = st.selectbox("Account Type", ["Asset", "Liability", "Equity", "Revenue", "Expense"])
            is_cash_acc = st.checkbox("Is this a Cash/Bank Account?")
            if st.form_submit_button("+ Create Account"):
                if new_code.strip() and new_name.strip():
                    try:
                        conn = get_conn()
                        cur = conn.cursor()
                        cur.execute(
                            f"INSERT INTO accounts VALUES ({PH}, {PH}, {PH}, {PH})",
                            (new_code.strip(), new_name.strip(), new_type, 1 if is_cash_acc else 0)
                        )
                        conn.commit()
                        cur.close()
                        conn.close()
                        st.success("Account added to Cloud Database!")
                        st.rerun()
                    except Exception:
                        st.error("Account code already exists.")
