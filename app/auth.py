from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status, Header, Cookie
from sqlalchemy.orm import Session
from .database import get_db, User
import os
from dotenv import load_dotenv

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-here")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
# Feature flag: trust JWT claims and avoid DB dependency for auth
AUTH_TRUST_JWT_CLAIMS = str(os.getenv("AUTH_TRUST_JWT_CLAIMS", "false")).lower() == "true"

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def verify_token(token: str):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            return None
        return payload
    except JWTError:
        return None

class AuthUser:
    """Lightweight user built from JWT claims when DB-free auth is enabled."""
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

def get_current_user(
    authorization: Optional[str] = Header(None),
    gt_access: Optional[str] = Cookie(None),
    db: Session = Depends(get_db)
):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    # Extraire le token d'abord depuis le cookie HttpOnly "gt_access" (source de vérité),
    # puis éventuellement depuis l'en-tête Authorization si présent et valide.
    token: Optional[str] = None
    if gt_access:
        token = gt_access
    elif authorization and authorization.startswith("Bearer "):
        possible = authorization.split(" ", 1)[1]
        # Ignorer les placeholders hérités comme "cookie-based"
        if possible and possible.lower() != "cookie-based":
            token = possible

    if not token:
        raise credentials_exception

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    # Utiliser les informations du JWT directement sans vérifier la base de données
    # Cela accélère considérablement le chargement des listes (produits, devis, factures)
    # On garde cette méthode rapide par défaut maintenant
    claims = {
        "username": payload.get("sub"),
        "user_id": payload.get("user_id"),
        "email": payload.get("email"),
        "full_name": payload.get("full_name"),
        "role": payload.get("role", "user"),
        "is_active": payload.get("is_active", True),
    }
    # Minimal active check
    if not bool(claims.get("is_active", True)):
        raise credentials_exception
    return AuthUser(**claims)

def require_role(required_role: str):
    def role_checker(current_user: User = Depends(get_current_user)):
        if current_user.role != required_role and current_user.role != "admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions"
            )
        return current_user
    return role_checker

def get_current_active_user(current_user: User = Depends(get_current_user)):
    # Works for both ORM User and AuthUser (claims)
    if not getattr(current_user, "is_active", True):
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user

def require_any_role(roles: list[str]):
    """Authorize if user's role is in roles OR user is admin."""
    def checker(current_user: User = Depends(get_current_user)):
        r = getattr(current_user, "role", "user")
        if r == "admin":
            return current_user
        if r not in set(roles or []):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions"
            )
        return current_user
    return checker
