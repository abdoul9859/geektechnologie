"""
Database initialisation for MongoDB / Beanie.

- init_db() is called at FastAPI startup (from main.py)
- seed helpers for default data (admin user, categories, etc.)
"""
import os
from datetime import datetime
from .database import (
    init_db,
    get_next_id,
    User,
    Category,
    Client,
)
from .auth import get_password_hash


async def init_database():
    """Initialise Beanie and optionally seed default data."""
    # Connect to MongoDB & register document models
    await init_db()

    seed_defaults = os.getenv("SEED_DEFAULT_DATA", "false").lower() == "true"
    if not seed_defaults:
        print("ℹ️ Aucun semis de données par défaut (SEED_DEFAULT_DATA!=true)")
        return

    try:
        # Create admin user
        admin_user = await User.find_one(User.username == "admin")
        if not admin_user:
            new_id = await get_next_id("users")
            admin_user = User(
                user_id=new_id,
                username="admin",
                email="admin@geek-technologie.com",
                password_hash=get_password_hash("admin123"),
                full_name="Administrateur",
                role="admin",
                is_active=True,
            )
            await admin_user.insert()
            print("✅ Utilisateur admin créé")

        # Create normal user
        normal_user = await User.find_one(User.username == "user")
        if not normal_user:
            new_id = await get_next_id("users")
            normal_user = User(
                user_id=new_id,
                username="user",
                email="user@geek-technologie.com",
                password_hash=get_password_hash("user123"),
                full_name="Utilisateur",
                role="user",
                is_active=True,
            )
            await normal_user.insert()
            print("✅ Utilisateur normal créé")

        # Default categories
        categories = [
            {"name": "Smartphones", "requires_variants": True},
            {"name": "Ordinateurs portables", "requires_variants": True},
            {"name": "Tablettes", "requires_variants": True},
            {"name": "Accessoires", "requires_variants": False},
            {"name": "Téléphones fixes", "requires_variants": False},
            {"name": "Montres connectées", "requires_variants": True},
        ]
        for cat in categories:
            existing = await Category.find_one(Category.name == cat["name"])
            if not existing:
                new_id = await get_next_id("categories")
                c = Category(
                    category_id=new_id,
                    name=cat["name"],
                    description=f"Catégorie {cat['name']}",
                    requires_variants=cat["requires_variants"],
                )
                await c.insert()
        print("✅ Catégories par défaut créées")

        # Default client
        default_client = await Client.find_one(Client.name == "Client par défaut")
        if not default_client:
            new_id = await get_next_id("clients")
            default_client = Client(
                client_id=new_id,
                name="Client par défaut",
                contact="Contact par défaut",
                email="client@example.com",
                phone="+221 77 123 45 67",
                address="Adresse par défaut",
                city="Dakar",
                country="Sénégal",
            )
            await default_client.insert()
            print("✅ Client par défaut créé")

        print("✅ Base de données initialisée avec succès")

    except Exception as e:
        print(f"❌ Erreur lors de l'initialisation des données: {e}")
        raise
