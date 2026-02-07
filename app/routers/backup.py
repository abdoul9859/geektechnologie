from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse
from ..auth import get_current_user
import os
import json
import io
from datetime import datetime, date
from decimal import Decimal
import logging

from ..database import get_database, ALL_DOCUMENT_MODELS

router = APIRouter(prefix="/api/backup", tags=["backup"])


def _json_serializer(obj):
    """Custom JSON serializer for MongoDB documents."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    if hasattr(obj, "__str__"):
        return str(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


@router.get("/create")
async def create_backup(current_user=Depends(get_current_user)):
    """Creer une sauvegarde de la base de donnees (export JSON de toutes les collections)"""
    try:
        # Verifier que l'utilisateur est admin
        if current_user.role != "admin":
            raise HTTPException(status_code=403, detail="Acces refuse. Administrateur requis.")

        db = get_database()
        collections = await db.list_collection_names()

        backup_data = {
            "backup_date": datetime.now().isoformat(),
            "backup_type": "mongodb",
            "collections": {}
        }

        for col_name in collections:
            # Skip internal collections
            if col_name.startswith("system.") or col_name == "counters":
                continue
            docs = await db[col_name].find({}).to_list(length=None)
            # Convert ObjectId and other BSON types to serializable form
            serialized_docs = []
            for doc in docs:
                clean = {}
                for k, v in doc.items():
                    if k == "_id":
                        clean[k] = str(v)
                    elif isinstance(v, Decimal):
                        clean[k] = float(v)
                    elif isinstance(v, (datetime, date)):
                        clean[k] = v.isoformat()
                    else:
                        clean[k] = v
                serialized_docs.append(clean)
            backup_data["collections"][col_name] = serialized_docs

        # Also export the counters collection for auto-increment state
        counters = await db["counters"].find({}).to_list(length=None)
        backup_data["counters"] = [
            {"_id": str(c["_id"]), "seq": c.get("seq", 0)} for c in counters
        ]

        # Serialize to JSON
        json_content = json.dumps(backup_data, default=_json_serializer, ensure_ascii=False, indent=2)

        date_str = datetime.now().strftime("%Y-%m-%d")
        backup_filename = f"geek-technologie-backup-{date_str}.json"

        return StreamingResponse(
            io.BytesIO(json_content.encode("utf-8")),
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="{backup_filename}"'
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Erreur lors de la creation de la sauvegarde: {e}")
        raise HTTPException(status_code=500, detail="Erreur lors de la creation de la sauvegarde")


@router.post("/restore")
async def restore_backup(
    file: UploadFile = File(...),
    current_user=Depends(get_current_user)
):
    """Restaurer une sauvegarde de la base de donnees (import JSON)"""
    try:
        # Verifier que l'utilisateur est admin
        if current_user.role != "admin":
            raise HTTPException(status_code=403, detail="Acces refuse. Administrateur requis.")

        # Verifier l'extension du fichier
        filename = file.filename or ""
        lower_name = filename.lower()
        if not lower_name.endswith(".json"):
            raise HTTPException(
                status_code=400,
                detail="Le fichier doit etre un .json (sauvegarde MongoDB)"
            )

        # Lire le contenu
        content = await file.read()
        if len(content) < 50:
            raise HTTPException(status_code=400, detail="Le fichier semble invalide (trop petit)")

        # Decoder le JSON
        try:
            raw = content.decode("utf-8")
        except UnicodeDecodeError:
            try:
                raw = content.decode("utf-8-sig")
            except UnicodeDecodeError:
                raw = content.decode("latin-1")

        try:
            backup_data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise HTTPException(status_code=400, detail=f"JSON invalide: {e}")

        if "collections" not in backup_data:
            raise HTTPException(
                status_code=400,
                detail="Format de sauvegarde invalide: cle 'collections' manquante"
            )

        db = get_database()

        # Create a safety backup before restoring
        safety_backup = {
            "backup_date": datetime.now().isoformat(),
            "backup_type": "pre_restore_safety",
            "collections": {}
        }
        existing_collections = await db.list_collection_names()
        for col_name in existing_collections:
            if col_name.startswith("system.") or col_name == "counters":
                continue
            docs = await db[col_name].find({}).to_list(length=None)
            safety_backup["collections"][col_name] = [
                {k: str(v) if k == "_id" else v for k, v in doc.items()}
                for doc in docs
            ]

        # Save safety backup to /tmp
        safety_filename = f"pre-restore-safety-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        safety_path = f"/tmp/{safety_filename}"
        try:
            with open(safety_path, "w", encoding="utf-8") as f:
                json.dump(safety_backup, f, default=_json_serializer, ensure_ascii=False)
            logging.info(f"Sauvegarde de securite creee: {safety_path}")
        except Exception as e:
            logging.warning(f"Impossible de creer la sauvegarde de securite: {e}")
            safety_path = None

        # Drop and restore each collection
        try:
            collections_data = backup_data["collections"]

            for col_name, docs in collections_data.items():
                if col_name.startswith("system."):
                    continue
                # Drop the existing collection
                await db[col_name].drop()
                # Insert documents
                if docs:
                    # Remove string _id fields so MongoDB generates new ObjectIds,
                    # unless they look like they should be preserved
                    clean_docs = []
                    for doc in docs:
                        clean = dict(doc)
                        # Remove the string _id; MongoDB will auto-generate ObjectId
                        if "_id" in clean and isinstance(clean["_id"], str):
                            del clean["_id"]
                        clean_docs.append(clean)
                    if clean_docs:
                        await db[col_name].insert_many(clean_docs)

            # Restore counters collection
            if "counters" in backup_data:
                await db["counters"].drop()
                counters = backup_data["counters"]
                if counters:
                    counter_docs = []
                    for c in counters:
                        counter_docs.append({"_id": c["_id"], "seq": c.get("seq", 0)})
                    if counter_docs:
                        await db["counters"].insert_many(counter_docs)

        except Exception as e:
            logging.error(f"Echec restauration: {e}")
            # Attempt to restore from safety backup
            if safety_path and os.path.exists(safety_path):
                try:
                    with open(safety_path, "r", encoding="utf-8") as f:
                        safety_data = json.load(f)
                    for col_name, docs in safety_data.get("collections", {}).items():
                        await db[col_name].drop()
                        if docs:
                            clean_docs = []
                            for doc in docs:
                                clean = dict(doc)
                                if "_id" in clean and isinstance(clean["_id"], str):
                                    del clean["_id"]
                                clean_docs.append(clean)
                            if clean_docs:
                                await db[col_name].insert_many(clean_docs)
                    logging.info("Base restauree depuis la sauvegarde de securite")
                except Exception as restore_err:
                    logging.error(f"Impossible de restaurer la sauvegarde de securite: {restore_err}")
            raise HTTPException(status_code=500, detail=f"Echec restauration: {e}")

        logging.info(f"Base de donnees restauree depuis {file.filename}")

        return {
            "message": "Sauvegarde restauree avec succes",
            "backup_safety": safety_path or "non disponible",
            "collections_restored": list(collections_data.keys()),
            "total_collections": len(collections_data)
        }

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Erreur lors de la restauration: {e}")
        raise HTTPException(status_code=500, detail=f"Erreur lors de la restauration: {str(e)}")


@router.post("/sync-schema")
async def sync_schema(current_user=Depends(get_current_user)):
    """Synchroniser le schema (s'assurer que les index Beanie sont crees)."""
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Acces refuse. Administrateur requis.")
    try:
        # With Beanie/MongoDB, schema sync means ensuring indexes are created.
        # Beanie creates indexes during init_beanie(), but we can force re-creation.
        from beanie import init_beanie
        db = get_database()
        await init_beanie(database=db, document_models=ALL_DOCUMENT_MODELS)

        # Also ensure all collections exist
        existing = await db.list_collection_names()
        for model in ALL_DOCUMENT_MODELS:
            col_name = model.Settings.name if hasattr(model, 'Settings') and hasattr(model.Settings, 'name') else model.__name__.lower() + "s"
            if col_name not in existing:
                await db.create_collection(col_name)

        return {"message": "Synchronisation du schema effectuee"}
    except Exception as e:
        logging.error(f"Erreur sync schema: {e}")
        raise HTTPException(status_code=500, detail="Erreur lors de la synchronisation du schema")
