#!/usr/bin/env python3
"""
Outil de purge du cache AppCache.

Exemples d'utilisation (dans l'hôte):
  docker exec -it Geek Technologie_app python scripts/clear_cache.py --all
  docker exec -it Geek Technologie_app python scripts/clear_cache.py --prefix migration_logs:
  docker exec -it Geek Technologie_app python scripts/clear_cache.py --clients-reminders
  docker exec -it Geek Technologie_app python scripts/clear_cache.py --expired-only
  docker exec -it Geek Technologie_app python scripts/clear_cache.py --prefix stats: --dry-run

Ce script s'appuie sur la configuration DB de app.database (DATABASE_URL) déjà injectée via l'environnement.
"""
from __future__ import annotations

import argparse
import sys
import os
from typing import List
from datetime import datetime

# Ensure project root is on sys.path when executed as a script (e.g., /app/scripts/clear_cache.py)
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from app.database import SessionLocal, AppCache  # type: ignore


def _list_keys(session, prefix: str | None = None, expired_only: bool = False) -> List[str]:
    q = session.query(AppCache.cache_key)
    if prefix:
        q = q.filter(AppCache.cache_key.like(f"{prefix}%"))
    if expired_only:
        now = datetime.now()
        q = q.filter(AppCache.expires_at.isnot(None)).filter(AppCache.expires_at <= now)
    return [k for (k,) in q.order_by(AppCache.cache_key.asc()).all()]  # type: ignore


def clear_all(session, dry_run: bool = False) -> int:
    if dry_run:
        keys = _list_keys(session)
        print(f"[DRY-RUN] {len(keys)} clés seraient supprimées.")
        for k in keys:
            print(f" - {k}")
        return len(keys)
    count = session.query(AppCache).delete(synchronize_session=False)
    session.commit()
    print(f"✅ Supprimé {count} entrées de cache (ALL)")
    return count


def clear_prefix(session, prefix: str, dry_run: bool = False) -> int:
    if not prefix:
        print("⚠️  --prefix requis")
        return 0
    keys = _list_keys(session, prefix=prefix)
    if dry_run:
        print(f"[DRY-RUN] {len(keys)} clés (prefix='{prefix}') seraient supprimées:")
        for k in keys:
            print(f" - {k}")
        return len(keys)
    count = session.query(AppCache).filter(AppCache.cache_key.like(f"{prefix}%")).delete(synchronize_session=False)
    session.commit()
    print(f"✅ Supprimé {count} entrées de cache (prefix='{prefix}')")
    return count


essential_client_prefix = "DEBT_REMINDER_LAST_SENT_"

def clear_clients_reminders(session, dry_run: bool = False) -> int:
    keys = _list_keys(session, prefix=essential_client_prefix)
    if dry_run:
        print(f"[DRY-RUN] {len(keys)} clés (rappels clients) seraient supprimées:")
        for k in keys:
            print(f" - {k}")
        return len(keys)
    count = session.query(AppCache).filter(AppCache.cache_key.like(f"{essential_client_prefix}%")).delete(synchronize_session=False)
    session.commit()
    print(f"✅ Supprimé {count} entrées de cache de rappels clients")
    return count


def clear_expired(session, dry_run: bool = False) -> int:
    now = datetime.now()
    keys = _list_keys(session, prefix=None, expired_only=True)
    if dry_run:
        print(f"[DRY-RUN] {len(keys)} clés expirées seraient supprimées:")
        for k in keys:
            print(f" - {k}")
        return len(keys)
    count = session.query(AppCache).filter(AppCache.expires_at.isnot(None)).filter(AppCache.expires_at <= now).delete(synchronize_session=False)
    session.commit()
    print(f"✅ Supprimé {count} entrées de cache expirées")
    return count


def parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Purger le cache AppCache")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--all", action="store_true", help="Supprimer toutes les entrées du cache")
    g.add_argument("--prefix", type=str, help="Supprimer les clés commençant par ce préfixe")
    g.add_argument("--clients-reminders", action="store_true", help="Supprimer les clés de rappels clients (DEBT_REMINDER_LAST_SENT_*)")
    g.add_argument("--expired-only", action="store_true", help="Supprimer uniquement les entrées expirées")
    p.add_argument("--dry-run", action="store_true", help="Lister sans supprimer")
    return p.parse_args(argv)


def main(argv: List[str]) -> int:
    args = parse_args(argv)
    session = SessionLocal()
    try:
        if args.all:
            clear_all(session, dry_run=args.dry_run)
        elif args.prefix:
            clear_prefix(session, prefix=args.prefix, dry_run=args.dry_run)
        elif args.clients_reminders:
            clear_clients_reminders(session, dry_run=args.dry_run)
        elif args.expired_only:
            clear_expired(session, dry_run=args.dry_run)
        else:
            print("Aucune action.")
            return 2
        return 0
    finally:
        try:
            session.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
