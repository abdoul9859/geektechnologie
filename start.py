#!/usr/bin/env python3
"""
Script de démarrage pour l'application Geek Technologie
"""

import uvicorn
import os
import sys
from pathlib import Path

# Ajouter le répertoire racine au PYTHONPATH
root_dir = Path(__file__).parent
sys.path.insert(0, str(root_dir))

def main():
    """Démarrer l'application FastAPI"""
    print("🚀 Démarrage de Geek Technologie - Gestion de Stock")
    print("=" * 50)
    
    # Configuration
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    # Désactiver le reload par défaut en production (Koyeb)
    reload = os.getenv("RELOAD", "false").lower() == "true"
    
    print(f"📍 Serveur: http://{host}:{port}")
    print(f"🔄 Rechargement automatique: {'Activé' if reload else 'Désactivé'}")
    print(f"🗄️  Base de données: SQLite (app.db)")
    print("=" * 50)
    print("💡 Comptes par défaut:")
    print("   - Admin: admin / admin123")
    print("   - Utilisateur: user / user123")
    print("=" * 50)
    
    try:
        uvicorn.run(
            "main:app",
            host=host,
            port=port,
            reload=reload,
            log_level="info",
            access_log=True
        )
    except KeyboardInterrupt:
        print("\n👋 Arrêt de l'application")
    except Exception as e:
        print(f"❌ Erreur lors du démarrage: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
