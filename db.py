"""
Database layer: connection handling and table creation.

Works with
  * PostgreSQL (Supabase)  - when a DATABASE_URL is supplied  -> real use
  * SQLite (a local file)  - when no DATABASE_URL is supplied -> local testing

All SQL in the rest of the app is written with "?" placeholders; this module
swaps them for "%s" when talking to PostgreSQL.
"""
import contextlib
import sqlite3
import threading
import time

import pandas as pd

from coa_defaults import DEFAULT_COA, DEFAULT_DEPARTMENTS, NAME_FIXES

try:
    import psycopg2
    from psycopg2 import pool as pg_pool
except ImportError:  # pragma: no cover
    psycopg2 = None
    pg_pool = None

# Errors that mean "the database refused this row" (duplicate, broken link ...)
INTEGRITY_ERRORS = (sqlite3.IntegrityError,) + (
    (psycopg2.IntegrityError,) if psycopg2 else ()
)

# Errors that mean "the connection to the cloud database dropped".
CONNECTION_ERRORS = (
    (psycopg2.OperationalError, psycopg2.InterfaceError) if psycopg2 else ()
)

PG_TABLES = ["accounts", "departments", "vouchers", "voucher_lines", "audit_log"]


class Tx:
    """One database transaction (everything inside commits or rolls back together)."""

    def __init__(self, db: "Database", conn):
        self.db = db
        self.cur = conn.cursor()

    def execute(self, sql, params=()):
        args = tuple(params)
        sql = self.db.adapt(sql)
        if self.db.is_pg:
            # psycopg2 treats a "%" in the SQL as a placeholder whenever params
            # are given, so send None when there are none.
            self.cur.execute(sql, args or None)
        else:
            self.cur.execute(sql, args)
        return self.cur

    def fetchall(self, sql, params=()):
        self.execute(sql, params)
        return self.cur.fetchall()

    def fetchone(self, sql, params=()):
        self.execute(sql, params)
        return self.cur.fetchone()

    def insert_id(self, sql, params=()):
        """INSERT a row and return its new numeric id."""
        if self.db.is_pg:
            self.cur.execute(self.db.adapt(sql) + " RETURNING id", tuple(params))
            return self.cur.fetchone()[0]
        self.cur.execute(self.db.adapt(sql), tuple(params))
        return self.cur.lastrowid


class Database:
    def __init__(self, url: str | None = None, sqlite_path: str = "ledger_data.db"):
        self.url = url or None
        self.is_pg = bool(self.url)
        if self.is_pg and psycopg2 is None:
            raise RuntimeError(
                "DATABASE_URL is set but the 'psycopg2-binary' package is not "
                "installed. Add it to requirements.txt."
            )
        self.sqlite_path = sqlite_path
        self.ph = "%s" if self.is_pg else "?"
        self.init_warnings: list[str] = []
        self._pool = None                # PostgreSQL connections are re-used, not re-opened
        self._pool_lock = threading.Lock()
        self._last_used: dict[int, float] = {}

    # ------------------------------------------------------------------ basics
    @property
    def label(self) -> str:
        return "PostgreSQL (cloud)" if self.is_pg else "SQLite (local file)"

    def adapt(self, sql: str) -> str:
        return sql.replace("?", self.ph)

    # PostgreSQL: a small pool of re-usable connections (opening a new cloud
    # connection can take a second or more). A connection that sat idle may have been
    # closed by the server, so it is checked first and replaced if it is dead.
    def _acquire(self):
        if not self.is_pg:
            conn = sqlite3.connect(self.sqlite_path, check_same_thread=False)
            conn.execute("PRAGMA foreign_keys = ON")
            return conn
        with self._pool_lock:
            if self._pool is None:
                self._pool = pg_pool.ThreadedConnectionPool(1, 10, self.url, connect_timeout=10)
        conn = self._pool.getconn()
        idle = time.time() - self._last_used.get(id(conn), 0)
        if conn.closed or idle > 30:
            try:
                if conn.closed:
                    raise psycopg2.OperationalError("closed")
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                conn.rollback()
            except Exception:
                self._pool.putconn(conn, close=True)
                conn = self._pool.getconn()
        return conn

    def _release(self, conn):
        if not self.is_pg:
            conn.close()
            return
        self._last_used[id(conn)] = time.time()
        broken = conn.closed or conn.info.transaction_status == psycopg2.extensions.TRANSACTION_STATUS_UNKNOWN
        self._pool.putconn(conn, close=bool(broken))

    @contextlib.contextmanager
    def tx(self):
        """with db.tx() as t:  ...  -> commits on success, rolls back on any error."""
        conn = self._acquire()
        try:
            yield Tx(self, conn)
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            self._release(conn)

    def _read_with_retry(self, fn):
        """Reads are safe to repeat, so retry once if the cloud connection had dropped."""
        try:
            return fn()
        except CONNECTION_ERRORS:
            if not self.is_pg:
                raise
            return fn()

    def q(self, sql: str, params=()) -> pd.DataFrame:
        """Run a SELECT and return a DataFrame."""
        def run():
            with self.tx() as t:
                cur = t.execute(sql, params)
                rows = cur.fetchall()
                cols = [d[0] for d in cur.description]
            return pd.DataFrame(rows, columns=cols)
        return self._read_with_retry(run)

    def scalar(self, sql: str, params=()):
        def run():
            with self.tx() as t:
                row = t.fetchone(sql, params)
            return row[0] if row else None
        return self._read_with_retry(run)

    # ------------------------------------------------------------------ schema
    def _table_ddl(self) -> list[str]:
        pk = "SERIAL PRIMARY KEY" if self.is_pg else "INTEGER PRIMARY KEY AUTOINCREMENT"
        date = "DATE" if self.is_pg else "TEXT"
        ts = ("TIMESTAMPTZ NOT NULL DEFAULT now()" if self.is_pg
              else "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP")
        return [
            """CREATE TABLE IF NOT EXISTS accounts (
                code      TEXT PRIMARY KEY,
                name      TEXT NOT NULL,
                acc_type  TEXT NOT NULL
                          CHECK (acc_type IN ('Asset','Liability','Equity','Revenue','Expense')),
                is_cash   INTEGER NOT NULL DEFAULT 0 CHECK (is_cash IN (0, 1)),
                is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
            )""",
            """CREATE TABLE IF NOT EXISTS departments (
                name      TEXT PRIMARY KEY,
                is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
            )""",
            f"""CREATE TABLE IF NOT EXISTS vouchers (
                id          {pk},
                tx_date     {date} NOT NULL,
                ref_no      TEXT NOT NULL UNIQUE,
                v_type      TEXT NOT NULL CHECK (v_type IN
                            ('RECEIPT','PAYMENT','TRANSFER','JOURNAL','OPENING','REVERSAL')),
                unit_tag    TEXT NOT NULL REFERENCES departments(name),
                narration   TEXT NOT NULL,
                status      TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','REVERSED')),
                reverses_id INTEGER REFERENCES vouchers(id),
                corrects_id INTEGER REFERENCES vouchers(id),
                created_by  TEXT NOT NULL,
                created_at  {ts}
            )""",
            f"""CREATE TABLE IF NOT EXISTS voucher_lines (
                id          {pk},
                voucher_id  INTEGER NOT NULL REFERENCES vouchers(id),
                acc_code    TEXT NOT NULL REFERENCES accounts(code),
                description TEXT NOT NULL DEFAULT '',
                debit       NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (debit >= 0),
                credit      NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (credit >= 0),
                CHECK ((debit > 0 AND credit = 0) OR (credit > 0 AND debit = 0))
            )""",
            f"""CREATE TABLE IF NOT EXISTS audit_log (
                id        {pk},
                at        {ts},
                user_name TEXT NOT NULL,
                action    TEXT NOT NULL,
                detail    TEXT NOT NULL DEFAULT ''
            )""",
            "CREATE INDEX IF NOT EXISTS idx_vouchers_date ON vouchers (tx_date)",
            "CREATE INDEX IF NOT EXISTS idx_lines_voucher ON voucher_lines (voucher_id)",
            "CREATE INDEX IF NOT EXISTS idx_lines_account ON voucher_lines (acc_code)",
        ]

    def _pg_hardening(self) -> list[tuple[str, str]]:
        """Security steps that only exist in PostgreSQL. Each runs on its own."""
        steps: list[tuple[str, str]] = []
        for t in PG_TABLES:
            steps.append((f"Row Level Security on {t}",
                          f"ALTER TABLE public.{t} ENABLE ROW LEVEL SECURITY"))
        steps.append(("Row Level Security on old 'cashbook' table (if it exists)", """
            DO $do$ BEGIN
              IF to_regclass('public.cashbook') IS NOT NULL THEN
                EXECUTE 'ALTER TABLE public.cashbook ENABLE ROW LEVEL SECURITY';
              END IF;
            END $do$"""))
        steps.append(("Guard: every voucher must balance", """
            CREATE OR REPLACE FUNCTION public.check_voucher_balanced() RETURNS trigger
            LANGUAGE plpgsql SET search_path = public AS $fn$
            DECLARE d NUMERIC; c NUMERIC;
            BEGIN
              SELECT COALESCE(SUM(debit),0), COALESCE(SUM(credit),0) INTO d, c
                FROM voucher_lines WHERE voucher_id = NEW.voucher_id;
              IF d <> c THEN
                RAISE EXCEPTION 'Voucher % is not balanced: debits % vs credits %',
                                NEW.voucher_id, d, c;
              END IF;
              RETURN NULL;
            END $fn$;
            DROP TRIGGER IF EXISTS trg_voucher_balanced ON public.voucher_lines;
            CREATE CONSTRAINT TRIGGER trg_voucher_balanced
              AFTER INSERT ON public.voucher_lines
              DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
              EXECUTE FUNCTION public.check_voucher_balanced();"""))
        steps.append(("Guard: posted lines cannot be edited or deleted", """
            CREATE OR REPLACE FUNCTION public.protect_posted_lines() RETURNS trigger
            LANGUAGE plpgsql SET search_path = public AS $fn$
            BEGIN
              RAISE EXCEPTION 'Posted voucher lines cannot be changed or deleted. Reverse the voucher instead.';
            END $fn$;
            DROP TRIGGER IF EXISTS trg_lines_immutable ON public.voucher_lines;
            CREATE TRIGGER trg_lines_immutable
              BEFORE UPDATE OR DELETE ON public.voucher_lines
              FOR EACH ROW EXECUTE FUNCTION public.protect_posted_lines();"""))
        steps.append(("Guard: posted vouchers cannot be edited or deleted", """
            CREATE OR REPLACE FUNCTION public.protect_posted_vouchers() RETURNS trigger
            LANGUAGE plpgsql SET search_path = public AS $fn$
            BEGIN
              IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'Posted vouchers cannot be deleted. Reverse the voucher instead.';
              END IF;
              IF NEW.tx_date     IS DISTINCT FROM OLD.tx_date
              OR NEW.ref_no      IS DISTINCT FROM OLD.ref_no
              OR NEW.v_type      IS DISTINCT FROM OLD.v_type
              OR NEW.unit_tag    IS DISTINCT FROM OLD.unit_tag
              OR NEW.narration   IS DISTINCT FROM OLD.narration
              OR NEW.created_by  IS DISTINCT FROM OLD.created_by
              OR NEW.reverses_id IS DISTINCT FROM OLD.reverses_id
              OR NEW.corrects_id IS DISTINCT FROM OLD.corrects_id THEN
                RAISE EXCEPTION 'Posted vouchers cannot be edited. Reverse the voucher and post a corrected one.';
              END IF;
              IF OLD.status = 'REVERSED' AND NEW.status <> 'REVERSED' THEN
                RAISE EXCEPTION 'A reversed voucher cannot be un-reversed.';
              END IF;
              RETURN NEW;
            END $fn$;
            DROP TRIGGER IF EXISTS trg_vouchers_protect ON public.vouchers;
            CREATE TRIGGER trg_vouchers_protect
              BEFORE UPDATE OR DELETE ON public.vouchers
              FOR EACH ROW EXECUTE FUNCTION public.protect_posted_vouchers();"""))
        return steps

    def init_schema(self):
        """Create / upgrade everything the app needs. Safe to run many times."""
        # 1) tables (must succeed)
        for stmt in self._table_ddl():
            with self.tx() as t:
                t.cur.execute(stmt)

        # 2) upgrade an accounts table created by version 1 of the app
        with self.tx() as t:
            if self.is_pg:
                t.cur.execute("ALTER TABLE accounts ADD COLUMN IF NOT EXISTS "
                              "is_active INTEGER NOT NULL DEFAULT 1")
            else:
                cols = [r[1] for r in t.cur.execute("PRAGMA table_info(accounts)").fetchall()]
                if "is_active" not in cols:
                    t.cur.execute("ALTER TABLE accounts ADD COLUMN "
                                  "is_active INTEGER NOT NULL DEFAULT 1")

        # 3) starter data (only when empty / missing)
        with self.tx() as t:
            if t.fetchone("SELECT COUNT(*) FROM accounts")[0] == 0:
                for code, name, acc_type, is_cash in DEFAULT_COA:
                    t.execute("INSERT INTO accounts (code, name, acc_type, is_cash) "
                              "VALUES (?, ?, ?, ?)", (code, name, acc_type, is_cash))
            for dept in DEFAULT_DEPARTMENTS:
                t.execute("INSERT INTO departments (name) VALUES (?) "
                          "ON CONFLICT (name) DO NOTHING", (dept,))
            for code, (old, new) in NAME_FIXES.items():
                t.execute("UPDATE accounts SET name = ? WHERE code = ? AND name = ?",
                          (new, code, old))

        # 4) PostgreSQL-only security steps (a failure is reported, not fatal)
        if self.is_pg:
            for label, stmt in self._pg_hardening():
                try:
                    with self.tx() as t:
                        t.cur.execute(stmt)
                except Exception as exc:  # pragma: no cover
                    self.init_warnings.append(f"{label}: {exc}")
        return self
