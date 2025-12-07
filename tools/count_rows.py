import sqlite3
import json
from pathlib import Path

DB_PATH = Path('data/geektech.sqlite3')

TABLES = [
    'users','clients','products','product_variants','invoices','invoice_items','invoice_payments',
    'quotations','quotation_items','delivery_notes','delivery_note_items','bank_transactions',
    'suppliers','supplier_invoices','supplier_invoice_payments','stock_movements','categories',
    'category_attributes','category_attribute_values','app_cache','daily_purchases',
    'daily_purchase_categories','scan_history','user_settings','migrations','migration_logs',
    'daily_sales','daily_client_requests','product_serial_numbers','product_variant_attributes'
]

def main():
    if not DB_PATH.exists():
        print(json.dumps({'error': f'Database not found: {DB_PATH}'}, ensure_ascii=False))
        return
    con = sqlite3.connect(str(DB_PATH))
    cur = con.cursor()
    existing = {r[0] for r in cur.execute("select name from sqlite_master where type='table'")}
    res = {}
    for t in TABLES:
        if t in existing:
            try:
                cnt = cur.execute(f"select count(*) from {t}").fetchone()[0]
            except Exception as e:
                cnt = f'error: {e}'
        else:
            cnt = 'missing'
        res[t] = cnt
    print(json.dumps(res, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
