#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script de migration pour ajouter les colonnes de garantie à la table invoices
"""
import sqlite3
import sys
import io

# Forcer l'encodage UTF-8 pour la sortie
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

def migrate_database(db_path):
    """Ajoute les colonnes de garantie si elles n'existent pas"""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Vérifier si les colonnes existent déjà
        cursor.execute("PRAGMA table_info(invoices)")
        columns = [row[1] for row in cursor.fetchall()]
        
        columns_to_add = [
            ("has_warranty", "BOOLEAN DEFAULT 0"),
            ("warranty_duration", "INTEGER"),
            ("warranty_start_date", "DATE"),
            ("warranty_end_date", "DATE")
        ]
        
        for col_name, col_type in columns_to_add:
            if col_name not in columns:
                print(f"Ajout de la colonne {col_name}...")
                cursor.execute(f"ALTER TABLE invoices ADD COLUMN {col_name} {col_type}")
                print(f"✅ Colonne {col_name} ajoutée")
            else:
                print(f"ℹ️  Colonne {col_name} existe déjà")
        
        conn.commit()
        conn.close()
        print("\n✅ Migration terminée avec succès!")
        return True
        
    except Exception as e:
        print(f"\n❌ Erreur lors de la migration: {e}")
        return False

if __name__ == "__main__":
    db_path = "data/app.db"
    if len(sys.argv) > 1:
        db_path = sys.argv[1]
    
    print(f"Migration de la base de données: {db_path}\n")
    success = migrate_database(db_path)
    sys.exit(0 if success else 1)
