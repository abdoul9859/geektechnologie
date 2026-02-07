from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from typing import Optional
from datetime import datetime
from pathlib import Path

from ..database import Migration, MigrationLog, get_next_id
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
    current_user=Depends(get_current_user),
):
    """Retourne la liste des migrations (triees par date de creation DESC)."""
    try:
        filters: dict = {}
        if type:
            filters["type"] = type
        if status:
            filters["status"] = status

        total = await Migration.find(filters).count()
        items = (
            await Migration.find(filters)
            .sort(-Migration.created_at)
            .skip(skip)
            .limit(limit)
            .to_list()
        )
        return [serialize_migration(m) for m in items]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{migration_id}")
async def get_migration(
    migration_id: int,
    current_user=Depends(get_current_user),
):
    m = await Migration.find_one(Migration.migration_id == migration_id)
    if not m:
        raise HTTPException(status_code=404, detail="Migration non trouvee")
    return serialize_migration(m)


@router.get("/{migration_id}/logs")
async def get_migration_logs(
    migration_id: int,
    current_user=Depends(get_current_user),
):
    m = await Migration.find_one(Migration.migration_id == migration_id)
    if not m:
        raise HTTPException(status_code=404, detail="Migration non trouvee")
    logs = (
        await MigrationLog.find(MigrationLog.migration_id == migration_id)
        .sort(+MigrationLog.timestamp)
        .to_list()
    )
    return [serialize_log(l) for l in logs]


@router.post("/")
async def create_migration(
    payload: dict,
    current_user=Depends(get_current_user),
):
    """Cree une entree de migration (declaration)."""
    try:
        name = payload.get("name")
        mtype = payload.get("type")
        if not name or not mtype:
            raise HTTPException(status_code=400, detail="Champs 'name' et 'type' requis")

        new_id = await get_next_id("migrations")
        m = Migration(
            migration_id=new_id,
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
        await m.insert()

        # Optionnel: premier log
        first_log_msg = payload.get("log_message")
        if first_log_msg:
            log_id = await get_next_id("migration_logs")
            log = MigrationLog(
                log_id=log_id,
                migration_id=m.migration_id,
                level=payload.get("log_level", "info"),
                message=first_log_msg,
            )
            await log.insert()

        return serialize_migration(m)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{migration_id}/start")
async def start_migration(
    migration_id: int,
    payload: dict = {},
    current_user=Depends(get_current_user),
):
    """Passe une migration a l'etat running et initialise les compteurs si fournis."""
    try:
        m = await Migration.find_one(Migration.migration_id == migration_id)
        if not m:
            raise HTTPException(status_code=404, detail="Migration non trouvee")
        m.status = "running"
        m.error_message = None
        m.processed_records = payload.get("processed_records", 0)
        m.success_records = payload.get("success_records", 0)
        m.error_records = payload.get("error_records", 0)
        m.total_records = payload.get("total_records", m.total_records)
        await m.save()

        # Log
        log_id = await get_next_id("migration_logs")
        log = MigrationLog(
            log_id=log_id,
            migration_id=migration_id,
            level="info",
            message=payload.get("message", "Migration demarree"),
        )
        await log.insert()

        return serialize_migration(m)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{migration_id}/complete")
async def complete_migration(
    migration_id: int,
    payload: dict,
    current_user=Depends(get_current_user),
):
    """Cloture une migration."""
    try:
        m = await Migration.find_one(Migration.migration_id == migration_id)
        if not m:
            raise HTTPException(status_code=404, detail="Migration non trouvee")
        m.processed_records = payload.get("processed_records", m.processed_records)
        m.success_records = payload.get("success_records", m.success_records)
        m.error_records = payload.get("error_records", m.error_records)
        m.total_records = payload.get("total_records", m.total_records)
        m.error_message = payload.get("error_message")
        m.status = payload.get("status", ("failed" if m.error_message else "completed"))
        m.completed_at = datetime.utcnow()
        await m.save()

        # Log
        end_msg = payload.get("message") or (
            "Migration terminee" if m.status == "completed" else "Migration echouee"
        )
        log_id = await get_next_id("migration_logs")
        log = MigrationLog(
            log_id=log_id,
            migration_id=migration_id,
            level=("success" if m.status == "completed" else "error"),
            message=end_msg,
        )
        await log.insert()

        return serialize_migration(m)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{migration_id}/logs")
async def add_log(
    migration_id: int,
    payload: dict,
    current_user=Depends(get_current_user),
):
    """Ajoute un log a une migration."""
    try:
        m = await Migration.find_one(Migration.migration_id == migration_id)
        if not m:
            raise HTTPException(status_code=404, detail="Migration non trouvee")
        level = payload.get("level", "info")
        message = payload.get("message")
        if not message:
            raise HTTPException(status_code=400, detail="'message' requis")
        log_id = await get_next_id("migration_logs")
        log = MigrationLog(
            log_id=log_id,
            migration_id=migration_id,
            level=level,
            message=message,
        )
        await log.insert()
        return serialize_log(log)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{migration_id}/upload")
async def upload_migration_file(
    migration_id: int,
    file: UploadFile = File(...),
    current_user=Depends(get_current_user),
):
    """Upload d'un fichier pour une migration."""
    try:
        m = await Migration.find_one(Migration.migration_id == migration_id)
        if not m:
            raise HTTPException(status_code=404, detail="Migration non trouvee")

        base_dir = Path("uploads") / "migrations"
        base_dir.mkdir(parents=True, exist_ok=True)

        ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        safe_name = file.filename.replace("..", "_")
        dest_path = base_dir / f"{migration_id}_{ts}_{safe_name}"

        with dest_path.open("wb") as f:
            content = await file.read()
            f.write(content)

        m.file_name = str(dest_path.name)
        await m.save()

        log_id = await get_next_id("migration_logs")
        log = MigrationLog(
            log_id=log_id,
            migration_id=migration_id,
            level="info",
            message=f"Fichier charge: {m.file_name}",
        )
        await log.insert()

        return serialize_migration(m)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
