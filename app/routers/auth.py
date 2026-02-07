from fastapi import APIRouter, Depends, HTTPException, status, Response
from fastapi.security import HTTPBearer
from datetime import timedelta, datetime
from ..database import User, get_next_id
from ..schemas import UserLogin, Token, UserResponse, UserCreate, UserUpdate
from ..auth import (
    verify_password,
    get_password_hash,
    create_access_token,
    get_current_user,
    ACCESS_TOKEN_EXPIRE_MINUTES,
)
import logging
import os
from dotenv import load_dotenv

load_dotenv()

AUTH_COOKIE_NAME = os.getenv("AUTH_COOKIE_NAME", "gt_access")
AUTH_COOKIE_SECURE = str(os.getenv("AUTH_COOKIE_SECURE", "false")).lower() == "true"
AUTH_COOKIE_SAMESITE = os.getenv("AUTH_COOKIE_SAMESITE", "lax").lower()
AUTH_COOKIE_PATH = os.getenv("AUTH_COOKIE_PATH", "/")

router = APIRouter(prefix="/api/auth", tags=["authentication"])
security = HTTPBearer()

@router.post("/login", response_model=Token)
async def login(user_credentials: UserLogin, response: Response):
    try:
        user = await User.find_one(User.username == user_credentials.username)
        if not user or not verify_password(user_credentials.password, user.password_hash):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Nom d'utilisateur ou mot de passe incorrect"
            )
        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Compte utilisateur désactivé"
            )
        access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        token_payload = {
            "sub": user.username,
            "user_id": user.user_id,
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role,
            "is_active": bool(user.is_active),
        }
        access_token = create_access_token(data=token_payload, expires_delta=access_token_expires)
        user.last_login = datetime.utcnow()
        await user.save()
        try:
            response.set_cookie(
                key=AUTH_COOKIE_NAME,
                value=access_token,
                max_age=int(access_token_expires.total_seconds()),
                httponly=True,
                samesite=AUTH_COOKIE_SAMESITE,
                secure=AUTH_COOKIE_SECURE,
                path=AUTH_COOKIE_PATH,
            )
        except Exception:
            pass
        return {
            "access_token": access_token,
            "token_type": "bearer",
            "user": UserResponse(
                user_id=user.user_id,
                username=user.username,
                email=user.email,
                full_name=user.full_name,
                role=user.role,
                is_active=user.is_active,
                created_at=user.created_at,
            )
        }
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Erreur lors de la connexion: {e}")
        raise HTTPException(status_code=500, detail="Erreur serveur")

@router.get("/verify", response_model=UserResponse)
async def verify_token(current_user=Depends(get_current_user)):
    return UserResponse(
        user_id=getattr(current_user, "user_id", 0) or 0,
        username=getattr(current_user, "username", ""),
        email=getattr(current_user, "email", ""),
        full_name=getattr(current_user, "full_name", None),
        role=getattr(current_user, "role", "user"),
        is_active=bool(getattr(current_user, "is_active", True)),
        created_at=getattr(current_user, "created_at", datetime.utcnow()),
    )

@router.post("/logout")
async def logout(response: Response):
    try:
        response.set_cookie(
            key=AUTH_COOKIE_NAME, value="", max_age=0,
            httponly=True, samesite=AUTH_COOKIE_SAMESITE,
            secure=AUTH_COOKIE_SECURE, path=AUTH_COOKIE_PATH,
        )
    except Exception:
        pass
    return {"message": "Déconnexion réussie"}

@router.post("/register", response_model=UserResponse)
async def register(user_data: UserCreate):
    existing = await User.find_one(
        {"$or": [{"username": user_data.username}, {"email": user_data.email}]}
    )
    if existing:
        raise HTTPException(status_code=400, detail="Nom d'utilisateur ou email déjà utilisé")
    new_id = await get_next_id("users")
    hashed_password = get_password_hash(user_data.password)
    db_user = User(
        user_id=new_id,
        username=user_data.username,
        email=user_data.email,
        password_hash=hashed_password,
        full_name=user_data.full_name,
        role=user_data.role,
    )
    await db_user.insert()
    return UserResponse(
        user_id=db_user.user_id, username=db_user.username,
        email=db_user.email, full_name=db_user.full_name,
        role=db_user.role, is_active=db_user.is_active,
        created_at=db_user.created_at,
    )

@router.get("/users", response_model=list[UserResponse])
async def get_users(current_user=Depends(get_current_user)):
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Accès refusé. Droits administrateur requis.")
    users = await User.find(User.username != "owner").to_list()
    return [
        UserResponse(
            user_id=u.user_id, username=u.username, email=u.email,
            full_name=u.full_name, role=u.role, is_active=u.is_active,
            created_at=u.created_at,
        ) for u in users
    ]

@router.put("/users/{user_id}", response_model=UserResponse)
async def update_user(user_id: int, user_data: UserUpdate, current_user=Depends(get_current_user)):
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Accès refusé. Droits administrateur requis.")
    user = await User.find_one(User.user_id == user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur non trouvé")
    username = user_data.username if user_data.username is not None else user.username
    email = user_data.email if user_data.email is not None else user.email
    conflict = await User.find_one({"username": username, "user_id": {"$ne": user_id}})
    if conflict:
        raise HTTPException(status_code=400, detail="Ce nom d'utilisateur existe déjà")
    conflict_email = await User.find_one({"email": email, "user_id": {"$ne": user_id}})
    if conflict_email:
        raise HTTPException(status_code=400, detail="Cette adresse email existe déjà")
    if user_data.username is not None:
        user.username = user_data.username
    if user_data.email is not None:
        user.email = user_data.email
    if user_data.full_name is not None:
        user.full_name = user_data.full_name
    if user_data.role is not None:
        user.role = user_data.role
    if user_data.password:
        user.password_hash = get_password_hash(user_data.password)
    await user.save()
    return UserResponse(
        user_id=user.user_id, username=user.username, email=user.email,
        full_name=user.full_name, role=user.role, is_active=user.is_active,
        created_at=user.created_at,
    )

@router.delete("/users/{user_id}")
async def delete_user(user_id: int, current_user=Depends(get_current_user)):
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Accès refusé. Droits administrateur requis.")
    if getattr(current_user, "user_id", None) == user_id:
        raise HTTPException(status_code=400, detail="Vous ne pouvez pas supprimer votre propre compte")
    user = await User.find_one(User.user_id == user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur non trouvé")
    await user.delete()
    return {"message": "Utilisateur supprimé avec succès"}

@router.put("/users/{user_id}/status", response_model=UserResponse)
async def update_user_status(user_id: int, payload: dict, current_user=Depends(get_current_user)):
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Accès refusé. Droits administrateur requis.")
    user = await User.find_one(User.user_id == user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur non trouvé")
    is_active = payload.get("is_active")
    if isinstance(is_active, bool):
        user.is_active = is_active
        await user.save()
    return UserResponse(
        user_id=user.user_id, username=user.username, email=user.email,
        full_name=user.full_name, role=user.role, is_active=user.is_active,
        created_at=user.created_at,
    )
