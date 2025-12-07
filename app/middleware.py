"""
Middlewares pour améliorer la robustesse de l'application
"""
import asyncio
import logging
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from starlette.responses import Response
import time

logger = logging.getLogger(__name__)

async def error_handling_middleware(request: Request, call_next):
    """
    Middleware pour gérer les erreurs globalement et éviter les plantages
    """
    try:
        response = await call_next(request)
        return response
    except HTTPException as e:
        # Les HTTPException sont déjà gérées par FastAPI
        raise e
    except Exception as e:
        logger.error(f"Erreur non gérée dans {request.url}: {str(e)}")
        
        # Pour les requêtes API, retourner du JSON
        if request.url.path.startswith("/api/"):
            return JSONResponse(
                status_code=500,
                content={"detail": "Erreur interne du serveur"}
            )
        
        # Pour les autres requêtes, retourner une erreur générique
        return Response(
            content="Erreur interne du serveur",
            status_code=500,
            media_type="text/plain"
        )

async def timeout_middleware(request: Request, call_next):
    """
    Middleware pour limiter le temps d'exécution des requêtes
    """
    timeout_seconds = 30  # 30 secondes de timeout
    
    try:
        # Utiliser asyncio.wait_for pour limiter le temps d'exécution
        response = await asyncio.wait_for(
            call_next(request), 
            timeout=timeout_seconds
        )
        return response
    except asyncio.TimeoutError:
        logger.warning(f"Timeout sur {request.url} après {timeout_seconds}s")
        
        # Pour les requêtes API, retourner du JSON
        if request.url.path.startswith("/api/"):
            return JSONResponse(
                status_code=408,
                content={"detail": "Délai d'attente dépassé"}
            )
        
        # Pour les autres requêtes
        return Response(
            content="Délai d'attente dépassé",
            status_code=408,
            media_type="text/plain"
        )
    except Exception as e:
        # Laisser les autres erreurs être gérées par le middleware d'erreur
        raise e
