"""
Système de cache pour améliorer les performances
"""

from functools import wraps
from typing import Any, Optional, Callable
import json
import hashlib
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from .database import AppCache, get_db

class CacheManager:
    """Gestionnaire de cache pour les requêtes fréquentes"""
    
    @staticmethod
    def _generate_key(prefix: str, *args, **kwargs) -> str:
        """Génère une clé de cache unique"""
        data = f"{prefix}:{args}:{sorted(kwargs.items())}"
        return hashlib.md5(data.encode()).hexdigest()
    
    @staticmethod
    def get(db: Session, key: str) -> Optional[Any]:
        """Récupère une valeur du cache"""
        try:
            cache_entry = db.query(AppCache).filter(
                AppCache.cache_key == key,
                AppCache.expires_at > datetime.now()
            ).first()
            
            if cache_entry:
                return json.loads(cache_entry.cache_value)
            return None
        except Exception:
            return None
    
    @staticmethod
    def set(db: Session, key: str, value: Any, ttl_minutes: int = 15):
        """Stocke une valeur dans le cache"""
        try:
            expires_at = datetime.now() + timedelta(minutes=ttl_minutes)
            cache_value = json.dumps(value, default=str)
            
            # Supprimer l'ancienne entrée si elle existe
            db.query(AppCache).filter(AppCache.cache_key == key).delete()
            
            # Créer la nouvelle entrée
            cache_entry = AppCache(
                cache_key=key,
                cache_value=cache_value,
                expires_at=expires_at
            )
            db.add(cache_entry)
            db.commit()
        except Exception:
            db.rollback()
    
    @staticmethod
    def clear_expired(db: Session):
        """Nettoie les entrées expirées du cache"""
        try:
            db.query(AppCache).filter(
                AppCache.expires_at <= datetime.now()
            ).delete()
            db.commit()
        except Exception:
            db.rollback()
