import os
import sys
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database import User, _normalize_db_url, _RAW_DATABASE_URL

# Create engine
DATABASE_URL = _normalize_db_url(_RAW_DATABASE_URL)
_is_sqlite = "sqlite" in DATABASE_URL
engine_kwargs = {
    "pool_pre_ping": True,
}
if _is_sqlite:
    engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, **engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Hash password
from passlib.context import CryptContext
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Create session
session = SessionLocal()

try:
    # Check if user exists
    existing_user = session.query(User).filter(User.username == "jarvis").first()
    if existing_user:
        print("✅ User 'jarvis' already exists")
        sys.exit(0)
    
    # Create new admin user (hidden)
    hashed_password = pwd_context.hash("admin123")
    new_user = User(
        username="jarvis",
        email="jarvis@admin.local",
        password_hash=hashed_password,
        full_name="Jarvis Admin",
        role="admin",
        is_active=True
    )
    
    session.add(new_user)
    session.commit()
    print("✅ Admin user 'jarvis' créé avec succès!")
    print(f"   Username: jarvis")
    print(f"   Password: admin123")
    print(f"   Role: admin")
    print(f"   Email: jarvis@admin.local (caché)")
    
except Exception as e:
    session.rollback()
    print(f"❌ Erreur: {e}")
    sys.exit(1)
finally:
    session.close()

