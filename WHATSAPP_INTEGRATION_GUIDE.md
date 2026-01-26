# Guide de Réplication - Intégration WhatsApp avec QR Code

Ce document décrit comment répliquer l'intégration WhatsApp avec système de connexion/déconnexion et actualisation du QR code dans une autre application.

---

## Architecture Globale

```
┌─────────────────────────────────────────────────────────────┐
│                        Navigateur                             │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  Page WhatsApp (/whatsapp)                            │  │
│  │  - Affiche QR code                                    │  │
│  │  - Polling status (toutes les 2.5s)                   │  │
│  │  - Boutons Rafraîchir/Vérifier                        │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                              ↓
                              ↓ fetch()
                              ↓
┌─────────────────────────────────────────────────────────────┐
│                    Application Main (FastAPI)                │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  Routes Proxy                                         │  │
│  │  - GET /whatsapp → Template whatsapp.html            │  │
│  │  - GET /api/whatsapp/status → Proxy → whatsapp:3001 │  │
│  │  - GET /api/whatsapp/qr → Proxy → whatsapp:3001    │  │
│  │  - GET /api/status → Legacy endpoint                │  │
│  │  - GET /api/qr → Legacy endpoint                     │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                              ↓
                              ↓ httpx.AsyncClient()
                              ↓
┌─────────────────────────────────────────────────────────────┐
│              WhatsApp Service (Node.js + whatsapp-web.js)    │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  API Endpoints                                        │  │
│  │  - GET /api/status → {status, qrCode}                 │  │
│  │  - GET /api/qr → {qr} ou {status}                     │  │
│  │  - POST /api/sendText → Envoyer message                │  │
│  │  - POST /api/sendFile → Envoyer fichier                │  │
│  │  - POST /api/sendImage → Envoyer image                 │  │
│  │  - POST /api/sendPdf → Générer et envoyer PDF         │  │
│  └───────────────────────────────────────────────────────┘  │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  WhatsApp Client (whatsapp-web.js)                    │  │
│  │  - Événements: qr, ready, authenticated, disconnected │  │
│  │  - Stocke QR dans qrCodeData                          │  │
│  │  - Stocke statut dans clientReady                      │  │
│  │  - Session persistante dans ./whatsapp-session       │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                              ↓
                              ↓
                         WhatsApp Web
```

---

## Composant 1 : WhatsApp Service (Node.js)

### Fichier : `whatsapp-service/index.js`

```javascript
const { Client, LocalAuth, MessageMedia } = require('whatsapp-web.js');
const express = require('express');
const qrcode = require('qrcode-terminal');
const axios = require('axios');

const app = express();
const PORT = 3001;

// État du client
let clientReady = false;
let qrCodeData = null;

// Initialiser le client WhatsApp
const client = new Client({
    authStrategy: new LocalAuth({
        dataPath: './whatsapp-session'
    }),
    puppeteer: {
        headless: true,
        args: [
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-dev-shm-usage',
            '--disable-gpu'
        ]
    }
});

// Événements WhatsApp
client.on('qr', (qr) => {
    console.log('QR Code reçu, scannez-le avec WhatsApp:');
    qrcode.generate(qr, { small: true });
    qrCodeData = qr; // Stocker le QR pour l'API
});

client.on('ready', () => {
    console.log('✅ WhatsApp client prêt!');
    clientReady = true;
    qrCodeData = null;
});

client.on('authenticated', () => {
    console.log('✅ Authentification réussie!');
});

client.on('auth_failure', (msg) => {
    console.error('❌ Échec d\'authentification:', msg);
    clientReady = false;
});

client.on('disconnected', (reason) => {
    console.log('❌ Déconnecté:', reason);
    clientReady = false;
});

// Nettoyer les locks Chromium avant démarrage
function _cleanupChromiumSingletonLocks(rootDir) {
    // ... code de nettoyage
}

// Démarrer le client
_cleanupChromiumSingletonLocks(path.resolve('./whatsapp-session'));
client.initialize();

// API Endpoints

// GET /api/status
app.get('/api/status', (req, res) => {
    res.json({
        status: clientReady ? 'ready' : 'not_ready',
        qrCode: qrCodeData ? true : false
    });
});

// GET /api/qr
app.get('/api/qr', (req, res) => {
    if (clientReady) {
        return res.json({ status: 'already_connected' });
    }
    if (qrCodeData) {
        return res.json({ qr: qrCodeData });
    }
    res.json({ status: 'waiting_for_qr' });
});

// POST /api/sendText
app.post('/api/sendText', async (req, res) => {
    const { phone, text } = req.body;
    
    if (!clientReady) {
        return res.status(503).json({ error: 'WhatsApp non connecté' });
    }
    
    // Formater le numéro
    let chatId = phone.replace(/\+/g, '').replace(/ /g, '').replace(/-/g, '');
    if (!chatId.endsWith('@c.us')) {
        chatId = chatId + '@c.us';
    }
    
    const result = await client.sendMessage(chatId, text, { sendSeen: false });
    res.json({ success: true, messageId: result.id._serialized });
});

// POST /api/sendFile
app.post('/api/sendFile', async (req, res) => {
    const { phone, fileUrl, filename, caption } = req.body;
    
    if (!clientReady) {
        return res.status(503).json({ error: 'WhatsApp non connecté' });
    }
    
    // Télécharger le fichier
    const response = await axios.get(fileUrl, { 
        responseType: 'arraybuffer',
        timeout: 30000
    });
    
    const base64Data = Buffer.from(response.data).toString('base64');
    const mimeType = response.headers['content-type'] || 'application/octet-stream';
    
    // Nettoyer le base64
    const base64Clean = _normalizeBase64(base64Data);
    
    const media = new MessageMedia(mimeType, base64Clean, filename || 'document');
    
    let chatId = phone.replace(/\+/g, '').replace(/ /g, '').replace(/-/g, '');
    if (!chatId.endsWith('@c.us')) {
        chatId = chatId + '@c.us';
    }
    
    const result = await client.sendMessage(chatId, media, { 
        caption: caption || '', 
        sendSeen: false, 
        sendMediaAsDocument: true 
    });
    
    res.json({ success: true, messageId: result.id._serialized });
});

// Démarrer le serveur
app.listen(PORT, '0.0.0.0', () => {
    console.log(`🚀 Service WhatsApp démarré sur le port ${PORT}`);
});
```

### Fichier : `whatsapp-service/package.json`

```json
{
  "name": "whatsapp-service",
  "version": "1.0.0",
  "main": "index.js",
  "dependencies": {
    "whatsapp-web.js": "^1.23.0",
    "express": "^4.18.2",
    "axios": "^1.6.0",
    "qrcode-terminal": "^0.12.0",
    "puppeteer": "^21.0.0"
  }
}
```

### Fichier : `whatsapp-service/Dockerfile`

```dockerfile
FROM node:18-slim

# Installer les dépendances pour Puppeteer
RUN apt-get update && apt-get install -y \
    chromium \
    fonts-liberation \
    libappindicator3-1 \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libgbm1 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    xdg-utils \
    dbus \
    --no-install-recommends \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /run/dbus

# Variables d'environnement pour Puppeteer
ENV PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true
ENV PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium

WORKDIR /app

# Copier les fichiers
COPY package.json ./
RUN npm install

COPY index.js ./

# Créer le dossier pour la session
RUN mkdir -p /app/whatsapp-session

EXPOSE 3001

CMD ["node", "index.js"]
```

---

## Composant 2 : Application Main (FastAPI)

### Fichier : `main.py` - Routes Proxy

```python
import os
import httpx
from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse

# ===== Page WhatsApp =====
@app.get("/whatsapp", response_class=HTMLResponse)
async def whatsapp_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse("whatsapp.html", {
        "request": request, 
        "global_settings": _load_company_settings(db),
    })

# ===== Proxy Routes vers WhatsApp Service =====

# GET /api/whatsapp/qr
@app.get("/api/whatsapp/qr")
async def whatsapp_qr_proxy():
    base_url = os.getenv("WHATSAPP_SERVICE_URL", "http://whatsapp:3001")
    url = f"{base_url.rstrip('/')}/api/qr"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(url)
            r.raise_for_status()
            return JSONResponse(content=r.json())
    except Exception:
        return JSONResponse(status_code=503, content={"status": "error"})

# GET /api/whatsapp/status
@app.get("/api/whatsapp/status")
async def whatsapp_status_proxy():
    base_url = os.getenv("WHATSAPP_SERVICE_URL", "http://whatsapp:3001")
    url = f"{base_url.rstrip('/')}/api/status"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(url)
            r.raise_for_status()
            return JSONResponse(content=r.json())
    except Exception:
        return JSONResponse(status_code=503, content={"status": "not_ready", "qrCode": False})

# ===== Legacy Endpoints (compatibilité) =====
@app.get("/api/status")
async def whatsapp_status_legacy():
    return await whatsapp_status_proxy()

@app.get("/api/qr")
async def whatsapp_qr_legacy():
    return await whatsapp_qr_proxy()
```

### Middleware pour CSP (Content Security Policy)

```python
@app.middleware("http")
async def cache_headers_middleware(request, call_next):
    response = await call_next(request)
    try:
        path = request.url.path or ""
        content_type = (response.headers.get("content-type", "") or "").lower()
        
        # Cache headers
        if path.startswith("/static/") or path == "/favicon.ico":
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif content_type.startswith("text/html"):
            response.headers["Cache-Control"] = "no-store"
        
        # Security headers (uniquement en production)
        if not (request.client.host in ["127.0.0.1", "localhost"] or 
                request.headers.get("host", "").startswith(("localhost:", "127.0.0.1:"))):
            response.headers["Content-Security-Policy"] = (
                "upgrade-insecure-requests; "
                "frame-ancestors *; "
                "frame-src *; "
                "script-src * 'unsafe-inline' 'unsafe-eval'; "
                "style-src * 'unsafe-inline'"
            )
            response.headers.setdefault("Strict-Transport-Security", 
                "max-age=31536000; includeSubDomains; preload")
        
        # Désactiver X-Frame-Options
        if "X-Frame-Options" in response.headers:
            del response.headers["X-Frame-Options"]
    except Exception:
        pass
    return response
```

---

## Composant 3 : Frontend Template

### Fichier : `templates/whatsapp.html`

```html
{% extends "base.html" %}

{% block title %}WhatsApp - Connexion{% endblock %}

{% block extra_head %}
<script src="https://cdn.jsdelivr.net/npm/qrcode@1.5.4/build/qrcode.min.js"></script>
<style>
  .whatsapp-frame-wrap {
    height: calc(100vh - 140px);
    min-height: 560px;
    border: 1px solid rgba(0,0,0,0.1);
    border-radius: 12px;
    overflow: hidden;
    background: #f8f9fa;
  }
</style>
{% endblock %}

{% block content %}
<div class="container-fluid">
  <div class="row mb-3">
    <div class="col-12">
      <div class="d-flex justify-content-between align-items-center">
        <div>
          <h1 class="h3 mb-1">
            <i class="bi bi-whatsapp me-2 text-success"></i>
            WhatsApp
          </h1>
          <p class="text-muted mb-0">Scanner le QR code si déconnecté, sinon voir le statut connecté.</p>
        </div>
        <div>
          <span class="badge bg-secondary" id="waStatusBadge">Chargement...</span>
        </div>
      </div>
    </div>
  </div>

  <div class="row">
    <div class="col-12">
      <div class="whatsapp-frame-wrap">
        <div id="qrContainer" style="width:100%;height:100%;display:flex;align-items:center;justify-content:center;">
          <div>
            <div id="qrCanvas"></div>
            <div class="text-center mt-2">
              <button id="btnRefresh" class="btn btn-sm btn-primary">Rafraîchir QR</button>
              <button id="btnCheck" class="btn btn-sm btn-outline-secondary ms-1">Vérifier statut</button>
            </div>
          </div>
        </div>
      </div>
      <div class="text-muted mt-2" id="waHint" style="display:none;"></div>
    </div>
  </div>
</div>

<script>
  document.addEventListener('DOMContentLoaded', function() {
    const badge = document.getElementById('waStatusBadge');
    const qrCanvas = document.getElementById('qrCanvas');
    const btnRefresh = document.getElementById('btnRefresh');
    const btnCheck = document.getElementById('btnCheck');
    const hint = document.getElementById('waHint');
    let lastQr = null;
    let isRendering = false;
    let isPolling = false;

    function setBadge(text, cls) {
      badge.className = 'badge ' + cls;
      badge.textContent = text;
    }

    async function renderQr(qrString) {
      if (!qrString) return;
      if (isRendering) return;
      isRendering = true;
      try {
        if (qrString !== lastQr) {
          lastQr = qrString;
          qrCanvas.innerHTML = '<div class="text-muted">Génération du QR...</div>';
          
          const img = document.createElement('img');
          img.alt = 'QR Code WhatsApp';
          img.style.maxWidth = '100%';
          img.style.height = 'auto';
          
          // Vérifier si QRCode est disponible
          if (typeof QRCode !== 'undefined' && QRCode.toDataURL) {
            try {
              const dataUrl = await QRCode.toDataURL(qrString, { 
                width: 280, 
                margin: 1, 
                errorCorrectionLevel: 'M' 
              });
              img.src = dataUrl;
            } catch (e) {
              console.warn('QRCode.toDataURL failed, using fallback', e);
              // Fallback vers API externe
              img.src = 'https://api.qrserver.com/v1/create-qr-code/?size=280x280&data=' + encodeURIComponent(qrString);
            }
          } else {
            // QRCode non disponible, utiliser API externe directement
            console.warn('QRCode library not loaded, using external API');
            img.src = 'https://api.qrserver.com/v1/create-qr-code/?size=280x280&data=' + encodeURIComponent(qrString);
          }
          
          qrCanvas.innerHTML = '';
          qrCanvas.appendChild(img);
        }
      } catch (e) {
        console.error('QR render error', e);
        qrCanvas.innerHTML = '<div class="text-danger">Erreur de génération du QR</div>';
      } finally {
        isRendering = false;
      }
    }

    async function fetchJson(url) {
      const res = await fetch(url, { cache: 'no-store' });
      const data = await res.json().catch(() => ({}));
      return { ok: res.ok, status: res.status, data };
    }

    async function pollStatus() {
      if (isPolling) return;
      isPolling = true;
      try {
        const { ok, data } = await fetchJson('/api/whatsapp/status');
        if (!ok) throw new Error('status not ok');
        if (data.status === 'ready') {
          setBadge('Connecté', 'bg-success');
          hint.style.display = 'none';
        } else {
          setBadge('Déconnecté', 'bg-warning text-dark');
          // Demander un QR si nécessaire
          const qrResp = await fetchJson('/api/whatsapp/qr');
          if (qrResp.data && qrResp.data.qr) {
            await renderQr(qrResp.data.qr);
            hint.style.display = '';
            hint.textContent = 'Scanne le QR code avec WhatsApp (Paramètres → Appareils liés)';
          } else if (qrResp.data && qrResp.data.status === 'already_connected') {
            qrCanvas.innerHTML = '<div class="text-center fs-2">✅</div>';
            hint.style.display = 'none';
          } else {
            hint.style.display = '';
            hint.textContent = 'QR non disponible. Réessaie dans quelques secondes…';
          }
        }
      } catch (e) {
        setBadge('Indisponible', 'bg-danger');
      } finally { 
        isPolling = false; 
      }
    }

    // Boutons
    if (btnRefresh) btnRefresh.addEventListener('click', pollStatus);
    if (btnCheck) btnCheck.addEventListener('click', pollStatus);

    // Démarrer
    pollStatus();
    setInterval(pollStatus, 2500);
  });
</script>
{% endblock %}
```

---

## Composant 4 : Configuration Docker

### Fichier : `docker-compose.yml`

```yaml
version: '3.8'

services:
  app:
    build: .
    container_name: nitek_app
    ports:
      - "8000:8000"
    environment:
      - WHATSAPP_SERVICE_URL=http://whatsapp:3001
      - WHATSAPP_SERVICE_PUBLIC_URL=https://yourdomain.com:3001
    depends_on:
      - whatsapp

  whatsapp:
    build:
      context: ./whatsapp-service
      dockerfile: Dockerfile
    container_name: whatsapp_service
    ports:
      - "0.0.0.0:3001:3001"
    volumes:
      - ./whatsapp-service/whatsapp-session:/app/whatsapp-session
    restart: unless-stopped
    environment:
      PORT: "3001"
```

### Variables d'environnement

```bash
# .env
WHATSAPP_SERVICE_URL=http://whatsapp:3001
WHATSAPP_SERVICE_PUBLIC_URL=https://yourdomain.com:3001
APP_PUBLIC_URL=https://yourdomain.com
```

---

## Flux de Connexion

### 1. Initialisation du Service WhatsApp

```
1. Le service Node.js démarre
2. whatsapp-web.js initialise le client
3. Événement 'qr' déclenché → QR généré et stocké dans qrCodeData
4. API /api/qr retourne { qr: "..." }
5. API /api/status retourne { status: "not_ready", qrCode: true }
```

### 2. Affichage du QR dans le Navigateur

```
1. Page /whatsapp chargée
2. JavaScript appelle /api/whatsapp/status
3. Si status != "ready", appelle /api/whatsapp/qr
4. QR reçu → Affiché avec QRCode.toDataURL() ou API externe
5. Polling toutes les 2.5 secondes
```

### 3. Connexion de l'Utilisateur

```
1. Utilisateur scanne le QR avec WhatsApp
2. whatsapp-web.js détecte la connexion
3. Événement 'ready' déclenché
4. clientReady = true, qrCodeData = null
5. API /api/status retourne { status: "ready", qrCode: false }
6. Frontend affiche "Connecté"
```

### 4. Déconnexion

```
1. Utilisateur déconnecte depuis WhatsApp (Paramètres → Appareils liés)
2. Événement 'disconnected' déclenché
3. clientReady = false
4. Nouveau QR généré automatiquement
5. Frontend affiche le nouveau QR
```

---

## Étapes de Réplication

### Étape 1 : Créer le Service WhatsApp

1. Créer le dossier `whatsapp-service/`
2. Créer `package.json` avec les dépendances
3. Créer `index.js` avec le code du service
4. Créer `Dockerfile`

### Étape 2 : Intégrer dans l'Application Main

1. Ajouter les routes proxy dans `main.py`
2. Configurer le middleware CSP
3. Créer le template `templates/whatsapp.html`

### Étape 3 : Configurer Docker

1. Ajouter le service `whatsapp` dans `docker-compose.yml`
2. Configurer les variables d'environnement
3. Ajouter le volume pour la session

### Étape 4 : Tester

```bash
# Construire et démarrer
docker compose up -d --build

# Vérifier les logs
docker compose logs -f whatsapp

# Accéder à la page
# http://localhost:8000/whatsapp
```

---

## Points Clés à Retenir

1. **Architecture Proxy** : L'application main fait un proxy vers le service WhatsApp pour éviter les problèmes de port et de mixed content.

2. **Polling Frontend** : Le frontend poll le statut toutes les 2.5 secondes pour mettre à jour l'UI en temps réel.

3. **Fallback QR** : Le frontend a un fallback vers `api.qrserver.com` si la bibliothèque QRCode échoue.

4. **Session Persistante** : La session WhatsApp est stockée dans `./whatsapp-session` et persistée via un volume Docker.

5. **Nettoyage Chromium** : Le service nettoie les locks Chromium avant de démarrer pour éviter les problèmes de redémarrage.

6. **Legacy Endpoints** : Les endpoints `/api/status` et `/api/qr` sont maintenus pour la compatibilité.

7. **Middleware CSP** : Le middleware relaxe les politiques CSP pour permettre l'affichage du QR.

8. **Limitations WhatsApp** : WhatsApp bloque les tentatives de connexion trop fréquentes (anti-abus).

---

## API Endpoints Disponibles

### WhatsApp Service (Port 3001)

| Méthode | Endpoint | Description |
|---------|----------|-------------|
| GET | `/api/status` | Statut du service (ready/not_ready) |
| GET | `/api/qr` | QR code actuel |
| POST | `/api/sendText` | Envoyer un message texte |
| POST | `/api/sendFile` | Envoyer un fichier |
| POST | `/api/sendImage` | Envoyer une image |
| POST | `/api/sendPdf` | Générer et envoyer un PDF |

### Application Main (Port 8000)

| Méthode | Endpoint | Description |
|---------|----------|-------------|
| GET | `/whatsapp` | Page de connexion WhatsApp |
| GET | `/api/whatsapp/status` | Proxy statut WhatsApp |
| GET | `/api/whatsapp/qr` | Proxy QR WhatsApp |
| GET | `/api/status` | Legacy endpoint |
| GET | `/api/qr` | Legacy endpoint |

---

## Exemple d'Utilisation

### Envoyer un message depuis l'application

```python
import httpx

async def send_whatsapp_message(phone: str, text: str):
    """Envoyer un message WhatsApp"""
    whatsapp_url = os.getenv("WHATSAPP_SERVICE_URL", "http://whatsapp:3001")
    
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{whatsapp_url}/api/sendText",
            json={"phone": phone, "text": text}
        )
        return response.json()
```

### Vérifier le statut

```python
async def get_whatsapp_status():
    """Vérifier le statut WhatsApp"""
    whatsapp_url = os.getenv("WHATSAPP_SERVICE_URL", "http://whatsapp:3001")
    
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{whatsapp_url}/api/status")
        return response.json()
```

---

## Dépannage

### Le QR ne s'affiche pas

1. Vérifier que le service WhatsApp est démarré : `docker compose logs whatsapp`
2. Vérifier que l'API répond : `curl http://localhost:3001/api/status`
3. Vider le cache du navigateur : Ctrl+F5
4. Vérifier la console du navigateur pour les erreurs

### WhatsApp refuse la connexion

1. Attendre quelques heures (limitation anti-abus de WhatsApp)
2. Nettoyer les appareils liés dans WhatsApp
3. Redémarrer le service : `docker compose restart whatsapp`

### Erreur "Chromium not found"

1. Vérifier que Chromium est installé dans le Dockerfile
2. Vérifier `PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium`
3. Rebuilder le conteneur : `docker compose up -d --build whatsapp`

---

## Conclusion

Cette architecture permet d'intégrer WhatsApp dans n'importe quelle application avec :

- **Connexion via QR code** : Simple et sécurisé
- **Statut en temps réel** : Polling toutes les 2.5 secondes
- **API REST** : Facile à intégrer
- **Déconnexion automatique** : Géré par whatsapp-web.js
- **Session persistante** : Stockée dans un volume Docker
- **Fallback robuste** : API externe si bibliothèque échoue

Pour répliquer dans une autre application, suivez les étapes ci-dessus et adaptez les routes selon votre framework.
