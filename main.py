from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, FileResponse, Response, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from io import BytesIO
import uvicorn
import os
from dotenv import load_dotenv
import json
import re
from datetime import date, datetime
import httpx

# Charger les variables d'environnement
load_dotenv()

# Version d'assets pour bust de cache (commit SHA si fourni par la plateforme, sinon variable ou timestamp)
def get_asset_version():
    """Génère une version basée sur le timestamp de modification des fichiers statiques"""
    # En production, utiliser le commit SHA si disponible
    commit_sha = os.getenv("GIT_COMMIT_SHA") or os.getenv("KOYEB_COMMIT_SHA") or os.getenv("ASSET_VERSION")
    if commit_sha:
        return commit_sha[:12]

    # En développement, utiliser le timestamp de modification le plus récent parmi static/js, static/css et templates
    try:
        latest_mtime = 0
        for rel, exts in [(os.path.join("static", "js"), (".js",)),
                          (os.path.join("static", "css"), (".css",)),
                          ("templates", (".html",))]:
            if os.path.exists(rel):
                for fn in os.listdir(rel):
                    if fn.endswith(exts):
                        fp = os.path.join(rel, fn)
                        try:
                            m = os.path.getmtime(fp)
                            if m > latest_mtime:
                                latest_mtime = m
                        except Exception:
                            pass
        if latest_mtime > 0:
            return str(int(latest_mtime))
    except Exception:
        pass

    # Fallback: timestamp actuel
    return str(int(datetime.now().timestamp()))

ASSET_VERSION = get_asset_version()

# Imports de l'application — MongoDB / Beanie
from app.database import (
    init_db,
    Invoice, InvoiceItem, InvoicePayment,
    UserSettings, Product, DeliveryNote, DeliveryNoteItem,
    Client, ClientDebt,
    Quotation, QuotationItem,
)
from app.routers import (
    auth, products, clients, stock_movements, invoices, quotations,
    suppliers, debts, delivery_notes, bank_transactions, reports,
    user_settings, migrations, cache, dashboard, supplier_invoices,
    daily_recap, daily_purchases, daily_requests, daily_sales,
    google_sheets, client_debts, backup, maintenances,
)
from app.init_db import init_database
from app.auth import get_current_user
from app.services.migration_processor import migration_processor
try:
    from app.services.debt_notifier import debt_notifier
except Exception:
    debt_notifier = None  # type: ignore
try:
    from app.services.warranty_notifier import warranty_notifier
except Exception:
    warranty_notifier = None  # type: ignore
try:
    from app.services.maintenance_notifier import maintenance_notifier
except Exception:
    maintenance_notifier = None  # type: ignore

# Créer l'application FastAPI
app = FastAPI(
    title="Geek Technologie - Gestion de Stock",
    description="Application de gestion de stock et facturation avec FastAPI et Bootstrap",
    version="1.0.0"
)

# Configuration CORS pour la boutique en ligne (domaine séparé)
# Ajouter votre domaine de boutique dans STORE_DOMAIN
STORE_DOMAIN = os.getenv("STORE_DOMAIN", "http://localhost:3000")
ALLOWED_ORIGINS = [
    STORE_DOMAIN,
    "http://localhost:3000",
    "http://localhost:3001",
    "https://boutique.votredomaine.com",  # Remplacer par votre domaine
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Middleware de gestion du cache: HTML non cache, assets statiques fortement cacheés
@app.middleware("http")
async def cache_headers_middleware(request, call_next):
    response = await call_next(request)
    try:
        path = request.url.path or ""
        content_type = (response.headers.get("content-type", "") or "").lower()
        if path.startswith("/static/") or path == "/favicon.ico":
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif content_type.startswith("text/html"):
            response.headers["Cache-Control"] = "no-store"
        # Help browsers auto-upgrade any stray http resources to https and enable HSTS
        # Only apply security headers in production (not on localhost)
        if not (path.startswith("/") and (request.client.host in ["127.0.0.1", "localhost"] or
                request.headers.get("host", "").startswith(("localhost:", "127.0.0.1:")))):
            # Autoriser frame-ancestors '*' pour le diagnostic et s'assurer que l'iframe peut charger des scripts externes
            response.headers["Content-Security-Policy"] = "upgrade-insecure-requests; frame-ancestors *; frame-src *; script-src * 'unsafe-inline' 'unsafe-eval'; style-src * 'unsafe-inline'"
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains; preload")

        # Désactiver temporairement X-Frame-Options pour lever tout blocage résiduel
        if "X-Frame-Options" in response.headers:
            del response.headers["X-Frame-Options"]
    except Exception:
        # En cas de souci, on n'empêche pas la réponse de sortir
        pass
    return response

# Initialiser la base de données au démarrage
@app.on_event("startup")
async def startup_event():
    try:
        # Initialise MongoDB/Beanie connection + optional seeding
        should_init = os.getenv("INIT_DB_ON_STARTUP", "false").lower() == "true"
        if should_init:
            print("⚙️ INIT_DB_ON_STARTUP=true → initialisation de la base autorisée")
            await init_database()
        else:
            # Always connect to MongoDB, just skip seeding
            await init_db()
            print("⏭️ INIT_DB_ON_STARTUP!=true → saut du semis de données")

        # Démarrer le processeur de migrations en arrière-plan (désactivé par défaut)
        if os.getenv("ENABLE_MIGRATIONS_WORKER", "false").lower() == "true":
            migration_processor.start_background_processor()
        else:
            print("⏭️ ENABLE_MIGRATIONS_WORKER!=true → worker migrations non démarré")
        # Démarrer le notificateur de créances en retard si activé
        if os.getenv("ENABLE_DEBT_REMINDERS", "false").lower() == "true":
            if debt_notifier is not None:
                debt_notifier.start_background()
                print("✅ Notificateur de créances démarré")
        # Démarrer le notificateur de garanties si activé
        if os.getenv("ENABLE_WARRANTY_REMINDERS", "false").lower() == "true":
            if warranty_notifier is not None:
                warranty_notifier.start_background()
                print("✅ Notificateur de garanties démarré")
        # Démarrer le notificateur de maintenances si activé
        if os.getenv("ENABLE_MAINTENANCE_REMINDERS", "false").lower() == "true":
            if maintenance_notifier is not None:
                maintenance_notifier.start_background()
                print("✅ Notificateur de maintenances démarré")
        print("✅ Application démarrée avec succès")
    except Exception as e:
        print(f"❌ Erreur lors du démarrage: {e}")

# Arrêter le processeur au shutdown
@app.on_event("shutdown")
async def shutdown_event():
    try:
        # Arrêter uniquement si le worker était activé
        if os.getenv("ENABLE_MIGRATIONS_WORKER", "false").lower() == "true":
            migration_processor.stop_background_processor()
        if os.getenv("ENABLE_DEBT_REMINDERS", "false").lower() == "true" and debt_notifier is not None:
            debt_notifier.stop_background()
        if os.getenv("ENABLE_WARRANTY_REMINDERS", "false").lower() == "true" and warranty_notifier is not None:
            warranty_notifier.stop_background()
        if os.getenv("ENABLE_MAINTENANCE_REMINDERS", "false").lower() == "true" and maintenance_notifier is not None:
            maintenance_notifier.stop_background()
        print("✅ Application arrêtée proprement")
    except Exception as e:
        print(f"❌ Erreur lors de l'arrêt: {e}")

# Configuration des templates et fichiers statiques
templates = Jinja2Templates(directory="templates")
# Exposer une fonction globale de version pour le cache-busting des assets (dynamique)
templates.env.globals["ASSET_VERSION"] = get_asset_version

# ---- Jinja filters ----
def _format_number(value) -> str:
    try:
        # Support Decimal, int, float; round to 0 decimals for CFA display
        n = float(value or 0)
        text = f"{n:,.0f}"
        # Replace commas with spaces for French-style grouping
        return text.replace(",", " ")
    except Exception:
        try:
            return str(int(value))
        except Exception:
            return str(value or 0)

templates.env.filters["format_number"] = _format_number

def _format_cfa(value) -> str:
    return f"{_format_number(value)} F CFA"

templates.env.filters["format_cfa"] = _format_cfa

def _format_date_no_time(value) -> str:
    try:
        if value is None:
            return ""
        if isinstance(value, (datetime, date)):
            # Always YYYY-MM-DD
            return value.strftime("%Y-%m-%d")
        s = str(value)
        if "T" in s:
            return s.split("T")[0]
        if " " in s:
            return s.split(" ")[0]
        return s
    except Exception:
        try:
            return str(value).split(" ")[0]
        except Exception:
            return str(value or "")

templates.env.filters["format_date"] = _format_date_no_time

def _normalize_logo(logo_value: str | None) -> str | None:
    try:
        if not logo_value:
            return None
        s = str(logo_value).strip()
        if not s:
            return None
        # Already a proper URL or data URI
        if s.startswith("data:image") or s.startswith("http://") or s.startswith("https://") or s.startswith("/"):
            return s
        # Heuristic: base64 without header → wrap as PNG by default
        if len(s) > 64:
            return f"data:image/png;base64,{s}"
        return s
    except Exception:
        return logo_value
app.mount("/static", StaticFiles(directory="static"), name="static")

# Inclure les routers API de l'application de gestion
app.include_router(auth.router)
app.include_router(products.router)
app.include_router(clients.router)
app.include_router(stock_movements.router)
app.include_router(invoices.router)
app.include_router(quotations.router)
app.include_router(suppliers.router)
app.include_router(supplier_invoices.router)
app.include_router(debts.router)
app.include_router(client_debts.router)
app.include_router(backup.router)
# Désactivation de la page Bons de Livraison
app.include_router(bank_transactions.router)
app.include_router(reports.router)
app.include_router(user_settings.router)
app.include_router(migrations.router)
app.include_router(cache.router)
app.include_router(dashboard.router)
app.include_router(daily_recap.router)
app.include_router(daily_purchases.router)
app.include_router(daily_requests.router)
app.include_router(daily_sales.router)
app.include_router(google_sheets.router)
app.include_router(maintenances.router)

# Route pour le favicon
@app.get("/favicon.ico")
async def favicon():
    return FileResponse("static/favicon.ico")

# Route API de test
@app.get("/api")
async def api_status():
    return {
        "message": "API Geek Technologie",
        "status": "running",
        "version": "1.0.0",
        "framework": "FastAPI"
    }

# Endpoint de version pour live-reload
@app.get("/__live/version")
async def live_version():
    return {"v": get_asset_version()}

# Routes pour l'interface web
# Page d'accueil: Dashboard classique avec barre de navigation
@app.get("/", response_class=HTMLResponse)
async def dashboard_home(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request, "global_settings": await _load_company_settings()})

# Interface Desktop accessible via /desktop (interface avec fenêtres type macOS)
@app.get("/desktop", response_class=HTMLResponse)
async def desktop_page(request: Request):
    return templates.TemplateResponse("desktop.html", {"request": request, "global_settings": await _load_company_settings()})

# Alias /dashboard pour compatibilité
@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_alias(request: Request):
    return templates.TemplateResponse("dashboard.html", {"request": request, "global_settings": await _load_company_settings()})

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "global_settings": await _load_company_settings()})

@app.get("/products", response_class=HTMLResponse)
async def products_page(request: Request):
    return templates.TemplateResponse("products.html", {"request": request, "global_settings": await _load_company_settings()})

@app.get("/clients", response_class=HTMLResponse)
async def clients_page(request: Request):
    return templates.TemplateResponse("clients.html", {"request": request, "global_settings": await _load_company_settings()})

@app.get("/clients/detail", response_class=HTMLResponse)
async def client_detail_page(request: Request):
    return templates.TemplateResponse("clients_detail.html", {"request": request, "global_settings": await _load_company_settings()})

@app.get("/clients/debts", response_class=HTMLResponse)
async def client_debts_page(request: Request):
    return templates.TemplateResponse("client_debts.html", {"request": request, "global_settings": await _load_company_settings()})

@app.get("/clients/debts/print/{client_id}", response_class=HTMLResponse)
async def client_debts_print_page(request: Request, client_id: int):
    cl = await Client.find_one(Client.client_id == client_id)
    if not cl:
        raise HTTPException(status_code=404, detail="Client non trouvé")
    today = date.today()

    # Invoices with remaining > 0
    all_invoices = await Invoice.find(Invoice.client_id == client_id).sort(-Invoice.date).to_list()
    inv_data = []
    for inv in all_invoices:
        amount = float(inv.total or 0)
        paid = float(inv.paid_amount or 0)
        remaining = float(inv.remaining_amount if inv.remaining_amount is not None else max(0.0, amount - paid))
        if remaining <= 0:
            continue
        dd = inv.due_date
        if hasattr(dd, 'date'):
            dd = dd.date()
        overdue = bool(dd and dd < today and remaining > 0)
        st = "paid" if remaining <= 0 else ("overdue" if overdue else ("partial" if paid > 0 else "pending"))
        # Fetch items for this invoice
        items = await InvoiceItem.find(InvoiceItem.invoice_id == inv.invoice_id).to_list()
        inv_data.append({
            "id": int(inv.invoice_id),
            "invoice_number": inv.invoice_number,
            "date": inv.date,
            "due_date": inv.due_date,
            "amount": amount,
            "paid_amount": paid,
            "remaining_amount": remaining,
            "status": st,
            "items": [
                {
                    "product_name": it.product_name,
                    "quantity": int(it.quantity or 0),
                    "price": float(it.price or 0),
                    "total": float(it.total or 0),
                } for it in (items or [])
            ]
        })

    # Manual debts with remaining > 0
    all_debts = await ClientDebt.find(ClientDebt.client_id == client_id).sort(-ClientDebt.date).to_list()
    md_data = []
    for d in all_debts:
        amount = float(d.amount or 0)
        paid = float(d.paid_amount or 0)
        remaining = float(d.remaining_amount if d.remaining_amount is not None else amount - paid)
        if remaining <= 0:
            continue
        dd = d.due_date
        if hasattr(dd, 'date'):
            dd = dd.date()
        overdue = bool(dd and dd < today and remaining > 0)
        st = d.status or ("paid" if remaining <= 0 else ("overdue" if overdue else ("partial" if paid > 0 else "pending")))
        md_data.append({
            "id": int(d.debt_id),
            "reference": d.reference,
            "date": d.date,
            "due_date": d.due_date,
            "amount": amount,
            "paid_amount": paid,
            "remaining_amount": remaining,
            "status": st,
            "description": d.description,
        })

    total_amount = sum(x.get("amount", 0.0) for x in inv_data) + sum(x.get("amount", 0.0) for x in md_data)
    total_paid = sum(x.get("paid_amount", 0.0) for x in inv_data) + sum(x.get("paid_amount", 0.0) for x in md_data)
    total_remaining = sum(x.get("remaining_amount", 0.0) for x in inv_data) + sum(x.get("remaining_amount", 0.0) for x in md_data)

    company_settings = await _load_company_settings()
    context = {
        "request": request,
        "global_settings": company_settings,
        "settings": {
            "company_name": company_settings.get("name"),
            "address": company_settings.get("address"),
            "city": company_settings.get("city"),
            "email": company_settings.get("email"),
            "phone": company_settings.get("phone"),
            "phone2": company_settings.get("phone2"),
            "instagram": company_settings.get("instagram"),
            "website": company_settings.get("website"),
            "logo": company_settings.get("logo"),
            "logo_path": company_settings.get("logo_path"),
            "footer_text": company_settings.get("footer_text"),
        },
        "client": cl,
        "invoices": inv_data,
        "manual_debts": md_data,
        "summary": {
            "total_amount": total_amount,
            "total_paid": total_paid,
            "total_remaining": total_remaining,
        }
    }
    return templates.TemplateResponse("print_client_debts.html", context)

@app.get("/stock-movements", response_class=HTMLResponse)
async def stock_movements_page(request: Request):
    return templates.TemplateResponse("stock_movements.html", {"request": request, "global_settings": await _load_company_settings()})

@app.get("/invoices", response_class=HTMLResponse)
async def invoices_page(request: Request):
    return templates.TemplateResponse("invoices.html", {"request": request, "global_settings": await _load_company_settings()})

@app.get("/quotations", response_class=HTMLResponse)
async def quotations_page(request: Request):
    return templates.TemplateResponse("quotations.html", {"request": request, "global_settings": await _load_company_settings()})

@app.get("/maintenances", response_class=HTMLResponse)
async def maintenances_page(request: Request):
    return templates.TemplateResponse("maintenances.html", {"request": request, "global_settings": await _load_company_settings()})

@app.get("/scan", response_class=HTMLResponse)
async def scan_page(request: Request):
    return templates.TemplateResponse("scan.html", {"request": request, "global_settings": await _load_company_settings()})

@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    return templates.TemplateResponse("settings.html", {"request": request, "global_settings": await _load_company_settings()})

@app.get("/suppliers", response_class=HTMLResponse)
async def suppliers_page(request: Request):
    return templates.TemplateResponse("suppliers.html", {"request": request, "global_settings": await _load_company_settings()})

@app.get("/delivery-notes", response_class=HTMLResponse)
async def delivery_notes_page(request: Request):
    return templates.TemplateResponse("delivery_notes.html", {"request": request, "global_settings": await _load_company_settings()})

@app.get("/bank-transactions", response_class=HTMLResponse)
async def bank_transactions_page(request: Request):
    return templates.TemplateResponse("bank_transactions.html", {"request": request, "global_settings": await _load_company_settings()})

@app.get("/reports", response_class=HTMLResponse)
async def reports_page(request: Request):
    return templates.TemplateResponse("reports.html", {"request": request, "global_settings": await _load_company_settings()})

@app.get("/supplier-invoices", response_class=HTMLResponse)
async def supplier_invoices_page(request: Request):
    return templates.TemplateResponse("supplier_invoices.html", {"request": request, "global_settings": await _load_company_settings()})

@app.get("/debts", response_class=HTMLResponse)
async def debts_page(request: Request):
    return templates.TemplateResponse("debts.html", {"request": request, "global_settings": await _load_company_settings()})

@app.get("/barcode-generator", response_class=HTMLResponse)
async def barcode_generator_page(request: Request):
    return templates.TemplateResponse("barcode_generator.html", {"request": request, "global_settings": await _load_company_settings()})

@app.get("/guide", response_class=HTMLResponse)
async def guide_page(request: Request):
    return templates.TemplateResponse("guide.html", {"request": request, "global_settings": await _load_company_settings()})

@app.get("/migration-manager", response_class=HTMLResponse)
async def migration_manager_page(request: Request):
    return templates.TemplateResponse("migration_manager.html", {"request": request, "global_settings": await _load_company_settings()})

@app.get("/cache-manager", response_class=HTMLResponse)
async def cache_manager_page(request: Request):
    return templates.TemplateResponse("cache_manager.html", {"request": request, "global_settings": await _load_company_settings()})

@app.get("/daily-recap", response_class=HTMLResponse)
async def daily_recap_page(request: Request):
    return templates.TemplateResponse("daily_recap.html", {"request": request, "global_settings": await _load_company_settings()})

@app.get("/daily-purchases", response_class=HTMLResponse)
async def daily_purchases_page(request: Request):
    return templates.TemplateResponse("daily_purchases.html", {"request": request, "global_settings": await _load_company_settings()})

@app.get("/daily-requests", response_class=HTMLResponse)
async def daily_requests_page(request: Request):
    return templates.TemplateResponse("daily_requests.html", {"request": request, "global_settings": await _load_company_settings()})

@app.get("/daily-sales", response_class=HTMLResponse)
async def daily_sales_page(request: Request):
    return templates.TemplateResponse("daily_sales.html", {"request": request, "global_settings": await _load_company_settings()})

@app.get("/google-sheets-sync", response_class=HTMLResponse)
async def google_sheets_sync_page(request: Request):
    return templates.TemplateResponse("google_sheets_sync.html", {"request": request, "global_settings": await _load_company_settings()})

@app.get("/whatsapp", response_class=HTMLResponse)
async def whatsapp_page(request: Request):
    return templates.TemplateResponse("whatsapp.html", {
        "request": request,
        "global_settings": await _load_company_settings(),
    })

@app.get("/api/whatsapp/qr")
async def whatsapp_qr_proxy():
    base_url = os.getenv("WHATSAPP_SERVICE_URL", "http://whatsapp_baileys:3002")
    url = f"{base_url.rstrip('/')}/api/qr"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(url)
            r.raise_for_status()
            return JSONResponse(content=r.json())
    except Exception:
        return JSONResponse(status_code=503, content={"status": "error"})

@app.get("/api/whatsapp/status")
async def whatsapp_status_proxy():
    base_url = os.getenv("WHATSAPP_SERVICE_URL", "http://whatsapp_baileys:3002")
    url = f"{base_url.rstrip('/')}/api/status"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(url)
            r.raise_for_status()
            return JSONResponse(content=r.json())
    except Exception:
        return JSONResponse(status_code=503, content={"status": "not_ready", "qrCode": False})

@app.get("/api/whatsapp/restart")
async def whatsapp_restart_proxy():
    base_url = os.getenv("WHATSAPP_SERVICE_URL", "http://whatsapp_baileys:3002")
    url = f"{base_url.rstrip('/')}/api/restart"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(url)
            r.raise_for_status()
            return JSONResponse(content=r.json())
    except Exception:
        return JSONResponse(status_code=503, content={"success": False, "error": "Service indisponible"})

# ===== Legacy compatibility endpoints =====
@app.get("/api/status")
async def whatsapp_status_legacy():
    return await whatsapp_status_proxy()

@app.get("/api/qr")
async def whatsapp_qr_legacy():
    return await whatsapp_qr_proxy()

# ===================== PRINT ROUTES (Invoice, Delivery Note) =====================

async def _load_company_settings() -> dict:
    result = {}

    # Load company settings from INVOICE_COMPANY
    try:
        s = await UserSettings.find_one(UserSettings.setting_key == "INVOICE_COMPANY")
        if s and s.setting_value:
            result = json.loads(s.setting_value)
    except Exception:
        pass

    # Fallback: read from consolidated appSettings.company if present
    if not result:
        try:
            legacy_us = await UserSettings.find_one(UserSettings.setting_key == "appSettings")
            if legacy_us and legacy_us.setting_value:
                data = json.loads(legacy_us.setting_value)
                comp = (data or {}).get("company") or {}
                if comp:
                    result = {
                        "name": comp.get("companyName") or comp.get("name"),
                        "address": comp.get("companyAddress") or comp.get("address"),
                        "email": comp.get("companyEmail") or comp.get("email"),
                        "phone": comp.get("companyPhone") or comp.get("phone"),
                        "website": comp.get("companyWebsite") or comp.get("website"),
                        "logo": comp.get("logo"),  # DataURL support
                    }
        except Exception:
            pass

    # Load favicon from appSettings.general.faviconUrl
    try:
        app_settings_record = await UserSettings.find_one(UserSettings.setting_key == "appSettings")
        if app_settings_record and app_settings_record.setting_value:
            app_data = json.loads(app_settings_record.setting_value)
            general = (app_data or {}).get("general") or {}
            favicon_url = general.get("faviconUrl")
            if favicon_url:
                result["favicon"] = favicon_url
    except Exception:
        pass

    return result


@app.get("/invoices/print/{invoice_id}", response_class=HTMLResponse)
async def print_invoice_page(request: Request, invoice_id: int):
    inv = await Invoice.find_one(Invoice.invoice_id == invoice_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Facture non trouvée")

    # Fetch related data
    items = await InvoiceItem.find(InvoiceItem.invoice_id == invoice_id).to_list()
    inv_client = await Client.find_one(Client.client_id == inv.client_id) if inv.client_id else None
    payments = await InvoicePayment.find(InvoicePayment.invoice_id == invoice_id).to_list()

    # Parse IMEIs from notes meta (if present)
    imeis_by_product_id = {}
    try:
        if inv.notes:
            txt = str(inv.notes)
            if "__SERIALS__=" in txt:
                sub = txt.split("__SERIALS__=", 1)[1]
                cut_idx = sub.find("\n__")
                if cut_idx != -1:
                    sub = sub[:cut_idx].strip()
                sub = sub.strip()
                try:
                    arr = json.loads(sub)
                except Exception:
                    m = re.search(r"__SERIALS__=(\[.*?\])", txt, flags=re.S)
                    arr = json.loads(m.group(1)) if m else []
                for entry in (arr or []):
                    pid = str(entry.get("product_id"))
                    imeis_by_product_id[pid] = entry.get("imeis") or []
    except Exception:
        pass

    # Parse original quotation quantities from notes meta (if present)
    quote_qty_by_product_id = {}
    try:
        if inv.notes:
            txt = str(inv.notes)
            if "__QUOTE_QTYS__=" in txt:
                sub = txt.split("__QUOTE_QTYS__=", 1)[1]
                cut_idx = sub.find("\n__")
                if cut_idx != -1:
                    sub = sub[:cut_idx].strip()
                sub = sub.strip()
                try:
                    arrq = json.loads(sub)
                except Exception:
                    mqq = re.search(r"__QUOTE_QTYS__=(\[.*?\])", txt, flags=re.S)
                    arrq = json.loads(mqq.group(1)) if mqq else []
                for entry in (arrq or []):
                    try:
                        pid = str(int(entry.get("product_id")))
                        qty = int(entry.get("qty") or 0)
                        quote_qty_by_product_id[pid] = qty
                    except Exception:
                        pass
    except Exception:
        pass

    # Build product descriptions map for involved products
    product_descriptions = {}
    try:
        product_ids = sorted({int(it.product_id) for it in (items or []) if it.product_id is not None})
        if product_ids:
            prods = await Product.find({"product_id": {"$in": product_ids}}).to_list()
            for p in prods:
                product_descriptions[str(p.product_id)] = (p.description or "")
    except Exception:
        product_descriptions = {}

    # Group items by product_id + price and attach IMEIs
    grouped_by_key: dict[str, dict] = {}
    item_to_key: dict[int, str] = {}

    for it in (items or []):
        name = it.product_name or ""

        # Sections: product_id nul et libellé commençant par [SECTION]
        if it.product_id is None and isinstance(name, str) and name.strip().startswith("[SECTION]"):
            key = f"SECTION|{getattr(it, 'item_id', id(it))}"
            raw = name.strip()
            title = raw[len("[SECTION]"):].strip(" :-") or raw[len("[SECTION]"):].strip()
            grouped_by_key[key] = {
                "product_id": None,
                "name": title or "Section",
                "description": "",
                "price": 0.0,
                "qty": 0,
                "total": 0.0,
                "imeis": [],
                "quote_qty": None,
                "is_section": True,
            }
            if getattr(it, "item_id", None) is not None:
                item_to_key[it.item_id] = key
            continue

        # Lignes personnalisées sans produit (services, etc.)
        if it.product_id is None:
            key = f"CUSTOM|{getattr(it, 'item_id', id(it))}"
            grouped_by_key[key] = {
                "product_id": None,
                "name": name,
                "description": "",
                "price": float(it.price or 0),
                "qty": int(it.quantity or 0),
                "total": float(it.total or 0),
                "imeis": [],
                "quote_qty": None,
                "is_section": False,
            }
            if getattr(it, "item_id", None) is not None:
                item_to_key[it.item_id] = key
            continue

        # Produits classiques: grouper par (product_id, price)
        key = f"{it.product_id}|{float(it.price or 0)}"
        if key not in grouped_by_key:
            grouped_by_key[key] = {
                "product_id": it.product_id,
                "name": it.product_name,
                "description": product_descriptions.get(str(it.product_id)) if it.product_id is not None else "",
                "price": float(it.price or 0),
                "qty": 0,
                "total": 0.0,
                "imeis": [],
                "quote_qty": None,
                "is_section": False,
            }
        g = grouped_by_key[key]
        g["qty"] += int(it.quantity or 0)
        g["total"] += float(it.total or 0)
        if getattr(it, "item_id", None) is not None:
            item_to_key[it.item_id] = key

        # Fallback: extract inline IMEI from product_name like "(IMEI: 123...)"
        try:
            pname = (it.product_name or "")
            m = re.search(r"\(IMEI:\s*([^)]+)\)", pname, flags=re.I)
            if m:
                imei = (m.group(1) or "").strip()
                if imei and imei not in g["imeis"]:
                    g["imeis"].append(imei)
        except Exception:
            pass

    # Replace qty/total with IMEIs count when available
    for g in grouped_by_key.values():
        if g.get("is_section"):
            continue
        lst = imeis_by_product_id.get(str(g["product_id"])) or []
        try:
            g["quote_qty"] = quote_qty_by_product_id.get(str(g["product_id"]))
        except Exception:
            g["quote_qty"] = g.get("quote_qty")
        if lst:
            g["imeis"] = lst
            g["qty"] = len(lst)
            g["total"] = g["qty"] * float(g["price"])
        elif g.get("imeis"):
            g["qty"] = len(g["imeis"])
            g["total"] = g["qty"] * float(g["price"])

    # Extract signature image from notes if embedded
    signature_data_url = None
    try:
        if inv.notes:
            m2 = re.search(r"__SIGNATURE__=(.*)$", inv.notes, flags=re.S)
            if m2:
                signature_data_url = (m2.group(1) or '').strip()
    except Exception:
        pass

    company_settings = await _load_company_settings()

    # Resolve payment method: invoice.payment_method or latest payment's method
    resolved_payment_method = getattr(inv, "payment_method", None)
    try:
        if not resolved_payment_method and payments:
            latest = None
            for p in payments:
                if not latest:
                    latest = p
                else:
                    try:
                        if (p.payment_date or 0) > (latest.payment_date or 0):
                            latest = p
                    except Exception:
                        pass
            if latest and getattr(latest, "payment_method", None):
                resolved_payment_method = latest.payment_method
    except Exception:
        pass

    # Déterminer si on doit afficher la garantie (certificat)
    warranty_certificate = None
    try:
        if getattr(inv, "has_warranty", False) and getattr(inv, "warranty_duration", None):
            warranty_certificate = {
                "duration": inv.warranty_duration,
                "start_date": getattr(inv, "warranty_start_date", None),
                "end_date": getattr(inv, "warranty_end_date", None),
                "invoice_number": inv.invoice_number,
                "client_name": (inv_client.name if inv_client else ""),
                "date": inv.date,
                "products": [item["name"] for item in grouped_by_key.values() if not item.get("is_section")],
            }
    except Exception:
        warranty_certificate = None

    # Reconstituer la liste ordonnée en respectant l'ordre d'origine des items
    ordered_items = []
    seen_keys = set()
    for it in (items or []):
        key = item_to_key.get(getattr(it, "item_id", -1))
        if not key or key in seen_keys:
            continue
        g = grouped_by_key.get(key)
        if not g:
            continue
        ordered_items.append(g)
        seen_keys.add(key)

    context = {
        "request": request,
        "invoice": inv,
        "grouped_items": ordered_items,
        "signature_data_url": signature_data_url,
        "resolved_payment_method": resolved_payment_method,
        "warranty_certificate": warranty_certificate,
        "settings": {
            "company_name": company_settings.get("name"),
            "address": company_settings.get("address"),
            "city": company_settings.get("city"),
            "email": company_settings.get("email"),
            "phone": company_settings.get("phone"),
            "phone2": company_settings.get("phone2"),
            "whatsapp": company_settings.get("whatsapp"),
            "instagram": company_settings.get("instagram"),
            "website": company_settings.get("website"),
            "logo": _normalize_logo(company_settings.get("logo") or company_settings.get("logo_path")),
            "logo_path": company_settings.get("logo_path"),
            "footer_text": company_settings.get("footer_text"),
            "rc_number": company_settings.get("rc_number"),
            "ninea_number": company_settings.get("ninea_number"),
        },
    }

    # Si la facture a une garantie, utiliser le template combiné avec certificat
    if warranty_certificate:
        return templates.TemplateResponse("print_invoice_with_warranty.html", context)
    return templates.TemplateResponse("print_invoice.html", context)


@app.get("/invoices/pdf/{invoice_id}")
async def get_invoice_pdf(request: Request, invoice_id: int):
    """Génère et retourne le PDF de la facture"""
    try:
        import pdfkit
    except ImportError:
        raise HTTPException(status_code=500, detail="pdfkit non installé")

    inv = await Invoice.find_one(Invoice.invoice_id == invoice_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Facture non trouvée")

    # Fetch items
    items = await InvoiceItem.find(InvoiceItem.invoice_id == invoice_id).to_list()

    template_name = "print_invoice.html"
    if getattr(inv, "has_warranty", False) and getattr(inv, "warranty_duration", None):
        template_name = "print_invoice_with_warranty.html"

    company_settings = await _load_company_settings()

    grouped_items = []
    for it in (items or []):
        grouped_items.append({
            "product_id": it.product_id,
            "name": it.product_name,
            "description": "",
            "price": float(it.price or 0),
            "qty": int(it.quantity or 0),
            "total": float(it.total or 0),
            "imeis": [],
            "is_section": False,
        })

    context = {
        "request": request,
        "invoice": inv,
        "grouped_items": grouped_items,
        "signature_data_url": None,
        "resolved_payment_method": getattr(inv, "payment_method", None),
        "warranty_certificate": None,
        "settings": {
            "company_name": company_settings.get("name"),
            "address": company_settings.get("address"),
            "city": company_settings.get("city"),
            "email": company_settings.get("email"),
            "phone": company_settings.get("phone"),
            "phone2": company_settings.get("phone2"),
            "whatsapp": company_settings.get("whatsapp"),
            "instagram": company_settings.get("instagram"),
            "website": company_settings.get("website"),
            "logo": _normalize_logo(company_settings.get("logo") or company_settings.get("logo_path")),
            "logo_path": company_settings.get("logo_path"),
            "footer_text": company_settings.get("footer_text"),
            "rc_number": company_settings.get("rc_number"),
            "ninea_number": company_settings.get("ninea_number"),
        },
    }

    html_content = templates.get_template(template_name).render(context)

    options = {
        'page-size': 'A4',
        'encoding': 'UTF-8',
        'enable-local-file-access': None,
        'no-stop-slow-scripts': None,
    }
    pdf_bytes = pdfkit.from_string(html_content, False, options=options)

    filename = f"Facture_{inv.invoice_number}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@app.get("/quotations/pdf/{quotation_id}")
async def get_quotation_pdf(request: Request, quotation_id: int):
    """Génère et retourne le PDF du devis"""
    try:
        import pdfkit
    except ImportError:
        raise HTTPException(status_code=500, detail="pdfkit non installé")

    q = await Quotation.find_one(Quotation.quotation_id == quotation_id)
    if not q:
        raise HTTPException(status_code=404, detail="Devis non trouvé")

    q_client = await Client.find_one(Client.client_id == q.client_id) if q.client_id else None
    q_items = await QuotationItem.find(QuotationItem.quotation_id == quotation_id).to_list()

    company_settings = await _load_company_settings()

    # Attach items and client for template compatibility
    context = {
        "request": request,
        "quotation": q,
        "signature_data_url": None,
        "settings": {
            "company_name": company_settings.get("name"),
            "address": company_settings.get("address"),
            "city": company_settings.get("city"),
            "email": company_settings.get("email"),
            "phone": company_settings.get("phone"),
            "phone2": company_settings.get("phone2"),
            "whatsapp": company_settings.get("whatsapp"),
            "instagram": company_settings.get("instagram"),
            "website": company_settings.get("website"),
            "logo": _normalize_logo(company_settings.get("logo") or company_settings.get("logo_path")),
            "logo_path": company_settings.get("logo_path"),
            "footer_text": company_settings.get("footer_text"),
            "rc_number": company_settings.get("rc_number"),
            "ninea_number": company_settings.get("ninea_number"),
        },
        "product_descriptions": {},
        "items": q_items,
        "client": q_client,
    }

    html_content = templates.get_template("print_quotation.html").render(context)

    options = {
        'page-size': 'A4',
        'encoding': 'UTF-8',
        'enable-local-file-access': None,
        'no-stop-slow-scripts': None,
        'disable-javascript': None,
        'print-media-type': None,
        'no-outline': None,
        'margin-top': '10mm',
        'margin-bottom': '10mm',
        'margin-left': '10mm',
        'margin-right': '10mm',
    }
    pdf_bytes = pdfkit.from_string(html_content, False, options=options)

    filename = f"Devis_{q.quotation_number}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@app.get("/quotations/print/{quotation_id}", response_class=HTMLResponse)
async def print_quotation_page(request: Request, quotation_id: int):
    q = await Quotation.find_one(Quotation.quotation_id == quotation_id)
    if not q:
        raise HTTPException(status_code=404, detail="Devis non trouvé")

    q_client = await Client.find_one(Client.client_id == q.client_id) if q.client_id else None
    q_items = await QuotationItem.find(QuotationItem.quotation_id == quotation_id).to_list()

    # Signature depuis notes meta si présente
    signature_data_url = None
    try:
        if q.notes:
            m2 = re.search(r"__SIGNATURE__=(.*)$", q.notes, flags=re.S)
            if m2:
                signature_data_url = (m2.group(1) or '').strip()
    except Exception:
        pass

    # Build product descriptions map
    product_descriptions = {}
    try:
        product_ids = sorted({int(it.product_id) for it in (q_items or []) if it.product_id is not None})
        if product_ids:
            prods = await Product.find({"product_id": {"$in": product_ids}}).to_list()
            for p in prods:
                product_descriptions[str(p.product_id)] = (p.description or "")
    except Exception:
        product_descriptions = {}

    company_settings = await _load_company_settings()
    context = {
        "request": request,
        "quotation": q,
        "client": q_client,
        "items": q_items,
        "settings": {
            **company_settings,
            "logo": _normalize_logo(company_settings.get("logo") or company_settings.get("logo_path")),
        },
        "signature_data_url": signature_data_url,
        "product_descriptions": product_descriptions,
    }
    return templates.TemplateResponse("print_quotation.html", context)

@app.get("/delivery-notes/print/{note_id}", response_class=HTMLResponse)
async def print_delivery_note_page(request: Request, note_id: int):
    dn = await DeliveryNote.find_one(DeliveryNote.delivery_note_id == note_id)
    if not dn:
        raise HTTPException(status_code=404, detail="Bon de livraison non trouvé")

    dn_items = await DeliveryNoteItem.find(DeliveryNoteItem.delivery_note_id == note_id).to_list()
    dn_client = await Client.find_one(Client.client_id == dn.client_id) if dn.client_id else None

    note = {
        "id": dn.delivery_note_id,
        "number": dn.delivery_note_number,
        "client_id": dn.client_id,
        "client_name": (dn_client.name if dn_client else None),
        "date": dn.date,
        "delivery_date": dn.delivery_date,
        "status": dn.status,
        "delivery_address": dn.delivery_address,
        "delivery_contact": dn.delivery_contact,
        "delivery_phone": dn.delivery_phone,
        "items": [
            {
                "product_id": it.product_id,
                "product_name": re.sub(r"\s*\(IMEI:\s*[^)]+\)\s*$", "", (it.product_name or ""), flags=re.I),
                "quantity": it.quantity,
                "unit_price": float(it.price or 0),
                "serials": (json.loads(it.serial_numbers) if (isinstance(it.serial_numbers, str) and it.serial_numbers.strip().startswith("[")) else []) if it.serial_numbers else []
            }
            for it in (dn_items or [])
        ],
        "subtotal": float(dn.subtotal or 0),
        "tax_rate": float(dn.tax_rate or 0),
        "tax_amount": float(dn.tax_amount or 0),
        "total": float(dn.total or 0),
        "notes": dn.notes,
        "created_at": dn.created_at,
    }

    # Construire la map des descriptions produits
    product_descriptions = {}
    try:
        item_list = note.get("items") or []
        product_ids = sorted({int(it.get("product_id")) for it in item_list if it.get("product_id") is not None})
        if product_ids:
            prods = await Product.find({"product_id": {"$in": product_ids}}).to_list()
            for p in prods:
                product_descriptions[str(p.product_id)] = (p.description or "")
    except Exception:
        product_descriptions = {}

    company_settings = await _load_company_settings()
    context = {
        "request": request,
        "note": note,
        "product_descriptions": product_descriptions,
        "settings": {
            "company_name": company_settings.get("name"),
            "address": company_settings.get("address"),
            "email": company_settings.get("email"),
            "phone": company_settings.get("phone"),
            "phone2": company_settings.get("phone2"),
            "whatsapp": company_settings.get("whatsapp"),
            "instagram": company_settings.get("instagram"),
            "website": company_settings.get("website"),
            "logo": company_settings.get("logo"),
            "rc_number": company_settings.get("rc_number"),
            "ninea_number": company_settings.get("ninea_number"),
        },
    }
    return templates.TemplateResponse("print_delivery_note.html", context)

# Gestion des erreurs
@app.exception_handler(404)
async def not_found_handler(request: Request, exc: HTTPException):
    return templates.TemplateResponse("404.html", {"request": request}, status_code=404)

@app.exception_handler(500)
async def internal_error_handler(request: Request, exc: HTTPException):
    return templates.TemplateResponse("500.html", {"request": request}, status_code=500)

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )
