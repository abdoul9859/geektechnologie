"""
Système de cache pour améliorer les performances
"""

from functools import wraps
from typing import Any, Optional, Callable
import json
import hashlib
from datetime import datetime, timedelta
from .database import AppCache, get_next_id


class CacheManager:
    """Gestionnaire de cache pour les requêtes fréquentes"""

    @staticmethod
    def _generate_key(prefix: str, *args, **kwargs) -> str:
        """Génère une clé de cache unique"""
        data = f"{prefix}:{args}:{sorted(kwargs.items())}"
        return hashlib.md5(data.encode()).hexdigest()

    @staticmethod
    async def get(key: str) -> Optional[Any]:
        """Récupère une valeur du cache"""
        try:
            cache_entry = await AppCache.find_one(
                {"cache_key": key, "expires_at": {"$gt": datetime.now()}}
            )
            if cache_entry:
                return json.loads(cache_entry.cache_value)
            return None
        except Exception:
            return None

    @staticmethod
    async def set(key: str, value: Any, ttl_minutes: int = 15):
        """Stocke une valeur dans le cache"""
        try:
            expires_at = datetime.now() + timedelta(minutes=ttl_minutes)
            cache_value = json.dumps(value, default=str)

            # Supprimer l'ancienne entrée si elle existe
            existing = await AppCache.find_one(AppCache.cache_key == key)
            if existing:
                existing.cache_value = cache_value
                existing.expires_at = expires_at
                await existing.save()
            else:
                new_id = await get_next_id("app_cache")
                cache_entry = AppCache(
                    cache_id=new_id,
                    cache_key=key,
                    cache_value=cache_value,
                    expires_at=expires_at,
                )
                await cache_entry.insert()
        except Exception:
            pass

    @staticmethod
    async def clear_expired():
        """Nettoie les entrées expirées du cache"""
        try:
            await AppCache.find(
                {"expires_at": {"$lte": datetime.now()}}
            ).delete()
        except Exception:
            pass
