import re
import sys
import sqlite3
from pathlib import Path

DUMP = Path('backup_utf8_perfect_.sql')
DB = Path('data/geektech.sqlite3')

INSERT_RE = re.compile(r"^\s*INSERT\s+INTO\s+(.*)$", re.IGNORECASE | re.DOTALL)
SCHEMA_TABLE_RE = re.compile(r"INSERT\s+INTO\s+\"?public\"?\.?\"?([A-Za-z_][A-Za-z0-9_]*)\"?\s*(\([^)]*\))\s*VALUES\s*", re.IGNORECASE | re.DOTALL)
SCHEMA_TABLE_NO_COL_RE = re.compile(r"INSERT\s+INTO\s+\"?public\"?\.?\"?([A-Za-z_][A-Za-z0-9_]*)\"?\s*VALUES\s*", re.IGNORECASE | re.DOTALL)
QUOTED_IDENT_IN_PARENS_RE = re.compile(r"\(\s*([^)]+?)\s*\)")
QUOTED_IDENT_RE = re.compile(r"\"([A-Za-z_][A-Za-z0-9_]*)\"")
BOOL_TRUE_RE = re.compile(r"\btrue\b", re.IGNORECASE)
BOOL_FALSE_RE = re.compile(r"\bfalse\b", re.IGNORECASE)
TYPE_CAST_RE = re.compile(r"(::[a-zA-Z_][a-zA-Z0-9_]*\b)")

TARGETS = {"clients", "delivery_notes", "delivery_note_items", "supplier_invoices"}


def normalize_insert(sql: str) -> str:
    s = sql.strip()
    if not INSERT_RE.match(s):
        return ""

    def _replace_with_cols(m):
        table = m.group(1)
        cols = m.group(2) or ""
        def _strip_quotes_in_cols(match):
            inside = match.group(1)
            inside = QUOTED_IDENT_RE.sub(r"\1", inside)
            return f"({inside})"
        cols = QUOTED_IDENT_IN_PARENS_RE.sub(_strip_quotes_in_cols, cols)
        return f"INSERT INTO {table} {cols} VALUES "

    new_s = SCHEMA_TABLE_RE.sub(_replace_with_cols, s)
    if new_s == s:
        new_s = SCHEMA_TABLE_NO_COL_RE.sub(lambda m: f"INSERT INTO {m.group(1)} VALUES ", s)
    if new_s == s:
        new_s = re.sub(r'INSERT\s+INTO\s+\"([A-Za-z_][A-Za-z0-9_]*)\"', r'INSERT INTO \1', s, flags=re.IGNORECASE)

    new_s = BOOL_TRUE_RE.sub('1', new_s)
    new_s = BOOL_FALSE_RE.sub('0', new_s)
    new_s = TYPE_CAST_RE.sub('', new_s)
    return new_s


def iter_inserts_for_targets():
    buf = []
    with DUMP.open('r', encoding='utf-8', errors='replace') as f:
        for line in f:
            if not buf and not line.lstrip().upper().startswith('INSERT INTO'):
                continue
            buf.append(line)
            if line.rstrip().endswith(';'):
                stmt = ''.join(buf)
                buf = []
                if 'INSERT INTO' in stmt:
                    norm = normalize_insert(stmt)
                    up = norm.upper()
                    if any((f'INSERT INTO {t.upper()} ' in up) for t in TARGETS):
                        yield norm
    if buf:
        stmt = ''.join(buf)
        norm = normalize_insert(stmt)
        up = norm.upper()
        if any((f'INSERT INTO {t.upper()} ' in up) for t in TARGETS):
            yield norm


def patch_supplier_invoices(stmt: str) -> str:
    # Ensure pdf_path and pdf_filename are present with dummy values
    if not stmt.upper().startswith('INSERT INTO SUPPLIER_INVOICES'):
        return stmt
    m = re.match(r"INSERT INTO SUPPLIER_INVOICES\s*(\([^)]*\))\s*VALUES\s*(\(.+\));?$", stmt, re.IGNORECASE | re.DOTALL)
    if not m:
        return stmt
    cols_raw = m.group(1)
    vals_raw = m.group(2)
    cols = cols_raw.strip()[1:-1]
    vals = vals_raw.strip()[1:-1]
    col_list = [c.strip() for c in cols.split(',')]
    # If columns missing, append
    if 'pdf_path' not in [c.lower() for c in col_list]:
        col_list.append('pdf_path')
        vals += ", 'N/A'"
    if 'pdf_filename' not in [c.lower() for c in col_list]:
        col_list.append('pdf_filename')
        vals += ", 'unknown.pdf'"
    new_cols = '(' + ', '.join(col_list) + ')'
    new_vals = '(' + vals + ')'
    return f"INSERT INTO supplier_invoices {new_cols} VALUES {new_vals};"


def main():
    if not DB.exists():
        print('DB not found')
        sys.exit(1)
    con = sqlite3.connect(str(DB))
    cur = con.cursor()

    # Schema adjustments (ignore if already exist)
    try:
        cur.execute("ALTER TABLE clients ADD COLUMN created_at TIMESTAMP")
    except Exception:
        pass
    try:
        cur.execute("ALTER TABLE delivery_notes ADD COLUMN signature_data_url TEXT")
    except Exception:
        pass
    con.commit()

    # Clean existing rows to avoid duplicates
    for t in ['clients', 'delivery_notes', 'delivery_note_items', 'supplier_invoices']:
        try:
            cur.execute(f'DELETE FROM {t}')
        except Exception:
            pass
    con.commit()

    kept = 0
    for stmt in iter_inserts_for_targets():
        s = stmt
        if s.upper().startswith('INSERT INTO SUPPLIER_INVOICES'):
            s = patch_supplier_invoices(s)
        try:
            cur.execute(s)
            kept += 1
            if kept % 200 == 0:
                con.commit()
        except Exception as e:
            # print minimal error and continue
            print('[WARN]', str(e)[:160])
    con.commit()
    print('Done. Inserted statements:', kept)

if __name__ == '__main__':
    main()
