from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime
import os
from pathlib import Path

from ..database import get_db, User, Migration, MigrationLog
from ..auth import get_current_user

router = APIRouter(prefix="/api/migrations", tags=["migrations"])


def serialize_migration(m: Migration) -> dict:
    return {
        "id": m.migration_id,
        "name": m.name,
        "type": m.type,
        "status": m.status,
        "created_at": m.created_at.isoformat() if m.created_at else None,
        "completed_at": m.completed_at.isoformat() if m.completed_at else None,
        "total_records": m.total_records,
        "processed_records": m.processed_records,
        "success_records": m.success_records,
        "error_records": m.error_records,
        "file_name": m.file_name,
        "description": m.description,
        "error_message": m.error_message,
    }


def serialize_log(l: MigrationLog) -> dict:
    return {
        "id": l.log_id,
        "migration_id": l.migration_id,
        "timestamp": (l.timestamp.isoformat() if l.timestamp else None),
        "level": l.level,
        "message": l.message,
    }


@router.get("/")
async def list_migrations(
    skip: int = 0,
    limit: int = 50,
    type: Optional[str] = None,
    status: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Retourne la liste des migrations (triées par date de création DESC)."""
    try:
        query = db.query(Migration)
        if type:
            query = query.filter(Migration.type == type)
        if status:
            query = query.filter(Migration.status == status)
        total = query.count()
        items = (
            query
            .order_by(Migration.created_at.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )
        return [serialize_migration(m) for m in items]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{migration_id}")
async def get_migration(
    migration_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    m = db.query(Migration).get(migration_id)
    if not m:
        raise HTTPException(status_code=404, detail="Migration non trouvée")
    return serialize_migration(m)


@router.get("/{migration_id}/logs")
async def get_migration_logs(
    migration_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    m = db.query(Migration).get(migration_id)
    if not m:
        raise HTTPException(status_code=404, detail="Migration non trouvée")
    logs = (
        db.query(MigrationLog)
        .filter(MigrationLog.migration_id == migration_id)
        .order_by(MigrationLog.timestamp.asc())
        .all()
    )
    return [serialize_log(l) for l in logs]


@router.post("/")
async def create_migration(
    payload: dict,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Crée une entrée de migration (déclaration). Le traitement peut être géré ailleurs."""
    try:
        name = payload.get("name")
        mtype = payload.get("type")
        if not name or not mtype:
            raise HTTPException(status_code=400, detail="Champs 'name' et 'type' requis")

        m = Migration(
            name=name,
            type=mtype,
            status=payload.get("status", "pending"),
            total_records=payload.get("total_records", 0),
            processed_records=payload.get("processed_records", 0),
            success_records=payload.get("success_records", 0),
            error_records=payload.get("error_records", 0),
            file_name=payload.get("file_name"),
            description=payload.get("description"),
            error_message=payload.get("error_message"),
            created_by=current_user.user_id,
        )
        db.add(m)
        db.commit()
        db.refresh(m)

        # Optionnel: premier log
        first_log_msg = payload.get("log_message")
        if first_log_msg:
            log = MigrationLog(
                migration_id=m.migration_id,
                level=payload.get("log_level", "info"),
                message=first_log_msg,
            )
            db.add(log)
            db.commit()

        return serialize_migration(m)
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{migration_id}/start")
async def start_migration(
    migration_id: int,
    payload: dict = {},
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Passe une migration à l'état running et initialise les compteurs si fournis."""
    try:
        m = db.query(Migration).get(migration_id)
        if not m:
            raise HTTPException(status_code=404, detail="Migration non trouvée")
        m.status = "running"
        m.error_message = None
        m.processed_records = payload.get("processed_records", 0)
        m.success_records = payload.get("success_records", 0)
        m.error_records = payload.get("error_records", 0)
        m.total_records = payload.get("total_records", m.total_records)
        db.add(m)
        # Log
        db.add(MigrationLog(migration_id=migration_id, level="info", message=payload.get("message", "Migration démarrée")))
        db.commit()
        db.refresh(m)
        return serialize_migration(m)
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{migration_id}/complete")
async def complete_migration(
    migration_id: int,
    payload: dict,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Clôture une migration: met à jour les compteurs et status completed/failed, et completed_at."""
    try:
        m = db.query(Migration).get(migration_id)
        if not m:
            raise HTTPException(status_code=404, detail="Migration non trouvée")
        m.processed_records = payload.get("processed_records", m.processed_records)
        m.success_records = payload.get("success_records", m.success_records)
        m.error_records = payload.get("error_records", m.error_records)
        m.total_records = payload.get("total_records", m.total_records)
        m.error_message = payload.get("error_message")
        m.status = payload.get("status", ("failed" if m.error_message else "completed"))
        m.completed_at = datetime.utcnow()
        db.add(m)
        # Log
        end_msg = payload.get("message") or ("Migration terminée" if m.status == "completed" else "Migration échouée")
        db.add(MigrationLog(migration_id=migration_id, level=("success" if m.status == "completed" else "error"), message=end_msg))
        db.commit()
        db.refresh(m)
        return serialize_migration(m)
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{migration_id}/logs")
async def add_log(
    migration_id: int,
    payload: dict,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Ajoute un log à une migration."""
    try:
        m = db.query(Migration).get(migration_id)
        if not m:
            raise HTTPException(status_code=404, detail="Migration non trouvée")
        level = payload.get("level", "info")
        message = payload.get("message")
        if not message:
            raise HTTPException(status_code=400, detail="'message' requis")
        log = MigrationLog(migration_id=migration_id, level=level, message=message)
        db.add(log)
        db.commit()
        db.refresh(log)
        return serialize_log(log)
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{migration_id}/upload")
async def upload_migration_file(
    migration_id: int,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Upload d'un fichier pour une migration. Le fichier est enregistré et le nom associé à la migration."""
    try:
        m = db.query(Migration).get(migration_id)
        if not m:
            raise HTTPException(status_code=404, detail="Migration non trouvée")

        # Répertoire de destination
        base_dir = Path("uploads") / "migrations"
        base_dir.mkdir(parents=True, exist_ok=True)

        ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        safe_name = file.filename.replace("..", "_")
        dest_path = base_dir / f"{migration_id}_{ts}_{safe_name}"

        with dest_path.open("wb") as f:
            content = await file.read()
            f.write(content)

        # Mettre à jour la migration
        m.file_name = str(dest_path.name)
        db.add(m)
        db.add(MigrationLog(migration_id=migration_id, level="info", message=f"Fichier chargé: {m.file_name}"))
        db.commit()
        db.refresh(m)
        return serialize_migration(m)
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
