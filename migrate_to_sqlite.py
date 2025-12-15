#!/usr/bin/env python3
"""
Script de migration PostgreSQL -> SQLite
Exporte toutes les données de PostgreSQL et les importe dans SQLite
"""

import os
import sys
import sqlite3
from datetime import datetime, date
from decimal import Decimal

# Configurer l'environnement pour utiliser PostgreSQL d'abord
os.environ["DATABASE_URL"] = "postgres://geekuser:geekpassword@db:5432/geekdb"
os.environ["DB_SSLMODE"] = "disable"

from sqlalchemy import create_engine, text, inspect
from sqlalchemy.orm import sessionmaker

# Connexion PostgreSQL
PG_URL = "postgresql+psycopg://geekuser:geekpassword@db:5432/geekdb"
SQLITE_PATH = "/app/data/geektech.db"

def get_pg_engine():
    return create_engine(PG_URL, pool_pre_ping=True)

def get_sqlite_connection():
    return sqlite3.connect(SQLITE_PATH)

def get_table_names(engine):
    """Récupère la liste des tables"""
    inspector = inspect(engine)
    return inspector.get_table_names()

def get_table_columns(engine, table_name):
    """Récupère les colonnes d'une table"""
    inspector = inspect(engine)
    return [col['name'] for col in inspector.get_columns(table_name)]

def pg_type_to_sqlite(pg_type):
    """Convertit les types PostgreSQL en SQLite"""
    pg_type = str(pg_type).upper()
    if 'INT' in pg_type:
        return 'INTEGER'
    elif 'VARCHAR' in pg_type or 'TEXT' in pg_type or 'CHAR' in pg_type:
        return 'TEXT'
    elif 'NUMERIC' in pg_type or 'DECIMAL' in pg_type or 'FLOAT' in pg_type or 'DOUBLE' in pg_type:
        return 'REAL'
    elif 'BOOL' in pg_type:
        return 'INTEGER'
    elif 'DATE' in pg_type or 'TIME' in pg_type:
        return 'TEXT'
    elif 'BYTEA' in pg_type or 'BLOB' in pg_type:
        return 'BLOB'
    else:
        return 'TEXT'

def create_sqlite_schema(pg_engine, sqlite_conn):
    """Crée le schéma SQLite basé sur PostgreSQL"""
    inspector = inspect(pg_engine)
    cursor = sqlite_conn.cursor()
    
    tables = inspector.get_table_names()
    print(f"Found {len(tables)} tables to migrate")
    
    for table_name in tables:
        columns = inspector.get_columns(table_name)
        pk_constraint = inspector.get_pk_constraint(table_name)
        pk_columns = pk_constraint.get('constrained_columns', []) if pk_constraint else []
        
        col_defs = []
        for col in columns:
            col_name = col['name']
            col_type = pg_type_to_sqlite(col['type'])
            nullable = col.get('nullable', True)
            
            col_def = f'"{col_name}" {col_type}'
            if col_name in pk_columns:
                col_def += ' PRIMARY KEY'
            if not nullable and col_name not in pk_columns:
                col_def += ' NOT NULL'
            
            col_defs.append(col_def)
        
        create_sql = f'CREATE TABLE IF NOT EXISTS "{table_name}" ({", ".join(col_defs)})'
        try:
            cursor.execute(f'DROP TABLE IF EXISTS "{table_name}"')
            cursor.execute(create_sql)
            print(f"  Created table: {table_name}")
        except Exception as e:
            print(f"  Error creating table {table_name}: {e}")
    
    sqlite_conn.commit()

def migrate_data(pg_engine, sqlite_conn):
    """Migre les données de PostgreSQL vers SQLite"""
    inspector = inspect(pg_engine)
    tables = inspector.get_table_names()
    cursor = sqlite_conn.cursor()
    
    # Ordre de migration pour respecter les foreign keys
    priority_tables = [
        'users', 'clients', 'suppliers', 'categories',
        'products', 'product_variants', 'product_serial_numbers',
        'quotations', 'quotation_items',
        'invoices', 'invoice_items', 'invoice_payments',
        'delivery_notes', 'delivery_note_items',
        'supplier_invoices', 'supplier_invoice_payments',
        'stock_movements', 'daily_sales', 'daily_purchases',
        'client_debts', 'client_debt_payments',
        'supplier_debts', 'supplier_debt_payments',
        'bank_transactions', 'user_settings', 'app_cache',
        'migrations', 'migration_logs', 'schema_migrations',
        'scan_history', 'daily_client_requests',
        'purchase_orders', 'purchase_order_items',
        'category_attributes', 'category_attribute_values',
        'product_variant_attributes', 'daily_purchase_categories'
    ]
    
    # Ajouter les tables non listées
    for t in tables:
        if t not in priority_tables:
            priority_tables.append(t)
    
    with pg_engine.connect() as pg_conn:
        for table_name in priority_tables:
            if table_name not in tables:
                continue
                
            try:
                # Récupérer les données
                result = pg_conn.execute(text(f'SELECT * FROM "{table_name}"'))
                rows = result.fetchall()
                columns = result.keys()
                
                if not rows:
                    print(f"  {table_name}: 0 rows (empty)")
                    continue
                
                # Insérer dans SQLite
                placeholders = ', '.join(['?' for _ in columns])
                col_names = ', '.join([f'"{c}"' for c in columns])
                insert_sql = f'INSERT INTO "{table_name}" ({col_names}) VALUES ({placeholders})'
                
                # Convertir les données
                converted_rows = []
                for row in rows:
                    converted_row = []
                    for val in row:
                        if val is None:
                            converted_row.append(None)
                        elif isinstance(val, bool):
                            converted_row.append(1 if val else 0)
                        elif isinstance(val, Decimal):
                            converted_row.append(float(val))
                        elif isinstance(val, (datetime, date)):
                            converted_row.append(val.isoformat())
                        else:
                            converted_row.append(val)
                    converted_rows.append(tuple(converted_row))
                
                cursor.executemany(insert_sql, converted_rows)
                print(f"  {table_name}: {len(rows)} rows migrated")
                
            except Exception as e:
                print(f"  Error migrating {table_name}: {e}")
    
    sqlite_conn.commit()

def create_indexes(sqlite_conn):
    """Crée les index importants"""
    cursor = sqlite_conn.cursor()
    indexes = [
        'CREATE INDEX IF NOT EXISTS idx_users_username ON users(username)',
        'CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)',
        'CREATE INDEX IF NOT EXISTS idx_clients_name ON clients(name)',
        'CREATE INDEX IF NOT EXISTS idx_products_name ON products(name)',
        'CREATE INDEX IF NOT EXISTS idx_products_barcode ON products(barcode)',
        'CREATE INDEX IF NOT EXISTS idx_invoices_created_at ON invoices(created_at)',
        'CREATE INDEX IF NOT EXISTS idx_invoices_client_id ON invoices(client_id)',
        'CREATE INDEX IF NOT EXISTS idx_invoices_status ON invoices(status)',
        'CREATE INDEX IF NOT EXISTS idx_invoice_items_invoice_id ON invoice_items(invoice_id)',
        'CREATE INDEX IF NOT EXISTS idx_quotations_client_id ON quotations(client_id)',
        'CREATE INDEX IF NOT EXISTS idx_stock_movements_product_id ON stock_movements(product_id)',
    ]
    
    for idx_sql in indexes:
        try:
            cursor.execute(idx_sql)
        except Exception as e:
            print(f"  Index error: {e}")
    
    sqlite_conn.commit()
    print("  Indexes created")

def main():
    print("=" * 60)
    print("Migration PostgreSQL -> SQLite")
    print("=" * 60)
    
    # Supprimer l'ancienne base SQLite si elle existe
    if os.path.exists(SQLITE_PATH):
        os.remove(SQLITE_PATH)
        print(f"Removed existing SQLite database: {SQLITE_PATH}")
    
    print("\n1. Connecting to PostgreSQL...")
    pg_engine = get_pg_engine()
    
    print("\n2. Creating SQLite database...")
    sqlite_conn = get_sqlite_connection()
    
    print("\n3. Creating SQLite schema...")
    create_sqlite_schema(pg_engine, sqlite_conn)
    
    print("\n4. Migrating data...")
    migrate_data(pg_engine, sqlite_conn)
    
    print("\n5. Creating indexes...")
    create_indexes(sqlite_conn)
    
    sqlite_conn.close()
    pg_engine.dispose()
    
    # Vérifier la taille du fichier
    size_mb = os.path.getsize(SQLITE_PATH) / (1024 * 1024)
    print(f"\n{'=' * 60}")
    print(f"Migration complete!")
    print(f"SQLite database: {SQLITE_PATH}")
    print(f"Size: {size_mb:.2f} MB")
    print(f"{'=' * 60}")

if __name__ == "__main__":
    main()
