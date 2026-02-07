#!/usr/bin/env python3
"""Create admin user in MongoDB (Beanie)."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from motor.motor_asyncio import AsyncIOMotorClient
from beanie import init_beanie
from passlib.context import CryptContext

from app.database import DATABASE_URL, User, ALL_DOCUMENT_MODELS

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


async def create_admin():
    # Connect to MongoDB
    client = AsyncIOMotorClient(DATABASE_URL)
    db_name = DATABASE_URL.rsplit("/", 1)[-1].split("?")[0] or "geektech"
    database = client[db_name]
    await init_beanie(database=database, document_models=ALL_DOCUMENT_MODELS)

    # Check if user exists
    existing = await User.find_one(User.username == "jarvis")
    if existing:
        print("User 'jarvis' already exists")
        return

    # Create admin
    hashed_password = pwd_context.hash("admin123")
    new_user = User(
        user_id=1,
        username="jarvis",
        email="jarvis@admin.local",
        password_hash=hashed_password,
        full_name="Jarvis Admin",
        role="admin",
        is_active=True,
    )
    await new_user.insert()
    print("Admin user 'jarvis' created successfully")
    print("  Username: jarvis")
    print("  Password: admin123")
    print("  Role: admin")


if __name__ == "__main__":
    asyncio.run(create_admin())
