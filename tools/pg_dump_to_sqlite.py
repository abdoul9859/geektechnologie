import os
import re
import sys
import sqlite3
from pathlib import Path

# Ensure we can import the app package
THIS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = THIS_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Note: we'll import app.database lazily inside main() AFTER we set DATABASE_URL

INSERT_RE = re.compile(r"^\s*INSERT\s+INTO\s+(.*)$", re.IGNORECASE | re.DOTALL)

# Matches: INSERT INTO "public"."table" ("col1", "col2") VALUES (...);
SCHEMA_TABLE_RE = re.compile(r"INSERT\s+INTO\s+\"?public\"?\.?\"?([A-Za-z_][A-Za-z0-9_]*)\"?\s*(\([^)]*\))\s*VALUES\s*", re.IGNORECASE | re.DOTALL)
# Fallback if no column list (rare): INSERT INTO "public"."table" VALUES (...);
SCHEMA_TABLE_NO_COL_RE = re.compile(r"INSERT\s+INTO\s+\"?public\"?\.?\"?([A-Za-z_][A-Za-z0-9_]*)\"?\s*VALUES\s*", re.IGNORECASE | re.DOTALL)

# Replace quoted identifiers inside the column list: "col" -> col
QUOTED_IDENT_IN_PARENS_RE = re.compile(r"\(\s*([^)]+?)\s*\)")
QUOTED_IDENT_RE = re.compile(r"\"([A-Za-z_][A-Za-z0-9_]*)\"")

# Convert PostgreSQL boolean literals to SQLite-friendly integers
BOOL_TRUE_RE = re.compile(r"\btrue\b", re.IGNORECASE)
BOOL_FALSE_RE = re.compile(r"\bfalse\b", re.IGNORECASE)

# Optionally strip type casts like '::timestamp' or '::jsonb' within values
TYPE_CAST_RE = re.compile(r"(::[a-zA-Z_][a-zA-Z0-9_]*\b)")


def normalize_insert(sql: str) -> str:
    s = sql.strip()
    # Only process INSERT statements
    if not INSERT_RE.match(s):
        return ""

    # Replace schema-qualified table with bare table name and keep column list if present
    def _replace_with_cols(m):
        table = m.group(1)
        cols = m.group(2) or ""
        # Remove quotes around column identifiers inside parens
        def _strip_quotes_in_cols(match):
            inside = match.group(1)
            inside = QUOTED_IDENT_RE.sub(r"\1", inside)
            return f"({inside})"
        cols = QUOTED_IDENT_IN_PARENS_RE.sub(_strip_quotes_in_cols, cols)
        return f"INSERT INTO {table} {cols} VALUES "

    new_s = SCHEMA_TABLE_RE.sub(_replace_with_cols, s)

    if new_s == s:
        # Try no-col variant
        new_s = SCHEMA_TABLE_NO_COL_RE.sub(lambda m: f"INSERT INTO {m.group(1)} VALUES ", s)

    # If still unchanged, also try to remove bare quoted table name without schema
    if new_s == s:
        new_s = re.sub(r'INSERT\s+INTO\s+\"([A-Za-z_][A-Za-z0-9_]*)\"', r'INSERT INTO \1', s, flags=re.IGNORECASE)

    # Convert booleans
    new_s = BOOL_TRUE_RE.sub('1', new_s)
    new_s = BOOL_FALSE_RE.sub('0', new_s)

    # Strip type casts like 'value'::timestamp or 123::int
    new_s = TYPE_CAST_RE.sub('', new_s)

    return new_s


def iter_statements(dump_path: Path):
    buf = []
    with dump_path.open('r', encoding='utf-8', errors='replace') as f:
        for line in f:
            # Skip non-data preamble quickly
            if not buf and not line.lstrip().upper().startswith('INSERT INTO'):
                # But we still need to collect multi-line INSERTs once they start
                continue
            buf.append(line)
            if line.rstrip().endswith(';'):
                stmt = ''.join(buf)
                buf = []
                yield stmt
        # Any trailing buffered content without semicolon (unlikely)
        if buf:
            yield ''.join(buf)


def main():
    if len(sys.argv) < 3:
        print("Usage: python tools/pg_dump_to_sqlite.py <path_to_dump.sql> <sqlite_db_path>")
        sys.exit(1)

    dump_path = Path(sys.argv[1]).resolve()
    sqlite_path = Path(sys.argv[2]).resolve()

    if not dump_path.exists():
        print(f"Dump introuvable: {dump_path}")
        sys.exit(2)

    # Ensure parent dir exists
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)

    # Force the app to use the target SQLite database
    os.environ["DATABASE_URL"] = f"sqlite:///{sqlite_path.as_posix()}"

    # Import and create schema via SQLAlchemy models (uses env above)
    from app.database import create_tables  # lazy import
    create_tables()

    # Ensure console can print UTF-8 without crashing on accents
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')  # Python 3.7+
    except Exception:
        pass

    # Import data
    conn = sqlite3.connect(str(sqlite_path))
    try:
        conn.execute('PRAGMA foreign_keys = OFF;')
        conn.execute('BEGIN;')
        count = 0
        kept = 0
        for raw_stmt in iter_statements(dump_path):
            count += 1
            stmt = normalize_insert(raw_stmt)
            if not stmt:
                continue
            try:
                conn.execute(stmt)
                kept += 1
                if kept % 500 == 0:
                    conn.commit()
            except sqlite3.Error as e:
                # Log and continue (avoid echoing full statement with accents)
                preview = stmt[:140].encode('utf-8', errors='ignore').decode('utf-8', errors='ignore')
                print(f"[WARN] Echec d'insertion (ignore): {e}\n  -> {preview}...")
        conn.commit()
        print(f"Terminé. INSERT traités: {kept}. Total de statements lus: {count}.")
    finally:
        conn.execute('PRAGMA foreign_keys = ON;')
        conn.close()


if __name__ == '__main__':
    main()
