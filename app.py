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

# All 115 Accounts extracted directly from KONEK COA UPDATE_085213.xlsx
KONEK_DEFAULT_COA = [
    ("11001", "Land & Buildings", "Asset", 0),
    ("11002", "Plant & Machinery", "Asset", 0),
    ("11003", "Office Equipment", "Asset", 0),
    ("11004", "Furniture & Fittings", "Asset", 0),
    ("11005", "Motor Vehicles", "Asset", 0),
    ("11006", "IT Infrastruction", "Asset", 0),
    ("11011", "Accum. Depreciation - Land & Buildings", "Asset", 0),
    ("11012", "Accum. Depreciation - Plant & Machinery", "Asset", 0),
    ("11013", "Accum. Depreciation - Office Equipment", "Asset", 0),
    ("11014", "Accum. Depreciation - Furniture & Fittings", "Asset", 0),
    ("11015", "Accum. Depreciation - Motor Vehicles", "Asset", 0),
    ("11016", "Accum. Depreciation - IT Infrastruction", "Asset", 0),
    ("12001", "Construction Materials", "Asset", 0),
    ("12002", "IT Infrastruction (Inventory)", "Asset", 0),
    ("12003", "Oil & Gas", "Asset", 0),
    ("12004", "General Supply", "Asset", 0),
    ("12011", "Goods in Transit", "Asset", 0),
    ("12012", "Consumables", "Asset", 0),
    ("13001", "Trade Debtors control account", "Asset", 0),
    ("13002", "Construction customers Receivable", "Asset", 0),
    ("13003", "Trade Debtors - IT receivable customers", "Asset", 0),
    ("13004", "Trade Debtors oil & gas", "Asset", 0),
    ("13005", "Trade debtors general supplies receivable", "Asset", 0),
    ("13006", "Retention Receivable", "Asset", 0),
    ("13007", "Contract Assets/Work certified not yet billed", "Asset", 0),
    ("13008", "Other receivables", "Asset", 0),
    ("13011", "Staff Loan and Advances", "Asset", 0),
    ("13012", "Prepaid Expenses", "Asset", 0),
    ("13013", "Withholding Tax Credits", "Asset", 0),
    ("14001", "ZENITH BANK", "Asset", 1),
    ("14002", "FIRST BANK", "Asset", 1),
    ("14003", "GTB", "Asset", 1),
    ("14011", "Petty Cash", "Asset", 1),
    ("21001", "Trade Creditor Control Account", "Liability", 0),
    ("21002", "Trade Creditor - Construction Material Suppliers", "Liability", 0),
    ("21003", "Trade Creditor - IT Equipment supplier", "Liability", 0),
    ("21004", "Trade Creditor - OIL & Gas suppliers", "Liability", 0),
    ("21005", "Trade Creditor - General Suppliers", "Liability", 0),
    ("21006", "Subcontractors payable", "Liability", 0),
    ("21007", "Accrued Expenses", "Liability", 0),
    ("21008", "Other payables", "Liability", 0),
    ("21009", "Supplier deposit received", "Liability", 0),
    ("21011", "Accrued Expense - Utilties", "Liability", 0),
    ("21012", "Accrued Expense - Audit Fees", "Liability", 0),
    ("21013", "Accrued Expense - Consultancy", "Liability", 0),
    ("21014", "Accrued Taxes", "Liability", 0),
    ("21015", "Unremmited Deductions", "Liability", 0),
    ("21021", "Directors' Current Account", "Liability", 0),
    ("22001", "Bank Loan", "Liability", 0),
    ("22002", "Bank Loan - Access Bank", "Liability", 0),
    ("22011", "Directors' Loan", "Liability", 0),
    ("22012", "Other Long Term Facility", "Liability", 0),
    ("23001", "Share Capital", "Equity", 0),
    ("23011", "Retained Earnings", "Equity", 0),
    ("23021", "Directors' Fund", "Equity", 0),
    ("31000", "Sales - Construction for project X", "Revenue", 0),
    ("32000", "Sales - Construction for project Y", "Revenue", 0),
    ("33000", "Sales - IT infrastructure", "Revenue", 0),
    ("34000", "Sales - Oil & Gas", "Revenue", 0),
    ("35000", "Sales - General Services", "Revenue", 0),
    ("31011", "Other Income", "Revenue", 0),
    ("41001", "Direct Material Project X", "Expense", 0),
    ("41010", "Direct Material Project Y", "Expense", 0),
    ("41020", "Purchases - Oil & Gas", "Expense", 0),
    ("41030", "Purchases - General Supplies", "Expense", 0),
    ("41100", "Direct Labour Construction workers", "Expense", 0),
    ("41101", "Direct Labour - Masons", "Expense", 0),
    ("41102", "Direct Labour - Carpenters", "Expense", 0),
    ("41103", "Direct Labour - Steel Fixers", "Expense", 0),
    ("41104", "Direct Labour - Electricians", "Expense", 0),
    ("41105", "Direct Labour - Plumbers", "Expense", 0),
    ("41106", "Direct Labour - Welders/Fabricators", "Expense", 0),
    ("41107", "Direct Labour - Painters", "Expense", 0),
    ("41108", "Direct Labour - Tiles", "Expense", 0),
    ("41109", "Direct Labour - POP/Ceiling Installers", "Expense", 0),
    ("41110", "Direct Labour - Equipment Operators", "Expense", 0),
    ("41111", "Direct Labour - General Labourers/Casual Workers", "Expense", 0),
    ("41120", "DIRECT LABOUR PROJECT Y", "Expense", 0),
    ("41130", "Direct Labour - IT Engineers & Technicians", "Expense", 0),
    ("41140", "Direct Labour - CCTV/Network Installers", "Expense", 0),
    ("41150", "Direct Labour - Oil & Gas Field Technicians", "Expense", 0),
    ("41161", "Direct Labour - Overtime", "Expense", 0),
    ("41162", "Consumables office", "Expense", 0),
    ("41163", "Diesel, Fuel and Lubricants", "Expense", 0),
    ("41164", "Electricity", "Expense", 0),
    ("41165", "Office Repairs and Maintenance", "Expense", 0),
    ("41166", "Maintenance", "Expense", 0),
    ("42001", "Salaries and Wages", "Expense", 0),
    ("42002", "Staff Welfare", "Expense", 0),
    ("42003", "Audit fees", "Expense", 0),
    ("42004", "Utility", "Expense", 0),
    ("42005", "Communication and Subscriptions", "Expense", 0),
    ("42006", "Fuel for motor vehicles", "Expense", 0),
    ("42007", "Printing and Stationery", "Expense", 0),
    ("42008", "Motor Vehicles Repairs & Maintenance", "Expense", 0),
    ("42009", "Security Expenses", "Expense", 0),
    ("42010", "General Expenses", "Expense", 0),
    ("42011", "Directors Remuneration", "Expense", 0),
    ("42012", "Business Promotion & Publicity", "Expense", 0),
    ("42013", "Transport & Travel", "Expense", 0),
    ("42014", "Bank Charges", "Expense", 0),
    ("42015", "Interest on Loan & Overdrafts", "Expense", 0),
    ("42016", "Insurance expense", "Expense", 0),
    ("42017", "Statutory Compliance Expenses", "Expense", 0),
    ("42018", "Cleaning & Sanitation Expense", "Expense", 0),
    ("42019", "Hotel & Accommodation", "Expense", 0),
    ("42020", "Miscellaneous expense", "Expense", 0),
    ("42021", "Annual Regulatory Dues", "Expense", 0),
    ("42022", "Office service charge", "Expense", 0),
    ("43011", "Depreciation - Land & Buildings", "Expense", 0),
    ("43012", "Depreciation - Plant & Machinery", "Expense", 0),
    ("43013", "Depreciation - Office Equipment", "Expense", 0),
    ("43014", "Depreciation - Furniture & Fittings", "Expense", 0),
    ("43015", "Depreciation - Motor Vehicles", "Expense", 0),
    ("43016", "Depreciation - IT Infrastruction", "Expense", 0),
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


def smart_parse_coa_df(raw_df):
    df = raw_df.dropna(how="all").dropna(axis=1, how="all").reset_index(drop=True)
    if df.shape[1] < 2:
        return []

    cols = df.columns.tolist()
    code_col, name_col = cols[0], cols[1]
    imported = []
    current_section = "Expense"
    is_cash_section = 0

    for _, r in df.iterrows():
        raw_c = str(r[code_col]).strip() if pd.notna(r[code_col]) else ""
        raw_n = str(r[name_col]).strip() if pd.notna(r[name_col]) else ""
        if raw_c.endswith(".0"):
            raw_c = raw_c[:-2]

        if (not raw_c or raw_c.lower() == "nan") and raw_n and raw_n.lower() != "nan":
            sec_lower = raw_n.lower()
            if any(k in sec_lower for k in ["bank", "cash"]):
                current_section = "Asset"
                is_cash_section = 1
            elif any(k in sec_lower for k in ["asset", "inventor", "debtor", "receiv", "prepay"]):
                current_section = "Asset"
                is_cash_section = 0
            elif any(k in sec_lower for k in ["creditor", "liabil", "payable", "loan"]):
                current_section = "Liability"
                is_cash_section = 0
            elif any(k in sec_lower for k in ["equit", "capital", "fund"]):
                current_section = "Equity"
                is_cash_section = 0
            elif any(k in sec_lower for k in ["rev", "sales", "income"]):
                current_section = "Revenue"
                is_cash_section = 0
            elif any(k in sec_lower for k in ["cost", "expense", "overhead"]):
                current_section = "Expense"
                is_cash_section = 0
            continue

        if not raw_c or not raw_n or raw_c.lower() in ["nan", "account code", "code", "gl code"] or raw_n.lower() in ["nan", "account name", "description"]:
            continue

        name_clean = " ".join(raw_n.split())
        acc_type = current_section
        is_cash = is_cash_section
        if raw_c.startswith("14") or any(k in name_clean.lower() for k in ["zenith", "first bank", "gtb", "petty cash", "cash at bank"]):
            if not raw_c.startswith("22"):
                acc_type = "Asset"
                is_cash = 1

        imported.append((raw_c, name_clean, acc_type, is_cash))
    return imported


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

st.title("📘 KONEK Cashbook → Automated General Ledger")
db_badge = "🟢 Cloud Database Connected" if USE_POSTGRES else "🟡 Local Mode"
st.caption(f"KONEK Investment & Services Ltd — Daily Cashbook & Double-Entry General Ledger | {db_badge}")

default_divisions = [
    "General / Head Office",
    "Construction - Project X",
    "Construction - Project Y",
    "IT Infrastructure",
    "Oil & Gas",
    "General Supply & Services",
    "Agric Operations"
]
existing_divs = [d for d in cb_df["unit_tag"].dropna().unique().tolist() if d not in default_divisions] if not cb_df.empty else []
all_divisions = default_divisions + existing_divs

selected_div = st.sidebar.selectbox(
    "🏢 Filter by Project / Department:",
    ["All Departments (Consolidated)"] + all_divisions
)

if selected_div != "All Departments (Consolidated)":
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
        unit_tag = f4.selectbox("Project / Department", all_divisions)

        f5, f6, f7, f8 = st.columns([2, 2, 3, 2])
        cash_choice = f5.selectbox(
            "Bank / Cash Account (14001-14011)",
            [f"{r['code']} - {r['name']}" for _, r in cash_accounts.iterrows()]
        )
        gl_choice = f6.selectbox(
            "Chart of Accounts Head",
            [f"{r['code']} - {r['name']} ({r['acc_type']})" for _, r in gl_accounts.iterrows()]
        )
        description = f7.text_input("Description / Particulars", placeholder="Enter transaction details...")
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

with tab2:
    st.subheader("Automated General Ledger (By Account Head)")
    if not gl_df.empty:
        st.download_button(
            "⬇️ Export Full General Ledger to Excel/CSV",
            data=gl_df.to_csv(index=False).encode("utf-8"),
            file_name="KONEK_General_Ledger.csv",
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

with tab3:
    st.subheader("Automated Trial Balance")
    if not gl_df.empty:
        tb = gl_df.groupby(["acc_code", "acc_name", "acc_type"], as_index=False)[["debit", "credit"]].sum()
        tb["Net Balance"] = (tb["debit"] - tb["credit"]).abs()
        tb["Dr / Cr"] = tb.apply(lambda r: "Dr" if r["debit"] >= r["credit"] else "Cr", axis=1)
        st.dataframe(tb, use_container_width=True, hide_index=True)
    else:
        st.info("No transactions posted yet.")

with tab4:
    st.subheader("⚙️ KONEK Chart of Accounts Manager")

    st.info("💡 Click the button below to load all **115 Accounts from KONEK INVESTMENT & SERVICES LTD** (`11001` to `43016`) in one click, or upload a new Excel file anytime.")
    if st.button("🔄 Load KONEK Investment & Services Ltd COA (115 Accounts)", type="primary"):
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
        st.success("Loaded all 115 KONEK Investment & Services Ltd accounts!")
        st.rerun()

    st.markdown("---")
    col_up, col_manual = st.columns([3, 2])

    with col_up:
        st.markdown("#### 📤 Upload a New Chart of Accounts Excel/CSV File")
        uploaded_file = st.file_uploader("Click 'Browse files' to upload an Excel (.xlsx) or CSV COA file", type=["xlsx", "xls", "csv"])

        if uploaded_file is not None:
            try:
                if uploaded_file.name.lower().endswith(".csv"):
                    raw_coa = pd.read_csv(uploaded_file)
                else:
                    raw_coa = pd.read_excel(uploaded_file)

                parsed_rows = smart_parse_coa_df(raw_coa)
                st.write(f"**Detected {len(parsed_rows)} valid accounts in `{uploaded_file.name}`:**")
                if parsed_rows:
                    preview_df = pd.DataFrame(parsed_rows, columns=["Account Code", "Account Name", "Category", "Is Bank/Cash"])
                    st.dataframe(preview_df, use_container_width=True, hide_index=True)

                    if st.button("✅ Confirm & Save Uploaded Chart of Accounts"):
                        conn = get_conn()
                        cur = conn.cursor()
                        cur.execute("DELETE FROM accounts")
                        for code_v, name_v, type_v, cash_v in parsed_rows:
                            cur.execute(f"DELETE FROM accounts WHERE code = {PH}", (code_v,))
                            cur.execute(
                                f"INSERT INTO accounts VALUES ({PH}, {PH}, {PH}, {PH})",
                                (code_v, name_v, type_v, cash_v)
                            )
                        conn.commit()
                        cur.close()
                        conn.close()
                        st.success(f"Successfully imported {len(parsed_rows)} accounts!")
                        st.rerun()
            except Exception as e:
                st.error(f"Could not read file: {e}")

    with col_manual:
        st.markdown("#### ➕ Add a Single Account Manually")
        with st.form("add_single_coa", clear_on_submit=True):
            new_code = st.text_input("Account Code", placeholder="e.g. 14004")
            new_name = st.text_input("Account Name", placeholder="e.g. UBA BANK")
            new_type = st.selectbox("Account Type", ["Asset", "Liability", "Equity", "Revenue", "Expense"])
            is_cash_acc = st.checkbox("Check if this is a Bank or Cash Account")
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

    st.markdown("---")
    st.markdown(f"#### 📋 Current Active Chart of Accounts ({len(acc_df)} Accounts)")
    st.dataframe(acc_df, use_container_width=True, hide_index=True)
