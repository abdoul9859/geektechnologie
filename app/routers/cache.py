from fastapi import APIRouter, Depends, HTTPException
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
import json

from ..auth import get_current_user

router = APIRouter(prefix="/api/cache", tags=["cache"])

# Simulateur de cache en memoire (a remplacer par Redis en production)
cache_storage: Dict[str, Dict[str, Any]] = {}


def serialize_cache_entry(key: str, entry: Dict[str, Any]) -> Dict[str, Any]:
    """Serialise une entree de cache pour l'API"""
    return {
        "key": key,
        "type": entry.get("type", "unknown"),
        "status": "active" if entry.get("expires_at", datetime.utcnow()) > datetime.utcnow() else "expired",
        "size": entry.get("size", 0),
        "hits": entry.get("hits", 0),
        "misses": entry.get("misses", 0),
        "created_at": entry.get("created_at", datetime.utcnow()).isoformat(),
        "expires_at": entry.get("expires_at", datetime.utcnow()).isoformat(),
        "last_accessed": entry.get("last_accessed", datetime.utcnow()).isoformat(),
        "data_preview": str(entry.get("data", ""))[:200]
        + ("..." if len(str(entry.get("data", ""))) > 200 else ""),
        "avg_response_time": entry.get("avg_response_time", 0),
    }


@router.get("/entries")
async def list_cache_entries(
    type: Optional[str] = None,
    status: Optional[str] = None,
    current_user=Depends(get_current_user),
):
    """Retourne la liste des entrees de cache"""
    try:
        entries = []
        now = datetime.utcnow()

        for key, entry in cache_storage.items():
            entry_status = "active" if entry.get("expires_at", now) > now else "expired"

            if type and entry.get("type") != type:
                continue

            if status and entry_status != status:
                continue

            entries.append(serialize_cache_entry(key, entry))

        entries.sort(key=lambda x: x["last_accessed"], reverse=True)

        return entries
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/entries/{key}")
async def get_cache_entry(
    key: str,
    current_user=Depends(get_current_user),
):
    """Recupere une entree de cache specifique"""
    if key not in cache_storage:
        raise HTTPException(status_code=404, detail="Entree de cache non trouvee")

    entry = cache_storage[key]
    entry["hits"] = entry.get("hits", 0) + 1
    entry["last_accessed"] = datetime.utcnow()

    return serialize_cache_entry(key, entry)


@router.post("/entries/{key}/refresh")
async def refresh_cache_entry(
    key: str,
    current_user=Depends(get_current_user),
):
    """Actualise une entree de cache (remet a jour l'expiration)"""
    if key not in cache_storage:
        raise HTTPException(status_code=404, detail="Entree de cache non trouvee")

    entry = cache_storage[key]
    entry["expires_at"] = datetime.utcnow() + timedelta(hours=1)
    entry["hits"] = entry.get("hits", 0) + 1
    entry["last_accessed"] = datetime.utcnow()

    return serialize_cache_entry(key, entry)


@router.delete("/entries/{key}")
async def delete_cache_entry(
    key: str,
    current_user=Depends(get_current_user),
):
    """Supprime une entree de cache"""
    if key not in cache_storage:
        raise HTTPException(status_code=404, detail="Entree de cache non trouvee")

    del cache_storage[key]
    return {"message": f"Entree '{key}' supprimee avec succes"}


@router.delete("/entries")
async def clear_all_cache(
    current_user=Depends(get_current_user),
):
    """Vide tout le cache"""
    cache_storage.clear()
    return {"message": "Cache vide avec succes"}


@router.get("/stats")
async def get_cache_stats(
    current_user=Depends(get_current_user),
):
    """Retourne les statistiques du cache"""
    try:
        now = datetime.utcnow()
        total_entries = len(cache_storage)
        active_entries = 0
        expired_entries = 0
        total_size = 0
        total_hits = 0
        total_misses = 0

        for entry in cache_storage.values():
            if entry.get("expires_at", now) > now:
                active_entries += 1
            else:
                expired_entries += 1

            total_size += entry.get("size", 0)
            total_hits += entry.get("hits", 0)
            total_misses += entry.get("misses", 0)

        hit_rate = (
            round((total_hits / (total_hits + total_misses)) * 100)
            if (total_hits + total_misses) > 0
            else 0
        )
        avg_response_time = (
            sum(entry.get("avg_response_time", 0) for entry in cache_storage.values()) / total_entries
            if total_entries > 0
            else 0
        )

        return {
            "total_entries": total_entries,
            "active_entries": active_entries,
            "expired_entries": expired_entries,
            "total_size": total_size,
            "memory_usage": total_size,
            "memory_percent": min(round((total_size / (100 * 1024 * 1024)) * 100), 100),
            "hit_rate": hit_rate,
            "total_hits": total_hits,
            "total_misses": total_misses,
            "avg_response_time": round(avg_response_time),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/entries")
async def create_cache_entry(
    payload: dict,
    current_user=Depends(get_current_user),
):
    """Cree une nouvelle entree de cache"""
    try:
        key = payload.get("key")
        if not key:
            raise HTTPException(status_code=400, detail="Cle requise")

        data = payload.get("data", "")
        ttl_hours = payload.get("ttl_hours", 1)
        cache_type = payload.get("type", "manual")

        size = len(json.dumps(data).encode("utf-8")) if data else 0

        cache_storage[key] = {
            "type": cache_type,
            "data": data,
            "size": size,
            "hits": 0,
            "misses": 0,
            "created_at": datetime.utcnow(),
            "expires_at": datetime.utcnow() + timedelta(hours=ttl_hours),
            "last_accessed": datetime.utcnow(),
            "avg_response_time": 0,
        }

        return serialize_cache_entry(key, cache_storage[key])
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/initialize")
async def initialize_cache(
    current_user=Depends(get_current_user),
):
    """Initialise le cache avec des donnees d'exemple"""
    try:
        initialize_sample_cache()
        return {
            "message": "Cache initialise avec des donnees d'exemple",
            "entries": len(cache_storage),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Utilitaires pour le cache
def get_cache_item(key: str) -> Optional[Any]:
    """Recupere un element du cache"""
    if key not in cache_storage:
        return None

    entry = cache_storage[key]
    now = datetime.utcnow()

    if entry.get("expires_at", now) <= now:
        entry["misses"] = entry.get("misses", 0) + 1
        return None

    entry["hits"] = entry.get("hits", 0) + 1
    entry["last_accessed"] = now
    return entry.get("data")


def set_cache_item(key: str, data: Any, ttl_hours: int = 1, cache_type: str = "auto") -> None:
    """Definit un element dans le cache"""
    size = len(json.dumps(data).encode("utf-8")) if data else 0

    cache_storage[key] = {
        "type": cache_type,
        "data": data,
        "size": size,
        "hits": 0,
        "misses": 0,
        "created_at": datetime.utcnow(),
        "expires_at": datetime.utcnow() + timedelta(hours=ttl_hours),
        "last_accessed": datetime.utcnow(),
        "avg_response_time": 0,
    }


def delete_cache_item(key: str) -> bool:
    """Supprime un element du cache"""
    if key in cache_storage:
        del cache_storage[key]
        return True
    return False


def initialize_sample_cache():
    """Initialise le cache avec quelques donnees d'exemple"""
    sample_data = [
        {
            "key": "products:list:recent",
            "type": "database",
            "data": {"products": [{"id": 1, "name": "Produit exemple"}]},
            "ttl_hours": 2,
        },
        {
            "key": "dashboard:stats",
            "type": "api",
            "data": {"total_products": 150, "total_clients": 45},
            "ttl_hours": 1,
        },
        {
            "key": "user:session:active",
            "type": "session",
            "data": {"user_id": 1, "role": "admin"},
            "ttl_hours": 8,
        },
    ]

    for item in sample_data:
        set_cache_item(item["key"], item["data"], item["ttl_hours"], item["type"])
