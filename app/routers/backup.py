from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from ..auth import get_current_user
import os
import shutil
from datetime import datetime
import logging
from pathlib import Path
import sqlite3
import re
import os as _os
import time
from ..database import engine as _engine
from ..database import create_tables as _create_tables

router = APIRouter(prefix="/api/backup", tags=["backup"])

# Remplacement atomique avec retries (Windows peut garder un lock fugitif)
def _atomic_replace_with_retries(src: str, dest: str, retries: int = 6, delay: float = 0.5):
    last_err = None
    for _ in range(retries):
        try:
            _os.replace(src, dest)
            return
        except Exception as e:
            last_err = e
            time.sleep(delay)
    # dernier essai avec fallback
    try:
        if _os.path.exists(dest):
            _os.remove(dest)
        shutil.move(src, dest)
        return
    except Exception:
        pass
    raise last_err

# Conversion minimale d'un dump PostgreSQL (INSERT-based) vers SQL compatible SQLite
def _sanitize_postgres_sql_for_sqlite(sql_text: str) -> str:
    # Supprimer BOM éventuel
    if sql_text and sql_text[0] == "\ufeff":
        sql_text = sql_text.lstrip("\ufeff")

    # Pré-nettoyage global: retirer tous les qualificateurs de schéma public.
    sql_text = re.sub(r'(?i)"public"\.', '', sql_text)
    sql_text = re.sub(r'(?i)public\.', '', sql_text)

    # Supprimer entièrement les blocs multi-lignes SEQUENCE et TYPE ENUM spécifiques PostgreSQL
    # CREATE SEQUENCE ... ;
    sql_text = re.sub(r'(?is)CREATE\s+SEQUENCE\b.*?;', '', sql_text)
    # ALTER SEQUENCE ... ;
    sql_text = re.sub(r'(?is)ALTER\s+SEQUENCE\b.*?;', '', sql_text)
    # CREATE TYPE ... AS ENUM (...);
    sql_text = re.sub(r'(?is)CREATE\s+TYPE\b.*?AS\s+ENUM\s*\(.*?\)\s*;', '', sql_text)
    
    # Réécrire CREATE INDEX/UNIQUE INDEX avec USING btree vers une forme SQLite
    # Ex: CREATE INDEX name ON table USING btree (col1, col2);
    def _rewrite_index(m):
        unique = m.group(1) or ''
        name = m.group(2)
        table = m.group(3)
        cols = m.group(4)
        return f"CREATE {unique}INDEX {name} ON {table} ({cols});"

    # Sans WHERE/ WITH ( ... )
    sql_text = re.sub(
        r'(?is)CREATE\s+(UNIQUE\s+)?INDEX\s+(["\w]+)\s+ON\s+(["\w\.]+)\s+USING\s+["\w]+\s*\((.*?)\)\s*;'
        , _rewrite_index, sql_text)

    # Variante multi-lignes avec WITH (...) optionnel
    sql_text = re.sub(
        r'(?is)CREATE\s+(UNIQUE\s+)?INDEX\s+(["\w]+)\s+ON\s+(["\w\.]+)\s+USING\s+["\w]+\s*\((.*?)\)\s*WITH\s*\(.*?\)\s*;'
        , _rewrite_index, sql_text)

    # Variante avec WHERE (index partiel) -> on supprime la clause WHERE
    sql_text = re.sub(
        r'(?is)CREATE\s+(UNIQUE\s+)?INDEX\s+(["\w]+)\s+ON\s+(["\w\.]+)\s+USING\s+["\w]+\s*\((.*?)\)\s*WHERE\s+.*?;'
        , _rewrite_index, sql_text)
    # Variantes étendues: CONCURRENTLY / IF NOT EXISTS / ONLY / WITH / WHERE
    sql_text = re.sub(
        r'(?is)CREATE\s+(UNIQUE\s+)?INDEX\s+(?:CONCURRENTLY\s+)?(?:IF\s+NOT\s+EXISTS\s+)?(["\w]+)\s+ON\s+(?:ONLY\s+)?(["\w\.]+)\s+USING\s+["]?\w+["]?\s*\((.*?)\)\s*(?:WITH\s*\(.*?\))?\s*(?:WHERE\s+.*?)?\s*;'
        , _rewrite_index, sql_text)
    # ALTER TABLE ... ; (supprimer le bloc entier pour éviter les lignes 'ADD ...')
    sql_text = re.sub(r'(?is)ALTER\s+TABLE\b.*?;', '', sql_text)
    # COMMENT ON ... ; (supprimer tout le bloc)
    sql_text = re.sub(r'(?is)COMMENT\s+ON\b.*?;', '', sql_text)
    # OWNER TO ... ; en fin d'instruction (sécurité supplémentaire)
    sql_text = re.sub(r'(?is)\s+OWNER\s+TO\s+\w+;?', ';', sql_text)

    lines = sql_text.splitlines()
    out = []
    skip_prefixes = (
        "SET ",
        "SELECT pg_catalog.set_config",
        "ALTER TABLE ONLY ",
        "ALTER TABLE ",
        "ALTER SEQUENCE ",
        "CREATE SEQUENCE ",
        "COMMENT ON ",
        "REVOKE ",
        "GRANT ",
        "OWNER TO ",
        "CREATE EXTENSION",
        "DROP EXTENSION",
        "CREATE SCHEMA ",
        "DROP SCHEMA ",
        "DROP TABLE ",
        "DROP INDEX ",
        "RESET ",
        "SELECT set_config",
        "SELECT pg_catalog.setval",
        "SELECT setval",
        "SET default_tablespace",
        "SET default_table_access_method",
    )
    for raw in lines:
        s = raw.strip()
        if not s:
            continue
        # Rejeter les lignes COPY ou backslash commandes PostgreSQL
        if s.startswith("COPY ") or s == "\\." or s.startswith("\\"):
            # Sera géré plus haut par une détection globale, mais on filtre au cas où
            continue
        # Supprimer tout ce qui touche aux SEQUENCE/OWNED BY même si pas en tête
        if re.search(r"\bSEQUENCE\b", s, flags=re.IGNORECASE):
            continue
        if any(s.startswith(pref) for pref in skip_prefixes):
            continue
        # Retirer les clauses OWNER ou autres artefacts en fin de ligne
        s = re.sub(r"\s+OWNER TO\s+\w+;?$", ";", s, flags=re.IGNORECASE)
        # Supprimer le préfixe de schéma public. sur les identifiants
        s = re.sub(r'(?i)"public"\.', '', s)
        s = re.sub(r'(?i)public\.', '', s)
        # Remplacer les types cités par des types SQLite compatibles
        s = re.sub(r'"text"', 'TEXT', s, flags=re.IGNORECASE)
        s = re.sub(r'"date"', 'TEXT', s, flags=re.IGNORECASE)
        # Remplacements de types courants
        s = re.sub(r"\bBIGSERIAL\b", "INTEGER", s, flags=re.IGNORECASE)
        s = re.sub(r"\bSERIAL\b", "INTEGER", s, flags=re.IGNORECASE)
        s = re.sub(r"\bUUID\b", "TEXT", s, flags=re.IGNORECASE)
        s = re.sub(r"\bBYTEA\b", "BLOB", s, flags=re.IGNORECASE)
        s = re.sub(r"\bDOUBLE PRECISION\b", "REAL", s, flags=re.IGNORECASE)
        s = re.sub(r"\bTIMESTAMP WITH TIME ZONE\b", "TEXT", s, flags=re.IGNORECASE)
        s = re.sub(r"\bTIMESTAMP WITHOUT TIME ZONE\b", "TEXT", s, flags=re.IGNORECASE)
        # DEFAULT nextval(...) -> supprimer le DEFAULT pour SQLite
        s = re.sub(r"DEFAULT\s+nextval\('[^']+'::regclass\)", "", s, flags=re.IGNORECASE)
        # true/false -> 1/0 dans les INSERT
        s = re.sub(r"\bTRUE\b", "1", s, flags=re.IGNORECASE)
        s = re.sub(r"\bFALSE\b", "0", s, flags=re.IGNORECASE)
        # Supprimer les contraintes spécifiques (ON UPDATE CURRENT_TIMESTAMP etc.)
        s = re.sub(r"ON UPDATE CURRENT_TIMESTAMP", "", s, flags=re.IGNORECASE)
        # Nettoyer des virgules/doubles espaces résiduels
        s = re.sub(r"\s+\)", ")", s)
        s = re.sub(r"\(\s+", "(", s)
        out.append(s)
    return "\n".join(out)

def _schema_sync_after_restore(db_path: str):
    try:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("PRAGMA busy_timeout=8000;")
            conn.execute("PRAGMA foreign_keys=OFF;")
            cur = conn.cursor()

            def table_exists(name: str) -> bool:
                cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,))
                return cur.fetchone() is not None

            def table_columns(table: str) -> set:
                cur.execute(f"PRAGMA table_info({table})")
                return {row[1] for row in cur.fetchall()}

            # categories
            if table_exists('categories'):
                cols = table_columns('categories')
                if 'requires_variants' not in cols:
                    conn.execute("ALTER TABLE categories ADD COLUMN requires_variants INTEGER DEFAULT 0")

            # products
            if table_exists('products'):
                cols = table_columns('products')
                if 'created_at' not in cols:
                    conn.execute("ALTER TABLE products ADD COLUMN created_at TEXT")
                if 'entry_date' not in cols:
                    conn.execute("ALTER TABLE products ADD COLUMN entry_date TEXT")
                if 'condition' not in cols:
                    conn.execute("ALTER TABLE products ADD COLUMN condition TEXT")
                if 'category' not in cols:
                    conn.execute("ALTER TABLE products ADD COLUMN category TEXT")
                if 'wholesale_price' not in cols:
                    conn.execute("ALTER TABLE products ADD COLUMN wholesale_price REAL")
                if 'purchase_price' not in cols:
                    conn.execute("ALTER TABLE products ADD COLUMN purchase_price REAL")
                if 'brand' not in cols:
                    conn.execute("ALTER TABLE products ADD COLUMN brand TEXT")
                if 'model' not in cols:
                    conn.execute("ALTER TABLE products ADD COLUMN model TEXT")
                if 'barcode' not in cols:
                    conn.execute("ALTER TABLE products ADD COLUMN barcode TEXT")
                if 'has_unique_serial' not in cols:
                    conn.execute("ALTER TABLE products ADD COLUMN has_unique_serial INTEGER DEFAULT 0")
                if 'notes' not in cols:
                    conn.execute("ALTER TABLE products ADD COLUMN notes TEXT")
                if 'image_path' not in cols:
                    conn.execute("ALTER TABLE products ADD COLUMN image_path TEXT")

            # product_variants
            if table_exists('product_variants'):
                cols = table_columns('product_variants')
                if 'is_sold' not in cols:
                    conn.execute("ALTER TABLE product_variants ADD COLUMN is_sold INTEGER DEFAULT 0")
                if 'condition' not in cols:
                    conn.execute("ALTER TABLE product_variants ADD COLUMN condition TEXT")

            # invoices
            if table_exists('invoices'):
                cols = table_columns('invoices')
                if 'date' not in cols:
                    conn.execute("ALTER TABLE invoices ADD COLUMN date TEXT")
                if 'due_date' not in cols:
                    conn.execute("ALTER TABLE invoices ADD COLUMN due_date TEXT")
                if 'payment_method' not in cols:
                    conn.execute("ALTER TABLE invoices ADD COLUMN payment_method TEXT")
                if 'subtotal' not in cols:
                    conn.execute("ALTER TABLE invoices ADD COLUMN subtotal REAL")
                if 'tax_rate' not in cols:
                    conn.execute("ALTER TABLE invoices ADD COLUMN tax_rate REAL")
                if 'tax_amount' not in cols:
                    conn.execute("ALTER TABLE invoices ADD COLUMN tax_amount REAL")
                if 'total' not in cols:
                    conn.execute("ALTER TABLE invoices ADD COLUMN total REAL")
                if 'paid_amount' not in cols:
                    conn.execute("ALTER TABLE invoices ADD COLUMN paid_amount REAL")
                if 'remaining_amount' not in cols:
                    conn.execute("ALTER TABLE invoices ADD COLUMN remaining_amount REAL")
                if 'notes' not in cols:
                    conn.execute("ALTER TABLE invoices ADD COLUMN notes TEXT")
                if 'show_tax' not in cols:
                    conn.execute("ALTER TABLE invoices ADD COLUMN show_tax INTEGER DEFAULT 1")
                if 'price_display' not in cols:
                    conn.execute("ALTER TABLE invoices ADD COLUMN price_display TEXT")
                if 'has_warranty' not in cols:
                    conn.execute("ALTER TABLE invoices ADD COLUMN has_warranty INTEGER DEFAULT 0")
                if 'warranty_duration' not in cols:
                    conn.execute("ALTER TABLE invoices ADD COLUMN warranty_duration INTEGER")
                if 'warranty_start_date' not in cols:
                    conn.execute("ALTER TABLE invoices ADD COLUMN warranty_start_date TEXT")
                if 'warranty_end_date' not in cols:
                    conn.execute("ALTER TABLE invoices ADD COLUMN warranty_end_date TEXT")
                if 'created_at' not in cols:
                    conn.execute("ALTER TABLE invoices ADD COLUMN created_at TEXT")
                if 'status' not in cols:
                    conn.execute("ALTER TABLE invoices ADD COLUMN status TEXT")

            # quotations
            if table_exists('quotations'):
                cols = table_columns('quotations')
                if 'date' not in cols:
                    conn.execute("ALTER TABLE quotations ADD COLUMN date TEXT")
                if 'total' not in cols:
                    conn.execute("ALTER TABLE quotations ADD COLUMN total REAL")
                if 'created_at' not in cols:
                    conn.execute("ALTER TABLE quotations ADD COLUMN created_at TEXT")
                if 'status' not in cols:
                    conn.execute("ALTER TABLE quotations ADD COLUMN status TEXT")

            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        logging.warning(f"Schema sync post-restore: {e}")

# Conversion des blocs COPY ... FROM stdin en INSERTs multi-lignes
def _convert_copy_blocks_to_inserts(sql_text: str) -> str:
    lines = sql_text.splitlines()
    out = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if re.match(r"^COPY\s+", line, flags=re.IGNORECASE):
            # Exemple: COPY public.table (col1, col2) FROM stdin;
            m = re.match(r"^COPY\s+([\w\.\"]+)\s*\(([^\)]*)\)\s+FROM\s+stdin;?", line, flags=re.IGNORECASE)
            if not m:
                # Ligne COPY non reconnue, ignorer jusqu'à fin du bloc
                i += 1
                while i < len(lines) and lines[i].strip() != "\\.":
                    i += 1
                i += 1
                continue
            table = m.group(1)
            # Supprimer le préfixe de schéma éventuel (public.)
            table = re.sub(r'(?i)\b"public"\.', '', table)
            table = re.sub(r'(?i)\bpublic\.', '', table)
            cols = [c.strip().strip('"') for c in m.group(2).split(',')]
            values_rows = []
            i += 1
            while i < len(lines):
                row = lines[i].rstrip('\n')
                if row.strip() == "\\.":
                    break
                # Les valeurs sont séparées par des tabs, \N = NULL
                parts = row.split('\t')
                conv = []
                for v in parts:
                    if v == r"\N":
                        conv.append("NULL")
                    else:
                        # Dé-échappage minimal: remplacer \\t et \\n+                        v = v.replace("\\\\", "\\").replace("\\t", "\t")
                        # Quoter en SQL avec échappement des quotes simples
                        conv.append("'" + v.replace("'", "''") + "'")
                values_rows.append("(" + ",".join(conv) + ")")
                i += 1
            # fermer le bloc COPY (ligne '\.')
            i += 1
            if values_rows:
                insert_sql = f"INSERT INTO {table} (" + ", ".join(cols) + ") VALUES\n  " + ",\n  ".join(values_rows) + ";"
                out.append(insert_sql)
        else:
            out.append(lines[i])
            i += 1
    return "\n".join(out)

@router.get("/create")
async def create_backup(current_user = Depends(get_current_user)):
    """Créer une sauvegarde de la base de données"""
    try:
        # Vérifier que l'utilisateur est admin
        if current_user.role != "admin":
            raise HTTPException(status_code=403, detail="Accès refusé. Administrateur requis.")
        
        # Chemin de la base de données
        db_path = "data/app.db"
        
        if not os.path.exists(db_path):
            raise HTTPException(status_code=404, detail="Base de données non trouvée")
        
        # Créer une copie temporaire pour le téléchargement
        date_str = datetime.now().strftime("%Y-%m-%d")
        backup_filename = f"geek-technologie-backup-{date_str}.db"
        temp_backup_path = f"data/{backup_filename}"
        
        # Copier le fichier
        shutil.copy2(db_path, temp_backup_path)
        
        # Retourner le fichier en téléchargement
        return FileResponse(
            path=temp_backup_path,
            filename=backup_filename,
            media_type="application/octet-stream"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Erreur lors de la création de la sauvegarde: {e}")
        raise HTTPException(status_code=500, detail="Erreur lors de la création de la sauvegarde")

@router.post("/restore")
async def restore_backup(
    file: UploadFile = File(...),
    current_user = Depends(get_current_user)
):
    """Restaurer une sauvegarde de la base de données"""
    try:
        # Vérifier que l'utilisateur est admin
        if current_user.role != "admin":
            raise HTTPException(status_code=403, detail="Accès refusé. Administrateur requis.")
        
        # Vérifier l'extension du fichier
        filename = file.filename or ""
        lower_name = filename.lower()
        is_db = lower_name.endswith('.db')
        is_sql = lower_name.endswith('.sql')
        if not (is_db or is_sql):
            raise HTTPException(status_code=400, detail="Le fichier doit être un .db (SQLite) ou un dump .sql")
        
        # Chemin de la base de données actuelle
        db_path = "data/app.db"
        data_dir = Path("data")
        tmp_dir = Path("/tmp")
        try:
            data_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        try:
            tmp_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        
        # Créer une sauvegarde de sécurité avant la restauration
        backup_safety_path = f"data/app.db.before-restore-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        if os.path.exists(db_path):
            shutil.copy2(db_path, backup_safety_path)
            logging.info(f"Sauvegarde de sécurité créée: {backup_safety_path}")
        
        if is_db:
            # Sauvegarder le fichier .db uploadé temporairement puis remplacer
            temp_db_path = str(tmp_dir / "temp_restore.db")
            with open(temp_db_path, "wb") as buffer:
                content = await file.read()
                buffer.write(content)

            if os.path.getsize(temp_db_path) < 1024:  # Moins de 1KB, probablement invalide
                os.remove(temp_db_path)
                raise HTTPException(status_code=400, detail="Le fichier semble invalide (trop petit)")

            try:
                _engine.dispose()
            except Exception:
                pass
            # Supprimer journaux SQLite éventuels
            for suffix in ("-wal", "-shm"):
                try:
                    _os.remove(db_path + suffix)
                except Exception:
                    pass
            time.sleep(0.3)
            try:
                _atomic_replace_with_retries(temp_db_path, db_path)
            except Exception:
                if os.path.exists(db_path):
                    os.remove(db_path)
                shutil.move(temp_db_path, db_path)
        else:
            # is_sql: créer une nouvelle base temporaire et exécuter le script SQL
            temp_sql_path = tmp_dir / "temp_restore.sql"
            with open(temp_sql_path, "wb") as buffer:
                content = await file.read()
                buffer.write(content)

            if temp_sql_path.stat().st_size < 100:  # script trop petit pour être un dump valable
                try:
                    temp_sql_path.unlink(missing_ok=True)
                except Exception:
                    pass
                raise HTTPException(status_code=400, detail="Le dump SQL semble invalide (trop petit)")

            temp_new_db = tmp_dir / "temp_restored.db"
            try:
                if temp_new_db.exists():
                    temp_new_db.unlink()
                # Exécuter le script SQL dans une nouvelle base SQLite
                conn = sqlite3.connect(str(temp_new_db))
                try:
                    # PRAGMAs pour limiter les I/O et collisions
                    conn.execute("PRAGMA busy_timeout=10000;")
                    conn.execute("PRAGMA journal_mode=DELETE;")
                    conn.execute("PRAGMA synchronous=OFF;")
                    conn.execute("PRAGMA temp_store=MEMORY;")
                    conn.execute("PRAGMA locking_mode=EXCLUSIVE;")
                    conn.execute("PRAGMA foreign_keys=OFF;")
                    # Lecture stricte pour préserver les accents (UTF-8), avec fallbacks
                    try:
                        with open(temp_sql_path, "r", encoding="utf-8", errors="strict") as f:
                            raw_script = f.read()
                    except UnicodeDecodeError:
                        try:
                            with open(temp_sql_path, "r", encoding="utf-8-sig", errors="strict") as f:
                                raw_script = f.read()
                        except UnicodeDecodeError:
                            with open(temp_sql_path, "r", encoding="latin-1", errors="strict") as f:
                                raw_script = f.read()
                    # Si le dump contient des COPY, les convertir en INSERT
                    if re.search(r"^COPY\s+", raw_script, flags=re.IGNORECASE | re.MULTILINE):
                        raw_script = _convert_copy_blocks_to_inserts(raw_script)
                    # Tentative de sanitization pour rendre compatible SQLite
                    sanitized = _sanitize_postgres_sql_for_sqlite(raw_script)
                    if not sanitized.strip():
                        raise HTTPException(status_code=400, detail="Le script SQL ne contient aucune instruction exécutable après conversion")
                    conn.executescript(sanitized)
                    conn.execute("PRAGMA foreign_keys=ON;")
                    conn.commit()
                finally:
                    conn.close()

                try:
                    _engine.dispose()
                except Exception:
                    pass
                for suffix in ("-wal", "-shm"):
                    try:
                        _os.remove(db_path + suffix)
                    except Exception:
                        pass
                time.sleep(0.3)
                try:
                    _atomic_replace_with_retries(str(temp_new_db), db_path)
                except Exception:
                    if os.path.exists(db_path):
                        os.remove(db_path)
                    shutil.move(str(temp_new_db), db_path)
                # Post-restore: créer les tables manquantes selon les modèles SQLAlchemy
                try:
                    _create_tables()
                except Exception as _e:
                    logging.warning(f"Sync schéma (create_tables) a rencontré un problème non bloquant: {_e}")
            except Exception as e:
                logging.error(f"Echec restauration depuis SQL: {e}")
                raise HTTPException(status_code=500, detail=f"Echec restauration depuis SQL: {e}")
            finally:
                try:
                    temp_sql_path.unlink(missing_ok=True)
                except Exception:
                    pass
        
        logging.info(f"Base de données restaurée depuis {file.filename}")
        
        return {
            "message": "Sauvegarde restaurée avec succès",
            "backup_safety": backup_safety_path
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Erreur lors de la restauration: {e}")
        # Tenter de restaurer la sauvegarde de sécurité si elle existe
        if 'backup_safety_path' in locals() and os.path.exists(backup_safety_path):
            try:
                shutil.copy2(backup_safety_path, db_path)
                logging.info("Base de données restaurée depuis la sauvegarde de sécurité")
            except Exception as restore_error:
                logging.error(f"Impossible de restaurer la sauvegarde de sécurité: {restore_error}")
        raise HTTPException(status_code=500, detail=f"Erreur lors de la restauration: {str(e)}")

@router.post("/sync-schema")
async def sync_schema(current_user = Depends(get_current_user)):
    """Synchroniser le schéma (ajoute colonnes manquantes, crée tables manquantes)."""
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Accès refusé. Administrateur requis.")
    db_path = "data/app.db"
    try:
        _create_tables()
        _schema_sync_after_restore(db_path)
        return {"message": "Synchronisation du schéma effectuée"}
    except Exception as e:
        logging.error(f"Erreur sync schéma: {e}")
        raise HTTPException(status_code=500, detail="Erreur lors de la synchronisation du schéma")
