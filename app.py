import os
import sqlite3
from datetime import date
import pandas as pd
import streamlit as st

try:
    import psycopg2
except ImportError:
    psycopg2 = None

st.set_page_config(page_title="KONEK Cashbook & General Ledger", layout="wide")


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

# Standard COA tailored for KONEK (Agric & General Contract Company)
KONEK_DEFAULT_COA = [
    ("1010", "Main Corporate Bank Account", "Asset", 1),
    ("1020", "Operations Bank Account", "Asset", 1),
    ("1030", "Office Petty Cash", "Asset", 1),
    ("1100", "Accounts Receivable (Trade Debtors)", "Asset", 0),
    ("1200", "Agricultural Inventory & Inputs (Seeds, Fertilizer, Chemicals)", "Asset", 0),
    ("1300", "Contract Work-in-Progress & Materials", "Asset", 0),
    ("1500", "Farm & Construction Machinery / Equipment", "Asset", 0),
    ("1510", "Motor Vehicles & Trucks", "Asset", 0),
    ("2010", "Accounts Payable (Trade Creditors / Suppliers)", "Liability", 0),
    ("2020", "Accrued Expenses & Taxes Payable", "Liability", 0),
    ("3010", "Share Capital / Owner's Equity", "Equity", 0),
    ("3020", "Retained Earnings", "Equity", 0),
    ("4010", "Agricultural Produce Sales", "Revenue", 0),
    ("4020", "General Contracting & Project Revenue", "Revenue", 0),
    ("4030", "Other Operating Income", "Revenue", 0),
    ("5010", "Cost of Sales - Agric Inputs & Livestock/Crop Direct Cost", "Expense", 0),
    ("5020", "Cost of Sales - Farm Labour & Harvesting", "Expense", 0),
    ("5030", "Cost of Sales - General Contract Materials & Subcontractors", "Expense", 0),
    ("5040", "Cost of Sales - Site Logistics & Equipment Hire", "Expense", 0),
    ("6010", "Salaries & Wages (Admin & Staff)", "Expense", 0),
    ("6020", "Fuel, Diesel & Generator Running Costs", "Expense", 0),
    ("6030", "Vehicle & Equipment Repairs / Maintenance", "Expense", 0),
    ("6040", "Transport, Travel & Logistics", "Expense", 0),
    ("6050", "Office Rent, Utilities & Internet", "Expense", 0),
    ("6060", "Bank Charges & Financial Fees", "Expense", 0),
]


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
        cur.executemany(
            f"INSERT INTO accounts VALUES ({PH}, {PH}, {PH}, {PH})",
            KONEK_DEFAULT_COA
        )

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


def normalize_account_type(raw_val):
    val = str(raw_val).strip().lower()
    if any(k in val for k in ["asset", "bank", "cash", "receivable", "inventory", "current asset", "fixed asset", "property"]):
        return "Asset"
    if any(k in val for k in ["liabil", "payable", "creditor", "loan", "tax", "accru"]):
        return "Liability"
    if any(k in val for k in ["equit", "capital", "retained", "reserve", "drawing"]):
        return "Equity"
    if any(k in val for k in ["rev", "income", "sale", "turnover"]):
        return "Revenue"
    return "Expense"


def detect_is_cash(acc_name, acc_type_raw):
    combined = (str(acc_name) + " " + str(acc_type_raw)).lower()
    if any(k in combined for k in ["bank", "petty cash", "cash in hand", "cash at bank", "cash account", "teller", "wallet"]):
        return 1
    return 0


def build_double_entry_gl(cb_df, acc_df):
    if cb_df.empty:
        return pd.DataFrame(columns=[
            "tx_date", "ref_no", "unit_tag", "description",
            "acc_code", "acc_name", "acc_type", "contra_acc", "debit", "credit"
        ])

    acc_map = acc_df.set_index("code").to_dict("index")
    postings = []

    for _, row in cb_df.iterrows():
        c_code = str(row["cash_acc"])
        g_code = str(row["gl_acc"])
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

st.title("📘 Cashbook → Automated General Ledger")
db_badge = "🟢 Cloud Database Connected" if USE_POSTGRES else "🟡 Local Mode"
st.caption(f"Daily Cashbook & Automated Double-Entry General Ledger for Agric & General Contracting | {db_badge}")

# Clean Business Division Filter (No confusing 'Partner' jargon)
default_divisions = ["General / All Operations", "Agric Operations", "General Contract", "Head Office / Admin"]
existing_divs = [d for d in cb_df["unit_tag"].dropna().unique().tolist() if d not in default_divisions] if not cb_df.empty else []
all_divisions = default_divisions + existing_divs

selected_div = st.sidebar.selectbox(
    "🏢 Filter by Cost Center / Division:",
    ["All Divisions (Consolidated)"] + all_divisions
)

if selected_div != "All Divisions (Consolidated)":
    filtered_cb = cb_df[cb_df["unit_tag"] == selected_div].copy()
else:
    filtered_cb = cb_df.copy()

gl_df = build_double_entry_gl(filtered_cb, acc_df)

total_in = filtered_cb.loc[filtered_cb["tx_type"] == "IN", "amount"].sum() if not filtered_cb.empty else 0.0
total_out = filtered_cb.loc[filtered_cb["tx_type"] == "OUT", "amount"].sum() if not filtered_cb.empty else 0.0
closing_cash = total_in - total_out
total_dr = gl_df["debit"].sum() if not gl_df.empty else 0.0
total_cr = gl_df["credit"].sum() if not gl_df.empty else 0.0

col1, col2, col3, col4 = st.columns(4)
col1.metric("Closing Cash & Bank Balance", f"{closing_cash:,.2f}")
col2.metric("Total Receipts (Money In)", f"{total_in:,.2f}")
col3.metric("Total Payments (Money Out)", f"{total_out:,.2f}")
col4.metric("Ledger Status", "BALANCED ✓" if round(total_dr, 2) == round(total_cr, 2) else "UNBALANCED", f"Dr = Cr ({total_dr:,.2f})")

tab1, tab2, tab3, tab4 = st.tabs([
    "1. Daily Cashbook Entry",
    "2. Automated General Ledger",
    "3. Trial Balance",
    "4. ⚙️ Manage / Upload Chart of Accounts"
])

# TAB 1: CASHBOOK
with tab1:
    st.subheader("Record Daily Cash / Bank Transaction")
    cash_accounts = acc_df[acc_df["is_cash"] == 1]
    gl_accounts = acc_df[acc_df["is_cash"] == 0]

    if cash_accounts.empty:
        cash_accounts = acc_df
    if gl_accounts.empty:
        gl_accounts = acc_df

    with st.form("cashbook_form", clear_on_submit=True):
        f1, f2, f3, f4 = st.columns(4)
        tx_date = f1.date_input("Transaction Date", value=date.today())
        ref_no = f2.text_input("Voucher / Receipt No.", placeholder="e.g. PV-001 or RC-001")
        tx_type = f3.selectbox("Transaction Type", ["OUT - Payment (Money Out)", "IN - Receipt (Money In)"])
        unit_tag = f4.selectbox("Division / Cost Center", all_divisions)

        f5, f6, f7, f8 = st.columns([2, 2, 3, 2])
        cash_choice = f5.selectbox(
            "Paid From / Received Into (Cash or Bank)",
            [f"{r['code']} - {r['name']}" for _, r in cash_accounts.iterrows()]
        )
        gl_choice = f6.selectbox(
            "Account Head (Chart of Accounts)",
            [f"{r['code']} - {r['name']} ({r['acc_type']})" for _, r in gl_accounts.iterrows()]
        )
        description = f7.text_input("Description / Particulars", placeholder="e.g. Purchase of fertilizer or Diesel for site...")
        amount = f8.number_input("Amount", min_value=0.01, step=500.0, format="%.2f")

        submitted = st.form_submit_button("+ Post Transaction to Cashbook & General Ledger")
        if submitted:
            if not ref_no.strip() or not description.strip():
                st.error("Please enter both a Voucher/Receipt Number and Description.")
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
                st.success("Transaction posted to Cashbook and General Ledger!")
                st.rerun()

    st.subheader("Cashbook Transaction Register")
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

        with st.expander("🗑️ Delete or Clear Transactions"):
            c_del1, c_del2 = st.columns(2)
            del_id = c_del1.number_input("Enter Transaction ID to delete:", min_value=1, step=1)
            if c_del1.button("Delete Selected Transaction ID"):
                conn = get_conn()
                cur = conn.cursor()
                cur.execute(f"DELETE FROM cashbook WHERE id = {PH}", (int(del_id),))
                conn.commit()
                cur.close()
                conn.close()
                st.success(f"Deleted transaction #{int(del_id)}")
                st.rerun()
            if c_del2.button("⚠️ Clear ALL Cashbook Demo Transactions"):
                conn = get_conn()
                cur = conn.cursor()
                cur.execute("DELETE FROM cashbook")
                conn.commit()
                cur.close()
                conn.close()
                st.success("All Cashbook transactions cleared!")
                st.rerun()
    else:
        st.info("No Cashbook transactions recorded yet. Use the form above to enter your first Receipt or Payment.")

# TAB 2: AUTOMATED GENERAL LEDGER
with tab2:
    st.subheader("Automated General Ledger (By Account Head)")
    if not gl_df.empty:
        st.download_button(
            "⬇️ Export Full General Ledger to Excel/CSV",
            data=gl_df.to_csv(index=False).encode("utf-8"),
            file_name="General_Ledger_Export.csv",
            mime="text/csv"
        )
        account_filter = st.selectbox(
            "Filter by Account Head:",
            ["All Accounts"] + [f"{r['code']} - {r['name']} ({r['acc_type']})" for _, r in acc_df.iterrows()]
        )
        for _, acc in acc_df.iterrows():
            if account_filter != "All Accounts" and not account_filter.startswith(str(acc["code"])):
                continue
            sub_gl = gl_df[gl_df["acc_code"] == str(acc["code"])].copy()
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
    else:
        st.info("General Ledger will populate automatically as soon as you enter transactions in Tab 1.")

# TAB 3: TRIAL BALANCE
with tab3:
    st.subheader("Automated Trial Balance")
    if not gl_df.empty:
        tb = gl_df.groupby(["acc_code", "acc_name", "acc_type"], as_index=False)[["debit", "credit"]].sum()
        tb["Net Balance"] = (tb["debit"] - tb["credit"]).abs()
        tb["Dr / Cr"] = tb.apply(lambda r: "Dr" if r["debit"] >= r["credit"] else "Cr", axis=1)
        st.dataframe(tb, use_container_width=True, hide_index=True)
    else:
        st.info("No transactions posted yet.")

# TAB 4: MANAGE / UPLOAD CHART OF ACCOUNTS (SELF-SERVICE FOR USER)
with tab4:
    st.subheader("⚙️ Input or Upload Your Own Chart of Accounts")
    st.write("You can upload your **Excel (`.xlsx`) or CSV Chart of Accounts** directly below, or add/remove individual accounts anytime.")

    col_up, col_manual = st.columns([3, 2])

    with col_up:
        st.markdown("#### 📤 Option 1: Upload Chart of Accounts File (`.xlsx` or `.csv`)")
        uploaded_file = st.file_uploader("Select your COA Excel or CSV file (e.g., KONEK COA UPDATE.xlsx)", type=["xlsx", "xls", "csv"])

        if uploaded_file is not None:
            try:
                if uploaded_file.name.lower().endswith(".csv"):
                    raw_coa = pd.read_csv(uploaded_file)
                else:
                    xls = pd.ExcelFile(uploaded_file)
                    sheet_choice = st.selectbox("Select Worksheet:", xls.sheet_names)
                    raw_coa = pd.read_excel(uploaded_file, sheet_name=sheet_choice)

                # Drop completely empty rows/columns
                raw_coa = raw_coa.dropna(how="all").reset_index(drop=True)
                st.write("**Preview of Uploaded File:**")
                st.dataframe(raw_coa.head(10), use_container_width=True)

                cols = raw_coa.columns.tolist()
                c1, c2, c3 = st.columns(3)
                code_col = c1.selectbox("Select Account Code / Number Column:", cols, index=0)
                name_col = c2.selectbox("Select Account Name / Description Column:", cols, index=min(1, len(cols) - 1))
                type_col = c3.selectbox("Select Account Type / Category Column:", ["(Auto-Detect from Name)"] + cols, index=min(2, len(cols)) if len(cols) > 2 else 0)

                replace_mode = st.radio(
                    "How should we import these accounts?",
                    ["Replace existing Chart of Accounts with this file", "Merge / Add to existing Chart of Accounts"]
                )

                if st.button("✅ Import Chart of Accounts Now"):
                    imported_rows = []
                    for idx, r in raw_coa.iterrows():
                        raw_code = str(r[code_col]).strip() if pd.notna(r[code_col]) else ""
                        raw_name = str(r[name_col]).strip() if pd.notna(r[name_col]) else ""
                        if not raw_name or raw_name.lower() in ["nan", "account name", "description", "account description"]:
                            continue
                        if not raw_code or raw_code.lower() in ["nan", "account code", "code", "gl code"]:
                            raw_code = f"ACC-{idx+100}"
                        # Clean trailing .0 from numeric codes read by pandas
                        if raw_code.endswith(".0"):
                            raw_code = raw_code[:-2]

                        raw_type = r[type_col] if type_col != "(Auto-Detect from Name)" and pd.notna(r[type_col]) else raw_name
                        norm_type = normalize_account_type(raw_type)
                        is_cash = detect_is_cash(raw_name, raw_type)
                        imported_rows.append((raw_code, raw_name, norm_type, is_cash))

                    if imported_rows:
                        conn = get_conn()
                        cur = conn.cursor()
                        if replace_mode.startswith("Replace"):
                            cur.execute("DELETE FROM accounts")
                        for code_v, name_v, type_v, cash_v in imported_rows:
                            cur.execute(f"DELETE FROM accounts WHERE code = {PH}", (code_v,))
                            cur.execute(
                                f"INSERT INTO accounts VALUES ({PH}, {PH}, {PH}, {PH})",
                                (code_v, name_v, type_v, cash_v)
                            )
                        conn.commit()
                        cur.close()
                        conn.close()
                        st.success(f"Successfully imported {len(imported_rows)} accounts into your Chart of Accounts!")
                        st.rerun()
                    else:
                        st.warning("No valid account rows found. Check the column selections above.")
            except Exception as e:
                st.error(f"Could not read file: {e}")

    with col_manual:
        st.markdown("#### ➕ Option 2: Add a Single Account Manually")
        with st.form("add_single_coa", clear_on_submit=True):
            new_code = st.text_input("Account Code", placeholder="e.g. 5050")
            new_name = st.text_input("Account Name", placeholder="e.g. Tractor Hire & Ploughing")
            new_type = st.selectbox("Account Type", ["Asset", "Liability", "Equity", "Revenue", "Expense"])
            is_cash_acc = st.checkbox("Check if this is a Bank or Cash Account (used for paying/receiving money)")
            if st.form_submit_button("+ Save Account"):
                if new_code.strip() and new_name.strip():
                    conn = get_conn()
                    cur = conn.cursor()
                    cur.execute(f"DELETE FROM accounts WHERE code = {PH}", (new_code.strip(),))
                    cur.execute(
                        f"INSERT INTO accounts VALUES ({PH}, {PH}, {PH}, {PH})",
                        (new_code.strip(), new_name.strip(), new_type, 1 if is_cash_acc else 0)
                    )
                    conn.commit()
                    cur.close()
                    conn.close()
                    st.success(f"Added {new_code} - {new_name}!")
                    st.rerun()

        st.markdown("#### 🔄 Quick Preset for Agric & General Contract")
        if st.button("Load Standard Agric & General Contract COA (25 Accounts)"):
            conn = get_conn()
            cur = conn.cursor()
            cur.execute("DELETE FROM accounts")
            cur.executemany(
                f"INSERT INTO accounts VALUES ({PH}, {PH}, {PH}, {PH})",
                KONEK_DEFAULT_COA
            )
            conn.commit()
            cur.close()
            conn.close()
            st.success("Loaded Agric & General Contract Chart of Accounts!")
            st.rerun()

    st.markdown("---")
    st.markdown(f"#### 📋 Current Active Chart of Accounts ({len(acc_df)} Accounts)")
    st.dataframe(acc_df, use_container_width=True, hide_index=True)
