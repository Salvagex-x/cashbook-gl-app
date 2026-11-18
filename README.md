# KONEK Cashbook → Automated General Ledger

A web app that turns daily cashbook entries into a full **double-entry general
ledger** and **trial balance**, built with **Streamlit**, **PostgreSQL
(Supabase)** and **Python**.

## What it does

| Screen | Purpose |
|---|---|
| **1. Cashbook** | Record receipts, payments (split across several account heads if needed) and transfers between bank/cash accounts. Running-balance register per bank account. |
| **2. Journal & Opening Balances** | Non-cash adjustments (depreciation, accruals, corrections) and opening balances. Debits must equal credits. |
| **3. General Ledger** | Per-account ledger with balance brought forward, contra account and running balance. Excel export. |
| **4. Trial Balance** | As-at-date trial balance with totals and a live Dr = Cr check. Excel export. |
| **5. Vouchers & Reversals** | Posted entries are never edited or deleted. **Correct** one in a single step (wrong month, amount or account): the old entry is cancelled and the corrected one posted, with a reason, both kept on record. Or just reverse it. |
| **6. Chart of Accounts** | Add/edit accounts, hide unused ones, safe Excel/CSV merge-upload (never deletes). Departments. |
| **7. Audit Log** | Who did what and when (admin only). |

## Accounting and security design

- **Double entry**: a voucher is a header plus lines; every voucher balances.
  Enforced in the app **and** by a deferred PostgreSQL trigger.
- **Immutable books**: database triggers refuse any edit/delete of posted
  vouchers or lines. Corrections happen by reversal + re-posting in one
  all-or-nothing step, linked and audited.
- **Compact entry forms**: the account dropdown can show just the code, code +
  type, or the full name; a warning appears when an entry is dated outside the
  current month.
- **Exact money**: stored as `NUMERIC(18,2)`, calculated in whole kobo.
- **Referential integrity**: foreign keys stop postings to unknown accounts;
  accounts are hidden, never deleted (and only at zero balance).
- **Unique voucher numbers** (case-insensitive).
- **Login + roles** (`admin`, `accountant`, `viewer`), salted PBKDF2 password
  hashes kept in Streamlit secrets, lock-out after repeated failures.
- **Row Level Security** switched on for every table, so the public Supabase
  API cannot read or write them.
- **Safe exports**: spreadsheet formula-injection neutralised.
- Secrets are never in the repository (`.gitignore`, `secrets.toml.example`).

## Project layout

```
app.py            screens (Streamlit)
db.py             connection pool, tables, PostgreSQL safety triggers
service.py        business rules: posting, reversal, chart of accounts, audit
reports.py        ledger, cash register, trial balance, Excel export (pandas)
auth.py           password hashing, roles
coa_import.py     reads a chart of accounts from Excel/CSV
coa_defaults.py   starter chart of accounts and departments
tools/make_password_hash.py   creates a password hash for the secrets
tests/            automated tests (run on SQLite, and PostgreSQL if configured)
```

## Run locally

```bash
pip install -r requirements-dev.txt
streamlit run app.py        # no DATABASE_URL -> local SQLite test mode, no login
pytest                      # run the tests
```

Without a `DATABASE_URL` the app uses a local SQLite file and skips the login
(for testing only). With a `DATABASE_URL` the app **refuses to run unless at
least one user is configured**.

## Deploy (Streamlit Community Cloud + Supabase)

Put `DATABASE_URL` and the `[users.*]` blocks (see
`.streamlit/secrets.toml.example`) in the app's **Settings → Secrets**.
The tables, security rules and triggers are created automatically on first start.

## Known limits (honest list)

- Cash-basis cashbook + journals; no bank-reconciliation screen, VAT or
  multi-currency yet.
- Login is a simple built-in one (no password reset, sessions end on page
  refresh). For many users, move to Supabase Auth or Google/Microsoft login.
- One company per database.
- Supabase free projects pause after a period of inactivity — use a paid plan
  or a keep-alive for daily business use.
