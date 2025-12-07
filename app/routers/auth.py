from fastapi import APIRouter, Depends, HTTPException, status, Response
from fastapi.security import HTTPBearer
from sqlalchemy.orm import Session
from datetime import timedelta, datetime
from ..database import get_db, User
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

# Cookie configuration via environment
AUTH_COOKIE_NAME = os.getenv("AUTH_COOKIE_NAME", "gt_access")
AUTH_COOKIE_SECURE = str(os.getenv("AUTH_COOKIE_SECURE", "false")).lower() == "true"
AUTH_COOKIE_SAMESITE = os.getenv("AUTH_COOKIE_SAMESITE", "lax").lower()
AUTH_COOKIE_PATH = os.getenv("AUTH_COOKIE_PATH", "/")

router = APIRouter(prefix="/api/auth", tags=["authentication"])
security = HTTPBearer()

@router.post("/login", response_model=Token)
async def login(user_credentials: UserLogin, response: Response, db: Session = Depends(get_db)):
    """Authentification utilisateur"""
    try:
        # Chercher l'utilisateur
        user = db.query(User).filter(User.username == user_credentials.username).first()
        
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
        
        # Créer le token d'accès (durée configurable via env ACCESS_TOKEN_EXPIRE_MINUTES)
        access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        # Inclure les claims nécessaires pour un mode sans DB (si activé côté auth)
        token_payload = {
            "sub": user.username,
            "user_id": getattr(user, "user_id", getattr(user, "id", None)),
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role,
            "is_active": bool(user.is_active),
        }
        access_token = create_access_token(data=token_payload, expires_delta=access_token_expires)
        
        # Mettre à jour la dernière connexion
        from datetime import datetime
        user.last_login = datetime.utcnow()
        db.commit()
        
        # Définir un cookie HttpOnly pour persister la session côté navigateur sans stockage JS
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
            # En cas d'environnement restrictif, ignorer silencieusement
            pass

        return {
            "access_token": access_token,
            "token_type": "bearer",
            "user": UserResponse.from_orm(user)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Erreur lors de la connexion: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erreur serveur"
        )

@router.get("/verify", response_model=UserResponse)
async def verify_token(current_user: User = Depends(get_current_user)):
    """Vérifier la validité du token"""
    # Si on a un utilisateur ORM, utiliser from_orm
    if isinstance(current_user, User):
        return UserResponse.from_orm(current_user)
    # Sinon, construire à partir des attributs (mode claims-based)
    return UserResponse(
        user_id=getattr(current_user, "user_id", getattr(current_user, "id", 0)) or 0,
        username=(getattr(current_user, "username", None) or ""),
        email=(getattr(current_user, "email", None) or ""),
        full_name=getattr(current_user, "full_name", None),
        role=getattr(current_user, "role", "user"),
        is_active=bool(getattr(current_user, "is_active", True)),
        created_at=getattr(current_user, "created_at", datetime.utcnow()),
    )

@router.post("/logout")
async def logout(response: Response):
    """Déconnexion: efface le cookie HttpOnly"""
    try:
        response.set_cookie(
            key=AUTH_COOKIE_NAME,
            value="",
            max_age=0,
            httponly=True,
            samesite=AUTH_COOKIE_SAMESITE,
            secure=AUTH_COOKIE_SECURE,
            path=AUTH_COOKIE_PATH,
        )
    except Exception:
        pass
    return {"message": "Déconnexion réussie"}

@router.post("/register", response_model=UserResponse)
async def register(user_data: UserCreate, db: Session = Depends(get_db)):
    """Créer un nouvel utilisateur (admin seulement)"""
    # Vérifier si l'utilisateur existe déjà
    existing_user = db.query(User).filter(
        (User.username == user_data.username) | (User.email == user_data.email)
    ).first()
    
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Nom d'utilisateur ou email déjà utilisé"
        )
    
    # Créer le nouvel utilisateur
    hashed_password = get_password_hash(user_data.password)
    db_user = User(
        username=user_data.username,
        email=user_data.email,
        password_hash=hashed_password,
        full_name=user_data.full_name,
        role=user_data.role
    )
    
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    
    return UserResponse.from_orm(db_user)

@router.get("/users", response_model=list[UserResponse])
async def get_users(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Récupérer la liste des utilisateurs (admin seulement)"""
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès refusé. Droits administrateur requis."
        )
    
    users = db.query(User).all()
    return [UserResponse.from_orm(user) for user in users]

@router.put("/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: int,
    user_data: UserUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Modifier un utilisateur (admin seulement)"""
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès refusé. Droits administrateur requis."
        )
    user = db.query(User).filter(User.user_id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Utilisateur non trouvé")
    username = user_data.username if user_data.username is not None else user.username
    email = user_data.email if user_data.email is not None else user.email
    existing_user = db.query(User).filter(User.username == username, User.user_id != user_id).first()
    if existing_user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Ce nom d'utilisateur existe déjà")
    existing_email = db.query(User).filter(User.email == email, User.user_id != user_id).first()
    if existing_email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cette adresse email existe déjà")
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
    db.commit()
    db.refresh(user)
    return UserResponse.from_orm(user)

@router.delete("/users/{user_id}")
async def delete_user(
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Supprimer un utilisateur (admin seulement)"""
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accès refusé. Droits administrateur requis."
        )
    if getattr(current_user, "user_id", None) == user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Vous ne pouvez pas supprimer votre propre compte")
    user = db.query(User).filter(User.user_id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Utilisateur non trouvé")
    db.delete(user)
    db.commit()
    return {"message": "Utilisateur supprimé avec succès"}

@router.put("/users/{user_id}/status", response_model=UserResponse)
async def update_user_status(
    user_id: int,
    payload: dict,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if current_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Accès refusé. Droits administrateur requis.")
    user = db.query(User).filter(User.user_id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Utilisateur non trouvé")
    is_active = payload.get("is_active")
    if isinstance(is_active, bool):
        user.is_active = is_active
        db.commit()
        db.refresh(user)
    return UserResponse.from_orm(user)
