// Gestion du scanner de codes-barres
let scanHistory = [];
let cameraStream = null;

// Initialisation
document.addEventListener('DOMContentLoaded', function() {
    try { setupBarcodeInput(); } catch (e) { console.warn('setupBarcodeInput error:', e); }
    loadScanHistory().catch(() => {});
    
    // Définir la date actuelle pour les nouveaux scans
    const now = new Date();
    now.setMinutes(now.getMinutes() - now.getTimezoneOffset());
    document.getElementById('movementDate')?.setAttribute('value', now.toISOString().slice(0, 16));
});

// Configuration de l'input code-barres
function setupBarcodeInput() {
    const barcodeInput = document.getElementById('barcodeInput');
    if (!barcodeInput) return;

    // Auto-focus sur l'input
    barcodeInput.focus();

    // Recherche automatique après saisie
    barcodeInput.addEventListener('input', debounce(function() {
        const value = (this && typeof this.value === 'string') ? this.value : '';
        const barcode = value.trim();
        if (barcode.length >= 3) {
            searchBarcode();
        }
    }, 500));

    // Recherche sur Enter
    barcodeInput.addEventListener('keypress', function(e) {
        if (e.key === 'Enter') {
            e.preventDefault();
            searchBarcode();
        }
    });
}

// Rechercher un code-barres
async function searchBarcode() {
    const barcodeInput = document.getElementById('barcodeInput');
    const barcode = barcodeInput.value.trim();
    
    if (!barcode) {
        showError('Veuillez saisir un code-barres');
        return;
    }

    try {
        showSearchLoading();
        
        const response = await fetch(`/api/products/scan/${encodeURIComponent(barcode)}`, {
            credentials: 'include'
        });

        if (!response.ok) {
            if (response.status === 404) {
                showNotFound(barcode);
                return;
            }
            throw new Error(`Erreur HTTP: ${response.status}`);
        }

        const result = await response.json();
        // Normaliser le format attendu par l'UI
        const normalized = normalizeScanResult(result);
        displaySearchResult(normalized, barcode);
        addToScanHistory(barcode, normalized);
        
        // Vider l'input pour le prochain scan
        barcodeInput.value = '';
        barcodeInput.focus();
        
    } catch (error) {
        console.error('Erreur lors de la recherche:', error);
        showError('Erreur lors de la recherche du code-barres');
        showNotFound(barcode);
    }
}

// Afficher le loading pendant la recherche
function showSearchLoading() {
    const resultsDiv = document.getElementById('searchResults');
    resultsDiv.innerHTML = `
        <div class="text-center py-4">
            <div class="spinner-border text-primary" role="status">
                <span class="visually-hidden">Recherche en cours...</span>
            </div>
            <div class="mt-2">Recherche en cours...</div>
        </div>
    `;
}

// Afficher le résultat de la recherche
function displaySearchResult(result, barcode) {
    const resultsDiv = document.getElementById('searchResults');
    
    let html = `
        <div class="alert alert-success">
            <h6><i class="bi bi-check-circle me-2"></i>Produit trouvé !</h6>
        </div>
        
        <div class="product-result">
            <div class="row">
                <div class="col-12">
                    <h5 class="text-primary">${escapeHtml(result.product_name)}</h5>
                    <p class="text-muted mb-2">Code-barres: <strong>${escapeHtml(barcode)}</strong></p>
                </div>
            </div>
    `;

    if (result.variant) {
        html += `
            <div class="row mt-3">
                <div class="col-md-6">
                    <h6>Informations Variante</h6>
                    <p><strong>IMEI/Série:</strong> ${escapeHtml(result.variant.imei_serial)}</p>
                    ${result.variant.attributes ? `<p><strong>Attributs:</strong> ${escapeHtml(result.variant.attributes)}</p>` : ''}
                    <p><strong>Statut:</strong> 
                        <span class="badge bg-${result.variant.is_sold ? 'danger' : 'success'}">
                            ${result.variant.is_sold ? 'Vendu' : 'Disponible'}
                        </span>
                    </p>
                </div>
                <div class="col-md-6">
                    <h6>Détails Produit</h6>
                     <p><strong>Prix:</strong> ${formatCurrency(result.price || 0)}</p>
                    <p><strong>Catégorie:</strong> ${escapeHtml(result.category_name || 'Non définie')}</p>
                </div>
            </div>
        `;
    } else {
        html += `
            <div class="row mt-3">
                <div class="col-md-6">
                    <h6>Informations Produit</h6>
                     <p><strong>Prix:</strong> ${formatCurrency(result.price || 0)}</p>
                    <p><strong>Catégorie:</strong> ${escapeHtml(result.category_name || 'Non définie')}</p>
                </div>
                <div class="col-md-6">
                    <h6>Stock</h6>
                    <p><strong>Quantité:</strong> ${result.stock_quantity || 0}</p>
                    <p><strong>Statut:</strong> 
                        <span class="badge bg-${(result.stock_quantity || 0) > 0 ? 'success' : 'warning'}">
                            ${(result.stock_quantity || 0) > 0 ? 'En stock' : 'Stock faible'}
                        </span>
                    </p>
                </div>
            </div>
        `;
    }

    html += `
            <div class="row mt-3">
                <div class="col-12">
                    <div class="btn-group w-100" role="group">
                        <button class="btn btn-outline-primary" onclick="viewProductDetails(${result.product_id})">
                            <i class="bi bi-eye me-1"></i>Voir détails
                        </button>
                        <button class="btn btn-outline-success" onclick="createStockMovement('${barcode}')">
                            <i class="bi bi-arrow-left-right me-1"></i>Mouvement stock
                        </button>
                        <button class="btn btn-outline-info" onclick="addToInvoice('${barcode}')">
                            <i class="bi bi-receipt me-1"></i>Ajouter à facture
                        </button>
                    </div>
                </div>
            </div>
        </div>
    `;
    
    resultsDiv.innerHTML = html;
}

// Adapter la réponse backend à ce que l'UI attend
function normalizeScanResult(res) {
    const base = {
        product_id: res.product_id || (res.product && res.product.product_id) || 0,
        product_name: res.product_name || (res.product && res.product.name) || '',
        price: typeof res.price === 'number' ? res.price : (res.product && Number(res.product.price)) || 0,
        category_name: res.category_name || (res.product && res.product.category) || null,
        stock_quantity: typeof res.stock_quantity === 'number' ? res.stock_quantity : (res.product && Number(res.product.quantity)) || 0
    };
    if (res.variant) {
        base.variant = {
            imei_serial: res.variant.imei_serial,
            attributes: res.variant.attributes,
            is_sold: !!res.variant.is_sold
        };
    }
    return base;
}

// Afficher quand aucun produit n'est trouvé
function showNotFound(barcode) {
    const resultsDiv = document.getElementById('searchResults');
    resultsDiv.innerHTML = `
        <div class="alert alert-warning">
            <h6><i class="bi bi-exclamation-triangle me-2"></i>Produit non trouvé</h6>
            <p class="mb-2">Aucun produit trouvé avec le code-barres: <strong>${escapeHtml(barcode)}</strong></p>
            <button class="btn btn-sm btn-primary" onclick="createProductWithBarcode('${escapeHtml(barcode)}')">
                <i class="bi bi-plus-circle me-1"></i>Créer un nouveau produit
            </button>
        </div>
    `;
}

// Ajouter à l'historique des scans
async function addToScanHistory(barcode, result) {
    const scanEntry = {
        timestamp: new Date(),
        barcode: barcode,
        product: result.product_name,
        stock: result.variant ? (result.variant.is_sold ? 0 : 1) : (result.stock_quantity || 0),
        found: true
    };
    
    scanHistory.unshift(scanEntry);
    
    // Limiter l'historique à 50 entrées
    if (scanHistory.length > 50) {
        scanHistory = scanHistory.slice(0, 50);
    }
    
    // Sauvegarder dans SQLite via API
    if (scanHistory.length > 0) {
        const latestScan = scanHistory[scanHistory.length - 1];
        await apiStorage.addScanHistory(latestScan);
    }
    
    updateScanHistoryDisplay();
}

// Charger l'historique des scans
async function loadScanHistory() {
    try {
        const items = await apiStorage.getScanHistory();
        scanHistory = Array.isArray(items) ? items : [];
        updateScanHistoryDisplay();
    } catch (error) {
        console.error('Erreur lors du chargement de l\'historique:', error);
        scanHistory = [];
    }
}

// Mettre à jour l'affichage de l'historique
function updateScanHistoryDisplay() {
    const tbody = document.getElementById('scanHistoryBody');
    if (!tbody) return;

    if (scanHistory.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="5" class="text-center text-muted py-4">
                    Aucun scan effectué
                </td>
            </tr>
        `;
        return;
    }

    tbody.innerHTML = scanHistory.slice(0, 10).map(entry => `
        <tr>
            <td>${formatDateTime(entry.timestamp)}</td>
            <td><code>${escapeHtml(entry.barcode)}</code></td>
            <td>${escapeHtml(entry.product)}</td>
            <td>
                <span class="badge bg-${entry.stock > 0 ? 'success' : 'warning'}">
                    ${entry.stock}
                </span>
            </td>
            <td>
                <button class="btn btn-sm btn-outline-primary" onclick="searchSpecificBarcode('${escapeHtml(entry.barcode)}')">
                    <i class="bi bi-arrow-repeat"></i>
                </button>
            </td>
        </tr>
    `).join('');
}

// Rechercher un code-barres spécifique depuis l'historique
function searchSpecificBarcode(barcode) {
    document.getElementById('barcodeInput').value = barcode;
    searchBarcode();
}

// Démarrer la caméra (fonctionnalité future)
function startCamera() {
    showInfo('Fonctionnalité caméra en cours de développement. Utilisez un scanner USB ou saisissez manuellement le code-barres.');
}

// Arrêter la caméra
function stopCamera() {
    if (cameraStream) {
        cameraStream.getTracks().forEach(track => track.stop());
        cameraStream = null;
    }
    
    document.getElementById('cameraContainer').style.display = 'none';
    document.getElementById('stopCameraBtn').style.display = 'none';
}

// Actions sur les produits trouvés
function viewProductDetails(productId) {
    window.location.href = `/products?product=${productId}`;
}

function createStockMovement(barcode) {
    // Rediriger vers la page des mouvements de stock avec le code-barres
    window.location.href = `/stock-movements?barcode=${encodeURIComponent(barcode)}`;
}

function addToInvoice(barcode) {
    // Rediriger vers la création de facture avec le produit
    window.location.href = `/invoices?add_barcode=${encodeURIComponent(barcode)}`;
}

function createProductWithBarcode(barcode) {
    // Rediriger vers la création de produit avec le code-barres pré-rempli
    window.location.href = `/products?barcode=${encodeURIComponent(barcode)}`;
}

// Vider l'historique des scans
async function clearScanHistory() {
    if (confirm('Êtes-vous sûr de vouloir vider l\'historique des scans ?')) {
        scanHistory = [];
        await apiStorage.clearScanHistory();
        updateScanHistoryDisplay();
        showSuccess('Historique des scans vidé');
    }
}
