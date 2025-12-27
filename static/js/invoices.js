// Supprimer les backdrops orphelins et réinitialiser les styles body si aucun modal n'est visible
function cleanupModalBackdrops() {
    try {
        const anyShown = document.querySelector('.modal.show');
        if (!anyShown) {
            document.querySelectorAll('.modal-backdrop').forEach(el => el.remove());
            document.body.classList.remove('modal-open');
            try { document.body.style.removeProperty('overflow'); } catch(e) {}
            try { document.body.style.removeProperty('padding-right'); } catch(e) {}
        }
    } catch (e) { /* ignore */ }
}
// Gestion des factures
let currentPage = 1;
// Mémorisation du dernier fichier de signature
let lastSignatureFile = null;
// Réduire le nombre de factures par page pour limiter le travail côté navigateur
const itemsPerPage = 20;
let invoices = [];
let filteredInvoices = [];
let clients = [];
let products = [];
let productVariantsByProductId = new Map(); // product_id -> [{variant_id, imei_serial, barcode, is_sold}]
let invoiceItems = [];
// Tri courant (pour la liste principale)
let currentSort = { by: 'created_at', dir: 'desc' };
// Quantités d'origine issues du devis (si disponibles) par product_id
let quoteQtyByProductId = new Map();
// Helpers to track used IMEIs across rows
function _normalizeCode(v) { return String(v || '').trim().toLowerCase(); }
function getUsedImeis(excludeItemId) {
    const set = new Set();
    (invoiceItems || []).forEach(i => {
        if (excludeItemId && i.id === excludeItemId) return;
        (i.scannedImeis || []).forEach(imei => set.add(_normalizeCode(imei)));
    });
    return set;
}
function rowHasImei(row, imei) {
    const n = _normalizeCode(imei);
    return (row.scannedImeis || []).some(x => _normalizeCode(x) === n);
}

// Calcule le stock disponible pour un produit
function computeAvailableStock(product) {
    try {
        const variants = productVariantsByProductId.get(Number(product.product_id)) || [];
        if (variants.length > 0) {
            return variants.filter(v => !v.is_sold).length;
        }
        return Number(product.quantity || 0);
    } catch (e) {
        return 0;
    }
}

async function resetInvoicePaymentsFromDetail(invoiceId) {
    try {
        if (!invoiceId) return;
        if (!confirm('Réinitialiser tous les paiements de cette facture ?')) return;
        await axios.post(`/api/invoices/${invoiceId}/payments/reset`);
        showSuccess('Paiements réinitialisés');
        await loadInvoiceDetail(invoiceId);
    } catch (error) {
        console.error('Erreur réinitialisation paiements:', error);
        showError(error?.response?.data?.detail || 'Erreur lors de la réinitialisation des paiements');
    }
}

// Initialisation
document.addEventListener('DOMContentLoaded', function() {
    // Inject sort arrows like products/quotations if header present
    try {
        const thMap = [
            ['number', 'Numéro'], ['client', 'Client'], ['date', 'Date'], ['due', 'Échéance'], ['total', 'Montant'], ['status', 'Statut']
        ];
        if (typeof window.buildSortHeader !== 'function') {
            window.buildSortHeader = function(label, byKey) {
                const isActive = (currentSort.by === byKey);
                const ascActive = isActive && currentSort.dir === 'asc';
                const descActive = isActive && currentSort.dir === 'desc';
                return `
                    <div class="d-flex align-items-center gap-2 sort-header">
                        <span>${label}</span>
                        <div class="sort-btn-group" role="group" aria-label="Trier ${label}">
                            <button type="button" class="sort-btn ${ascActive ? 'active' : ''}" data-sort-by="${byKey}" data-sort-dir="asc" title="Trier par ${label} (croissant)">
                                <i class="bi bi-chevron-up"></i>
                            </button>
                            <button type="button" class="sort-btn ${descActive ? 'active' : ''}" data-sort-by="${byKey}" data-sort-dir="desc" title="Trier par ${label} (décroissant)">
                                <i class="bi bi-chevron-down"></i>
                            </button>
                        </div>
                    </div>
                `;
            };
        }
        thMap.forEach(([k, label]) => {
            const th = document.querySelector(`table thead th[data-col="${k}"]`);
            if (th) th.innerHTML = buildSortHeader(label, k);
        });
        document.querySelectorAll('table thead [data-sort-by]')?.forEach(btn => {
            btn.addEventListener('click', () => {
                const by = btn.getAttribute('data-sort-by');
                const dir = btn.getAttribute('data-sort-dir');
                currentSort = { by, dir };
                loadInvoices();
            });
        });
    } catch (e) { /* ignore */ }
    // Utiliser la nouvelle logique d'authentification basée sur cookies
    const ready = () => {
        const hasAuthManager = !!window.authManager;
        const hasUser = !!(hasAuthManager && window.authManager.userData && Object.keys(window.authManager.userData).length);
        return hasAuthManager && (window.authManager.isAuthenticatedSync() || hasUser);
    };

    // Masquer la carte "Chiffre d'Affaires" pour les non-admin
    try {
        if (window.authManager && !window.authManager.isAdmin()) {
            const revenueEl = document.getElementById('totalRevenue');
            const revenueCardCol = revenueEl ? revenueEl.closest('.col-md-3') : null;
            if (revenueCardCol) revenueCardCol.style.display = 'none';
        }
    } catch (e) { /* ignore */ }

    // Initialiser immédiatement sans délai pour un chargement instantané
    loadInvoices();
    try { loadStats(); } catch(e){}
    // Lazy: clients/products chargés à l'usage
    setupEventListeners();
    setDefaultDates();
    // Si on vient d'une conversion devis -> facture, ouvrir le formulaire pré-rempli
    try {
        const raw = sessionStorage.getItem('prefill_invoice_from_quotation');
        if (raw) {
            const data = JSON.parse(raw);
            sessionStorage.removeItem('prefill_invoice_from_quotation');
            if (!Array.isArray(products) || !products.length) { try { loadProducts(); } catch(e) {} }
            if (!Array.isArray(clients) || !clients.length) { try { loadClients(); } catch(e) {} }
            Promise.all([waitForProductsLoaded(), waitForClientsLoaded()])
                .then(() => preloadPrefilledInvoiceFromQuotation(data));
        }
        // Si on vient de convertir un devis via API, ouvrir directement l'édition de la facture créée
        const editId = sessionStorage.getItem('edit_invoice_id');
        if (editId) {
            sessionStorage.removeItem('edit_invoice_id');
            // Précharger la facture dans le formulaire d'édition et ouvrir le modal
            preloadInvoiceIntoForm(Number(editId));
        }
        // Si on arrive depuis un devis pour ouvrir une facture spécifique, pré-remplir le champ recherche
        const q = sessionStorage.getItem('invoiceSearchQuery');
        if (q) {
            sessionStorage.removeItem('invoiceSearchQuery');
            try {
                const input = document.getElementById('invoiceSearch');
                if (input) { input.value = q; filterInvoices(); }
            } catch(e) {}
        }
        // Si on arrive depuis la page des créances, ouvrir directement le détail
        const openId = sessionStorage.getItem('open_invoice_detail_id');
        if (openId) {
            sessionStorage.removeItem('open_invoice_detail_id');
            // Attendre un court instant que la liste soit affichée puis ouvrir
            setTimeout(() => {
                try { viewInvoice(Number(openId)); } catch(e) {}
            }, 200);
        }
    } catch (e) {}

    // Nettoyage global des backdrops Bootstrap après fermeture d'un modal
    try {
        document.addEventListener('hidden.bs.modal', () => {
            cleanupModalBackdrops();
        });
    } catch (e) { /* ignore */ }
});

function setupEventListeners() {
    // Filtres
    // Dès qu'un filtre change, recharger côté serveur
    document.getElementById('statusFilter')?.addEventListener('change', () => { currentPage = 1; loadInvoices(); });
    document.getElementById('clientFilter')?.addEventListener('input', debounce(() => { currentPage = 1; loadInvoices(); }, 300));
    document.getElementById('dateFromFilter')?.addEventListener('change', () => { currentPage = 1; loadInvoices(); });
    document.getElementById('dateToFilter')?.addEventListener('change', () => { currentPage = 1; loadInvoices(); });
    document.getElementById('invoiceSearch')?.addEventListener('input', debounce(() => { currentPage = 1; loadInvoices(); }, 250));

    // TVA controls (bind once at load; also rebind inside openInvoiceModal for safety)
    setupTaxControls();
    // Catch-all: recalc totals on any input change within the invoice form
    const form = document.getElementById('invoiceForm');
    if (form) {
        form.addEventListener('input', () => calculateTotals());
        form.addEventListener('change', () => calculateTotals());
    }

    // Barcode input: Enter or short pause auto-add
    const barcodeInput = document.getElementById('invoiceBarcodeInput');
    if (barcodeInput) {
        barcodeInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                addItemByBarcode();
            }
        });
        let scanTimer;
        barcodeInput.addEventListener('input', () => {
            clearTimeout(scanTimer);
            const v = (barcodeInput.value || '').trim();
            if (v.length >= 3) {
                scanTimer = setTimeout(() => addItemByBarcode(), 250);
            }
        });
    }
    // Quick client modal
    document.getElementById('openQuickClientBtn')?.addEventListener('click', () => {
        new bootstrap.Modal(document.getElementById('clientQuickModal')).show();
        setTimeout(() => document.getElementById('qcName')?.focus(), 150);
    });
    document.getElementById('saveQuickClientBtn')?.addEventListener('click', saveQuickClient);

    // Initialiser la recherche client côté serveur (si non initialisée)
    try { setupClientSearch(); } catch (e) {}

    // Payment now switch
    const paySwitch = document.getElementById('paymentNowSwitch');
    if (paySwitch) {
        paySwitch.addEventListener('change', () => {
            const v = paySwitch.checked;
            const paymentFields = document.getElementById('paymentNowFields');
            const amountInput = document.getElementById('paymentNowAmount');
            const maxAmountSpan = document.getElementById('maxPaymentAmount');
            
            if (paymentFields) paymentFields.style.display = v ? 'flex' : 'none';
            
            if (v && amountInput) {
                // Calculer le montant maximum en tenant compte des paiements existants
                const totalAmount = parseFloat(document.getElementById('invoiceForm').dataset.total || '0');
                const invoiceId = document.getElementById('invoiceId').value;
                
                if (invoiceId) {
                    // En édition : récupérer le montant restant depuis les infos affichées
                    const paymentInfo = document.getElementById('existingPaymentInfo');
                    if (paymentInfo && paymentInfo.style.display !== 'none') {
                        // Extraire le montant restant du résumé affiché
                        const paymentSummary = document.getElementById('paymentStatusSummary');
                        if (paymentSummary) {
                            // Extraire le montant restant en cherchant tous les chiffres (y compris les espaces)
                            const remainingMatch = paymentSummary.innerHTML.match(/Restant:[^\d]*([\d\s,\.]+)\s*(?:FCFA|€|\$)?/i);
                            if (remainingMatch) {
                                // Nettoyer le montant en supprimant les espaces et en remplaçant la virgule par un point
                                const cleanAmount = remainingMatch[1].replace(/\s/g, '').replace(',', '.');
                                const remainingAmount = parseFloat(cleanAmount);
                                
                                if (!isNaN(remainingAmount)) {
                                    amountInput.value = remainingAmount.toString();
                                    amountInput.max = remainingAmount.toString();
                                    if (maxAmountSpan) maxAmountSpan.textContent = formatCurrency(remainingAmount);
                                    return;
                                }
                            }
                        }
                    }
                }
                
                // Par défaut (nouvelle facture ou pas de paiements existants)
                amountInput.value = totalAmount.toString();
                amountInput.max = totalAmount.toString();
                if (maxAmountSpan) maxAmountSpan.textContent = formatCurrency(totalAmount);
            }
        });
    }

    // Charger les méthodes de paiement (depuis SQLite via API) et peupler les selects
    populatePaymentMethodSelects();

    // Délégation pour recherche produit dans le tableau des articles (document-level)
    document.addEventListener('input', debounce(async (e) => {
        const target = e.target;
        if (!(target && target.classList && target.classList.contains('product-search-input'))) return;
        const query = String(target.value || '').trim();
        const row = target.closest('tr');
        if (!row) return;
        const suggestBox = row.querySelector('.product-suggestions');
        if (!suggestBox) return;
        if (query.length < 2) { suggestBox.classList.add('d-none'); suggestBox.innerHTML = ''; return; }
        try {
            const res = await axios.get('/api/products/', { params: { search: query, limit: 20 } });
            let list = res.data?.items || res.data || [];
            // Ne plus masquer les produits épuisés, les afficher avec indication
            // Conserver la dernière liste pour la sélection (fallback si products[] non chargé)
            try { window._latestProductResults = list; } catch (e) {}
            suggestBox.innerHTML = (list.length ? list : [{ __empty: true }]).map(p => {
                if (p.__empty) {
                    return '<div class="list-group-item text-muted small">Aucun produit</div>';
                }
                const variants = Array.isArray(p.variants) ? p.variants : [];
                const hasVariants = variants.length > 0;
                const available = hasVariants ? variants.filter(v => !v.is_sold).length : Number(p.quantity || 0);
                const stockBadge = `<span class="badge ${available>0?'bg-success':'bg-danger'} ms-2">${available>0?('Stock: '+available):'Rupture'}</span>`;
                const sub = [p.category, p.brand, p.model].filter(Boolean).join(' • ');
                const isOutOfStock = available === 0;
                return `
                <div class="list-group-item list-group-item-action d-flex justify-content-between align-items-center ${isOutOfStock ? 'text-muted' : ''}" data-product-id="${p.product_id}">
                    <div class="d-flex align-items-center gap-2 me-2">
                        ${p.image_path ? `
                            <img src="/${p.image_path}" alt="${escapeHtml(p.name || '')}"
                                 style="width: 40px; height: 40px; object-fit: cover; border-radius: 4px;">
                        ` : ''}
                        <div>
                            <div class="fw-semibold d-flex align-items-center">${escapeHtml(p.name || '')} ${stockBadge}</div>
                            <div class="text-muted small">${[p.barcode ? 'Code: '+escapeHtml(p.barcode) : '', sub ? escapeHtml(sub) : ''].filter(Boolean).join(' • ')}</div>
                        </div>
                    </div>
                    <div class="text-nowrap ms-3">
                        <div class="fw-semibold">${formatCurrency(p.price)}</div>
                        ${p.wholesale_price ? `<div class="text-muted small">Gros: ${formatCurrency(p.wholesale_price)}</div>` : ''}
                    </div>
                </div>`;
            }).join('');
            suggestBox.classList.remove('d-none');
        } catch (err) {
            suggestBox.classList.add('d-none'); suggestBox.innerHTML = '';
        }
    }, 250));

    // Sélection d'une suggestion (document-level) - utiliser mousedown pour capturer avant blur
    document.addEventListener('mousedown', (e) => {
        const item = e.target.closest('.list-group-item[data-product-id]');
        if (!item) return;
        // Empêcher le comportement par défaut et la propagation
        e.preventDefault();
        e.stopPropagation();

        const productId = item.getAttribute('data-product-id');
        if (!productId) return;

        // Trouver le conteneur de la ligne: on cherche l'ancêtre portant data-item-id
        let row = item.closest('[data-item-id]');
        if (!row) {
            const suggestBox = item.closest('.product-suggestions');
            if (suggestBox) {
                row = suggestBox.closest('[data-item-id]');
            }
        }

        const idAttr = row?.getAttribute('data-item-id');
        console.log('[ProductSelect] click on product', productId, 'row:', row, 'idAttr:', idAttr);

        if (productId && idAttr) {
            selectProduct(Number(idAttr), productId);
            const box = row.querySelector('.product-suggestions');
            if (box) { box.innerHTML = ''; box.classList.add('d-none'); }
            const input = row.querySelector('.product-search-input');
            if (input) input.value = '';
        }
    }, true); // Utiliser la phase de capture pour s'exécuter en premier

    // Cacher le dropdown si clic en dehors
    document.addEventListener('click', (e) => {
        // Ne pas fermer si le clic est sur un élément de suggestion (déjà géré ci-dessus)
        if (e.target.closest('.list-group-item[data-product-id]')) return;
        document.querySelectorAll('.product-suggestions').forEach(box => {
            // Vérifier si le clic est dans le conteneur parent (input-group ou le box lui-même)
            const container = box.closest('.position-relative');
            if (container && container.contains(e.target)) return;
            if (box.contains(e.target)) return;
            box.classList.add('d-none');
        });
    });
}

function setupTaxControls() {
    const taxSwitch = document.getElementById('showTaxSwitch');
    const taxRateInput = document.getElementById('taxRateInput');
    if (taxSwitch) {
        taxSwitch.removeEventListener('change', calculateTotals);
        taxSwitch.addEventListener('change', calculateTotals);
    }
    if (taxRateInput) {
        const handler = () => calculateTotals();
        taxRateInput.removeEventListener('input', handler);
        taxRateInput.addEventListener('input', handler);
        taxRateInput.removeEventListener('change', handler);
        taxRateInput.addEventListener('change', handler);
        taxRateInput.removeEventListener('keyup', handler);
        taxRateInput.addEventListener('keyup', handler);
        taxRateInput.removeEventListener('blur', handler);
        taxRateInput.addEventListener('blur', handler);
    }
}

function setupWarrantyControls() {
    const warrantySwitch = document.getElementById('hasWarrantySwitch');
    if (warrantySwitch) {
        warrantySwitch.removeEventListener('change', handleWarrantyChange);
        warrantySwitch.addEventListener('change', handleWarrantyChange);
    }
}

function handleWarrantyChange() {
    const warrantySwitch = document.getElementById('hasWarrantySwitch');
    const warrantyOptions = document.getElementById('warrantyOptions');
    const warrantyInfo = document.getElementById('warrantyInfo');
    const isEnabled = warrantySwitch?.checked || false;
    
    if (warrantyOptions) {
        warrantyOptions.style.display = isEnabled ? 'block' : 'none';
    }
    if (warrantyInfo) {
        warrantyInfo.style.display = isEnabled ? 'block' : 'none';
    }
}

function setDefaultDates() {
    const today = new Date().toISOString().split('T')[0];
    const invoiceDate = document.getElementById('invoiceDate');
    const paymentDate = document.getElementById('paymentDate');
    
    if (invoiceDate) invoiceDate.value = today;
    if (paymentDate) paymentDate.value = today;
    
    // Date d'échéance par défaut (30 jours)
    const dueDate = new Date();
    dueDate.setDate(dueDate.getDate() + 4);
    const dueDateInput = document.getElementById('dueDate');
    if (dueDateInput) dueDateInput.value = dueDate.toISOString().split('T')[0];
}

// Attendre le chargement des produits (variants inclus)
function waitForProductsLoaded(maxWaitMs = 2000) {
    return new Promise(resolve => {
        const start = Date.now();
        const tick = () => {
            if ((products && products.length) || (productVariantsByProductId && productVariantsByProductId.size)) {
                resolve();
                return;
            }
            if (Date.now() - start > maxWaitMs) { resolve(); return; }
            setTimeout(tick, 50);
        };
        tick();
    });
}

// Attendre le chargement des clients (pour préremplissage depuis devis)
function waitForClientsLoaded(maxWaitMs = 2000) {
    return new Promise(resolve => {
        const start = Date.now();
        const tick = () => {
            if (Array.isArray(clients) && clients.length) { resolve(); return; }
            if (Date.now() - start > maxWaitMs) { resolve(); return; }
            setTimeout(tick, 50);
        };
        tick();
    });
}

// Charger les statistiques
async function loadStats() {
    try {
        const { data: stats } = await axios.get('/api/invoices/stats/dashboard');
        document.getElementById('totalInvoices').textContent = stats.total_invoices || 0;
        document.getElementById('paidInvoices').textContent = stats.paid_invoices || 0;
        document.getElementById('pendingInvoices').textContent = stats.pending_invoices || 0;
        document.getElementById('totalRevenue').textContent = formatCurrency(stats.total_revenue || 0);
    } catch (error) {
        console.error('Erreur lors du chargement des statistiques:', error);
    }
}

// Charger la liste des factures (server-side pagination)
async function loadInvoices(page) {
    try {
        if (page) {
            currentPage = page;
        }
        showLoading();
        // Construire les paramètres côté serveur
        const statusFilter = document.getElementById('statusFilter')?.value || '';
        const clientFilter = (document.getElementById('clientFilter')?.value || '').trim();
        const dateFromFilter = document.getElementById('dateFromFilter')?.value || '';
        const dateToFilter = document.getElementById('dateToFilter')?.value || '';
        const search = (document.getElementById('invoiceSearch')?.value || '').trim();

        const params = {
            page: currentPage,
            page_size: itemsPerPage,
            sort_by: currentSort.by,
            sort_dir: currentSort.dir,
            _ts: Date.now() // cache-buster to avoid stale responses après suppression
        };
        if (statusFilter) params.status_filter = statusFilter;
        if (clientFilter) params.client_search = clientFilter;
        if (dateFromFilter) params.start_date = dateFromFilter;
        if (dateToFilter) params.end_date = dateToFilter;
        if (search) params.search = search;

        // Appel direct sans couche de retry/timeout supplémentaire
        const response = await axios.get('/api/invoices/paginated', { params });

        const payload = response?.data || { invoices: [], total: 0, page: 1, pages: 1 };
        invoices = Array.isArray(payload.invoices) ? payload.invoices : [];
        filteredInvoices = [...invoices];

        // Si une recherche d'ID/numéro a été demandée depuis la page devis, l'appliquer maintenant
        try {
            const pending = sessionStorage.getItem('invoiceSearchQuery');
            if (pending) {
                const input = document.getElementById('invoiceSearch');
                if (input) {
                    input.value = pending;
                    filterInvoices();
                    // garder ou nettoyer selon préférence; on nettoie pour ne pas réappliquer à chaque refresh
                    sessionStorage.removeItem('invoiceSearchQuery');
                }
            }
        } catch (e) {}

        displayInvoices();
        updatePagination(payload.total || filteredInvoices.length);
    } catch (error) {
        console.error('Erreur lors du chargement des factures:', error);
        
        // Afficher un message d'erreur dans le tableau
        const tbody = document.getElementById('invoicesTableBody');
        if (tbody) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="7" class="text-center text-danger py-4">
                        <i class="bi bi-exclamation-triangle fs-1 d-block mb-2"></i>
                        Erreur lors du chargement des factures
                    </td>
                </tr>
            `;
        }
        
        if (typeof showAlert === 'function') {
            showAlert('Erreur lors du chargement des factures', 'danger');
        }
    }
}

// Charger les clients
async function loadClients() {
    try {
        const { data } = await axios.get('/api/clients/');
        clients = data.items || data || [];
        populateClientSelect();
        setupClientSearch();
    } catch (error) {
        console.error('Erreur lors du chargement des clients:', error);
    }
}

// Charger les produits
async function loadProducts() {
    try {
        const { data } = await axios.get('/api/products/', { params: { limit: 1000 } });
        products = data.items || data || [];
        // Prefetch variants per product for quick selection
        await Promise.all(products.map(async (p) => {
            try {
                const variants = (p.variants && p.variants.length) ? p.variants : [];
                productVariantsByProductId.set(p.product_id, variants);
            } catch (e) { /* ignore */ }
        }));
        // Build quick lookup by barcode
        window._productByBarcode = new Map();
        products.forEach(p => { if (p.barcode) window._productByBarcode.set(String(p.barcode).trim(), p); });
        // Add variants barcodes too
        products.forEach(p => {
            (productVariantsByProductId.get(p.product_id) || []).forEach(v => {
                if (v.barcode) window._productByBarcode.set(String(v.barcode).trim(), { ...p, _variant: v });
                if (v.imei_serial) window._productByBarcode.set(String(v.imei_serial).trim(), { ...p, _variant: v });
            });
        });
    } catch (error) {
        console.error('Erreur lors du chargement des produits:', error);
    }
}

// Ajouter une ligne via code-barres (produit ou variante)
async function addItemByBarcode() {
    const input = document.getElementById('invoiceBarcodeInput');
    const code = (input?.value || '').trim();
    if (!code) {
        showWarning('Veuillez saisir/scanner un code-barres');
        return;
    }
    // 1) lookup local cache
    let hit = window._productByBarcode && window._productByBarcode.get(code);
    // 2) fallback server
    if (!hit) {
        try {
            const res = await fetch(`/api/products/scan/${encodeURIComponent(code)}`, { credentials: 'include' });
            if (res.ok) {
                const data = await res.json();
                if (data && data.product_id) {
                    const prod = products.find(p => p.product_id === data.product_id);
                    hit = prod ? { ...prod } : { product_id: data.product_id, name: data.product_name, price: data.price };
                    if (data.variant) hit._variant = { variant_id: data.variant.variant_id, imei_serial: data.variant.imei_serial };
                }
            }
        } catch (e) { /* ignore */ }
    }
    if (!hit) {
        showError('Produit non trouvé');
        return;
    }

    // Si variante détectée, regrouper par produit et accumuler IMEIs
    if (hit._variant && hit._variant.imei_serial) {
        const imeiNorm = _normalizeCode(hit._variant.imei_serial);
        const existing = invoiceItems.find(i => i.product_id === hit.product_id);
        if (existing) {
            existing.scannedImeis = existing.scannedImeis || [];
            // si IMEI déjà dans la ligne, ne pas incrémenter la quantité
            if (existing.scannedImeis.some(x => _normalizeCode(x) === imeiNorm)) {
                showWarning('Cette variante/IMEI est déjà scannée');
            } else {
                existing.quantity = (existing.scannedImeis.length + 1);
                existing.total = existing.quantity * existing.unit_price;
                existing.scannedImeis.push(hit._variant.imei_serial);
                // supprimer tout identifiant de variante unique pour rester en "groupe produit"
                existing.variant_id = null;
                existing.variant_imei = null;
            }
        } else {
            invoiceItems.push({
                id: Date.now(),
                product_id: hit.product_id,
                product_name: hit.name,
                variant_id: null,
                variant_imei: null,
                scannedImeis: [hit._variant.imei_serial],
                quantity: 1,
                unit_price: Math.round(Number(hit.price) || 0),
                total: Math.round(Number(hit.price) || 0)
            });
        }
        // Fusionner si plusieurs lignes de même produit existent
        mergeProductRows();
    } else {
        // Produit simple: regrouper par produit sans variante
        const existing = invoiceItems.find(i => i.product_id === hit.product_id && !i.variant_id);
        if (existing) {
            existing.quantity += 1;
            existing.total = existing.quantity * existing.unit_price;
        } else {
            invoiceItems.push({
                id: Date.now(),
                product_id: hit.product_id,
                product_name: hit.name,
                variant_id: null,
                variant_imei: null,
                scannedImeis: [],
                quantity: 1,
                unit_price: Math.round(Number(hit.price) || 0),
                total: Math.round(Number(hit.price) || 0)
            });
        }
    }
    updateInvoiceItemsDisplay();
    calculateTotals();
    if (input) input.value = '';
}

// Fusionner les lignes de même produit en une seule avec IMEIs cumulés
// IMPORTANT: préserve l'ordre original des items (sections, custom items restent à leur place)
function mergeProductRows() {
    const groups = new Map();
    // Grouper par product_id, mais garder l'index de la première occurrence
    invoiceItems.forEach((row, idx) => {
        const key = String(row.product_id || '');
        if (!key) return; // Pas de product_id = section ou custom sans produit
        if (!groups.has(key)) groups.set(key, { firstIndex: idx, rows: [] });
        groups.get(key).rows.push(row);
    });
    
    // Créer un tableau avec les items fusionnés, en préservant l'ordre
    const merged = [];
    const processedProductIds = new Set();
    
    invoiceItems.forEach((row, idx) => {
        const key = String(row.product_id || '');
        
        // Item sans product_id (section, custom item vide) : garder tel quel
        if (!key) {
            merged.push(row);
            return;
        }
        
        // Déjà traité ce product_id
        if (processedProductIds.has(key)) return;
        processedProductIds.add(key);
        
        const group = groups.get(key);
        if (!group) return;
        
        const rows = group.rows;
        if (rows.length === 1) {
            merged.push(rows[0]);
            return;
        }
        
        // Fusionner les lignes du même produit
        const base = { ...rows[0] };
        base.scannedImeis = ([]).concat(...rows.map(r => r.scannedImeis || []));
        // dédoublonner IMEIs
        const uniq = Array.from(new Set(base.scannedImeis.map(_normalizeCode)));
        base.scannedImeis = uniq;
        base.variant_id = null;
        base.variant_imei = null;
        if (base.scannedImeis.length > 0) {
            base.quantity = base.scannedImeis.length;
        } else {
            base.quantity = rows.reduce((s, r) => s + (Number(r.quantity) || 0), 0);
        }
        base.total = base.quantity * (Number(base.unit_price) || 0);
        merged.push(base);
    });
    
    invoiceItems = merged;
}

// Remplir le select des clients
function populateClientSelect() {
    const clientSelect = document.getElementById('clientSelect');
    if (!clientSelect) return;

    // Si c'est un input hidden, on ne remplit rien (compatibilité ancienne UI)
    if (clientSelect.tagName && clientSelect.tagName.toLowerCase() === 'select') {
        clientSelect.innerHTML = '<option value="">Sélectionner un client</option>';
        clients.forEach(client => {
            const option = document.createElement('option');
            option.value = client.client_id;
            option.textContent = client.name;
            clientSelect.appendChild(option);
        });
    }
}

// Recherche client avec autocomplétion (améliorée: dropdown au focus + filtrage live)
function setupClientSearch() {
    const searchInput = document.getElementById('clientSearch');
    const resultsBox = document.getElementById('clientSearchResults');
    if (!searchInput || !resultsBox) return;

    const closeResults = () => { resultsBox.style.display = 'none'; };

    // Conserver la dernière liste de résultats pour la sélection
    let _latestClientResults = [];

    const renderList = async (term) => {
        const t = String(term || '').trim();
        try {
            // server-side search, limited
            const { data } = await axios.get('/api/clients/', { params: { search: t || undefined, limit: 8 } });
            const list = Array.isArray(data) ? data : (data.items || []);
            _latestClientResults = list || [];
            if (!list.length) {
                resultsBox.innerHTML = '<div class="list-group-item text-muted small">Aucun client</div>';
            } else {
                resultsBox.innerHTML = list.map(c => `
                    <button type="button" class="list-group-item list-group-item-action" data-client-id="${c.client_id}">
                        <div class="d-flex justify-content-between align-items-center">
                            <div>
                                <strong>${escapeHtml(c.name || '')}</strong>
                                ${c.email ? `<div class=\"text-muted small\">${escapeHtml(c.email)}</div>` : ''}
                            </div>
                            ${c.phone ? `<small class=\"text-muted\">${escapeHtml(c.phone)}</small>` : ''}
                        </div>
                    </button>
                `).join('');
            }
            resultsBox.style.display = 'block';
        } catch (e) {
            resultsBox.innerHTML = '<div class="list-group-item text-muted small">Erreur de chargement</div>';
            resultsBox.style.display = 'block';
        }
    };

    // Ouvrir au focus (même sans recherche)
    searchInput.addEventListener('focus', () => {
        renderList(searchInput.value);
    });

    // Filtrage live au fil de la frappe
    searchInput.addEventListener('input', debounce(function(e){
        const inputVal = (e && e.target && typeof e.target.value === 'string') ? e.target.value : (searchInput.value || '');
        renderList(inputVal);
    }, 200));

    // Sélection par clic
    resultsBox.addEventListener('click', function(e){
        const btn = e.target.closest('[data-client-id]');
        if (!btn) return;
        const id = Number(btn.getAttribute('data-client-id'));
        // Chercher d'abord dans les derniers résultats, sinon fallback global
        const c = (_latestClientResults || []).find(x => Number(x.client_id) === id) || (clients || []).find(x => Number(x.client_id) === id);
        if (c) {
            selectClient(c.client_id, c.name);
        } else {
            // Fallback minimal si aucune info (remplit juste l'id)
            selectClient(id, '');
        }
    });

    // Navigation clavier
    searchInput.addEventListener('keydown', function(e){
        const items = resultsBox.querySelectorAll('.list-group-item');
        const active = resultsBox.querySelector('.list-group-item.active');
        let idx = Array.from(items).indexOf(active);
        if (e.key === 'ArrowDown') {
            e.preventDefault();
            if (idx < items.length - 1) { if (active) active.classList.remove('active'); items[idx+1]?.classList.add('active'); }
            else if (idx === -1 && items.length) { items[0].classList.add('active'); }
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            if (idx > 0) { if (active) active.classList.remove('active'); items[idx-1]?.classList.add('active'); }
        } else if (e.key === 'Enter') {
            e.preventDefault();
            if (active) {
                active.click();
            } else if (items.length) {
                items[0].click();
            }
        }
    });

    // Clic extérieur pour fermer
    document.addEventListener('click', function(e){
        if (!searchInput.contains(e.target) && !resultsBox.contains(e.target)) {
            closeResults();
        }
    });
}

function selectClient(clientId, clientName) {
    const hidden = document.getElementById('clientSelect');
    const input = document.getElementById('clientSearch');
    const resultsBox = document.getElementById('clientSearchResults');
    if (hidden) hidden.value = String(clientId || '');
    if (input) input.value = clientName || '';
    if (resultsBox) resultsBox.style.display = 'none';
}

// Afficher les factures
function displayInvoices() {
    const tbody = document.getElementById('invoicesTableBody');
    if (!tbody) return;

    if (filteredInvoices.length === 0) {
        showEmptyState();
        return;
    }

    // Server-side pagination: render the page as returned
    const invoicesToShow = filteredInvoices;

    tbody.innerHTML = invoicesToShow.map(invoice => {
        // Utiliser directement le nom du client retourné par l'API
        const clientName = invoice.client_name || '';
        const invDate = invoice.date || invoice.invoice_date;
        const due = invoice.due_date || invoice.dueDate;
        const total = Number((invoice.total ?? invoice.total_amount) || 0);
        const isPaid = String(invoice.status).toUpperCase() === 'PAID';
        return `
        <tr>
            <td>
                <strong>${escapeHtml(invoice.invoice_number)}</strong>
            </td>
            <td>${escapeHtml(clientName || '-')}</td>
            <td>${invDate ? formatDate(invDate) : '-'}</td>
            <td>${due ? formatDate(due) : '-'}</td>
            <td><strong>${formatCurrency(total)}</strong></td>
            <td>
                <span class="badge bg-${getInvoiceStatusBadgeColor(invoice.status)}">
                    ${getInvoiceStatusLabel(invoice.status)}
                </span>
            </td>
            <td>
				<div class="btn-group" role="group">
                    <button class="btn btn-sm btn-outline-info" onclick="viewInvoice(${invoice.invoice_id})" title="Voir">
                        <i class="bi bi-eye"></i>
                    </button>
                    <button class="btn btn-sm btn-outline-primary" onclick="editInvoice(${invoice.invoice_id})" title="Modifier">
                        <i class="bi bi-pencil"></i>
                    </button>
                    ${invoice.quotation_id ? `
                        <button class="btn btn-sm btn-outline-warning" onclick="viewQuotation(${invoice.quotation_id})" title="Voir le devis d'origine">
                            <i class="bi bi-file-text"></i>
                        </button>
                    ` : ''}
                    ${!isPaid ? `
                        <button class="btn btn-sm btn-outline-success" onclick="addPayment(${invoice.invoice_id})" title="Paiement">
                            <i class="bi bi-credit-card"></i>
                        </button>
                    ` : ''}
					<button class="btn btn-sm btn-outline-secondary" onclick="generateDeliveryNote(${invoice.invoice_id})" title="Générer BL">
						<i class="bi bi-truck"></i>
					</button>
                    <button class="btn btn-sm btn-outline-secondary" onclick="printInvoice(${invoice.invoice_id})" title="Imprimer">
                        <i class="bi bi-printer"></i>
                    </button>
                    <button class="btn btn-sm btn-outline-secondary" onclick="duplicateInvoice(${invoice.invoice_id})" title="Dupliquer">
                        <i class="bi bi-copy"></i>
                    </button>
                    <button class="btn btn-sm btn-outline-danger" onclick="deleteInvoice(${invoice.invoice_id})" title="Supprimer">
                        <i class="bi bi-trash"></i>
                    </button>
                </div>
            </td>
        </tr>
    `}).join('');
}

function showEmptyState() {
    const tbody = document.getElementById('invoicesTableBody');
    if (tbody) {
        tbody.innerHTML = `
            <tr>
                <td colspan="7" class="text-center text-muted py-4">
                    <i class="bi bi-inbox fs-1 d-block mb-2"></i>
                    Aucune facture trouvée
                </td>
            </tr>
        `;
    }
}

function showLoading() {
    // No visual loading indicator to keep UI instant
}

// Utilitaires pour les statuts de factures
function getInvoiceStatusBadgeColor(status) {
    const s = String(status || '').trim();
    const sLower = s.toLowerCase();
    // Normalized groups
    if (s === 'PAID' || sLower === 'payee' || sLower === 'payée' || sLower === 'paye' || sLower === 'payé') {
        // Green for paid
        return 'success';
    }
    if (s === 'OVERDUE' || sLower === 'en retard') {
        // Treat overdue as red to indicate attention
        return 'danger';
    }
    if (s === 'CANCELLED' || sLower === 'annulee' || sLower === 'annulée' || sLower === 'annule' || sLower === 'annulé') {
        // Red for cancelled
        return 'danger';
    }
    if (sLower === 'partiellement payee' || sLower === 'partiellement payée' || sLower === 'partially paid' || sLower === 'partially_paid') {
        // Yellow for partially paid
        return 'warning';
    }
    if (s === 'SENT' || s === 'DRAFT' || sLower === 'en attente' || sLower === 'pending' || sLower === 'brouillon' || sLower === 'envoyée' || sLower === 'envoyee') {
        // Red for pending per requirement
        return 'danger';
    }
    return 'secondary';
}

function getInvoiceStatusLabel(status) {
    const s = String(status || '').trim();
    const sLower = s.toLowerCase();
    switch (s) {
        case 'DRAFT': return 'Brouillon';
        case 'SENT': return 'Envoyée';
        case 'PAID': return 'Payée';
        case 'OVERDUE': return 'En retard';
        case 'CANCELLED': return 'Annulée';
    }
    // Handle French lowercase statuses
    if (sLower === 'en attente' || sLower === 'pending') return 'En attente';
    if (sLower === 'payee' || sLower === 'payée' || sLower === 'paye' || sLower === 'payé') return 'Payée';
    if (sLower === 'partiellement payee' || sLower === 'partiellement payée' || sLower === 'partially paid' || sLower === 'partially_paid') return 'Partiellement payée';
    if (sLower === 'en retard' || sLower === 'overdue') return 'En retard';
    if (sLower === 'annulee' || sLower === 'annulée' || sLower === 'annule' || sLower === 'annulé' || sLower === 'cancelled') return 'Annulée';
    if (sLower === 'brouillon' || sLower === 'draft') return 'Brouillon';
    if (sLower === 'envoyee' || sLower === 'envoyée' || sLower === 'sent') return 'Envoyée';
    return s;
}

// Filtrer les factures
// Conservé pour compat, mais désormais on recharge côté serveur
function filterInvoices() { currentPage = 1; loadInvoices(); }

// Pagination
function updatePagination(totalCount) {
    const count = Number.isFinite(totalCount) ? Number(totalCount) : filteredInvoices.length;
    const totalPages = Math.ceil(count / itemsPerPage);
    const paginationContainer = document.getElementById('pagination-container');
    
    if (!paginationContainer || totalPages <= 1) {
        if (paginationContainer) paginationContainer.innerHTML = '';
        return;
    }

    let paginationHTML = '<nav><ul class="pagination justify-content-center">';
    
    // Bouton précédent
    paginationHTML += `
        <li class="page-item ${currentPage === 1 ? 'disabled' : ''}">
            <a class="page-link" href="#" onclick="changePage(${currentPage - 1})">Précédent</a>
        </li>
    `;
    
    // Numéros de page
    for (let i = 1; i <= totalPages; i++) {
        if (i === 1 || i === totalPages || (i >= currentPage - 2 && i <= currentPage + 2)) {
            paginationHTML += `
                <li class="page-item ${i === currentPage ? 'active' : ''}">
                    <a class="page-link" href="#" onclick="changePage(${i})">${i}</a>
                </li>
            `;
        } else if (i === currentPage - 3 || i === currentPage + 3) {
            paginationHTML += '<li class="page-item disabled"><span class="page-link">...</span></li>';
        }
    }
    
    // Bouton suivant
    paginationHTML += `
        <li class="page-item ${currentPage === totalPages ? 'disabled' : ''}">
            <a class="page-link" href="#" onclick="changePage(${currentPage + 1})">Suivant</a>
        </li>
    `;
    
    paginationHTML += '</ul></nav>';
    paginationContainer.innerHTML = paginationHTML;
}

function changePage(page) {
    const totalPagesEl = document.getElementById('pagination-container');
    if (page >= 1) {
        currentPage = page;
        loadInvoices();
    }
}

// Ouvrir le modal pour nouvelle facture
function openInvoiceModal() {
    try {
        // Vérifier l'existence des éléments essentiels
        const modalEl = document.getElementById('invoiceModal');
        if (!modalEl) {
            console.error('Modal #invoiceModal introuvable dans le DOM');
            if (typeof showError === 'function') {
                showError('Erreur: formulaire de facture introuvable');
            }
            return;
        }
        // Charger les produits dès l'ouverture si la liste est vide
        try { if (!Array.isArray(products) || products.length === 0) { loadProducts().catch(()=>{}); } } catch(e) {}

        const titleEl = document.getElementById('invoiceModalTitle');
        const formEl = document.getElementById('invoiceForm');
        const idEl = document.getElementById('invoiceId');
        const numberEl = document.getElementById('invoiceNumber');

        // Titre du modal
        if (titleEl) {
            titleEl.innerHTML = '<i class="bi bi-plus-circle me-2"></i>Nouvelle Facture';
        }
        
        // Reset du formulaire
        if (formEl) {
            formEl.reset();
            try {
                formEl.dataset.quotationId = '';
            } catch(e) {}
        }
        
        if (idEl) {
            idEl.value = '';
        }
        
        // Reset client search/hidden
        try {
            const hidden = document.getElementById('clientSelect');
            const input = document.getElementById('clientSearch');
            const box = document.getElementById('clientSearchResults');
            if (hidden) hidden.value = '';
            if (input) input.value = '';
            if (box) box.style.display = 'none';
        } catch(e) {}
        
        setDefaultDates();
        
        // Pré-remplir un numéro de facture fiable depuis le serveur
        if (numberEl) {
            try {
                numberEl.value = '';
                numberEl.placeholder = 'Chargement du numéro...';
                axios.get('/api/invoices/next-number').then(({ data }) => {
                    if (data && data.invoice_number) {
                        numberEl.value = data.invoice_number;
                        numberEl.placeholder = '';
                    } else {
                        // Si l'API ne renvoie rien, laisser vide: le serveur générera automatiquement
                        numberEl.value = '';
                        numberEl.placeholder = 'Sera généré automatiquement';
                    }
                }).catch((error) => {
                    console.error('Erreur lors du chargement du numéro de facture:', error);
                    // En cas d'erreur API, laisser vide pour laisser le backend générer
                    numberEl.value = '';
                    numberEl.placeholder = 'Sera généré automatiquement';
                });
            } catch(e) { /* ignore */ }
        }
        
        // Démarrer avec une liste vide; l'utilisateur scanne pour ajouter
        invoiceItems = [];
        // Réinitialiser les quantités de devis
        quoteQtyByProductId = new Map();
        updateInvoiceItemsDisplay();
        
        // Reset méthode de paiement
        try {
            const pmSel = document.getElementById('invoicePaymentMethod');
            if (pmSel) pmSel.value = '';
        } catch (e) {}
        
        // Defaults for TVA UI
        const taxSwitch = document.getElementById('showTaxSwitch');
        const taxRateInput = document.getElementById('taxRateInput');
        if (taxSwitch) taxSwitch.checked = true;
        if (taxRateInput) taxRateInput.value = 18;
        
        // Reset garantie
        const warrantySwitch = document.getElementById('hasWarrantySwitch');
        const warrantyOptions = document.getElementById('warrantyOptions');
        const warrantyInfo = document.getElementById('warrantyInfo');
        if (warrantySwitch) warrantySwitch.checked = false;
        if (warrantyOptions) warrantyOptions.style.display = 'none';
        if (warrantyInfo) warrantyInfo.style.display = 'none';
        // Sélectionner 12 mois par défaut
        const warranty12 = document.getElementById('warranty12months');
        if (warranty12) warranty12.checked = true;
        
        // Ensure listeners are bound in case modal was created after initial setup
        setupTaxControls();
        setupWarrantyControls();
        calculateTotals();

        // Recharger les méthodes de paiement lors de l'ouverture du modal
        populatePaymentMethodSelects(true);

        // Setup signature pad
        try {
            const canvas = document.getElementById('signatureCanvas');
            if (canvas && canvas.getContext) {
                const ctx = canvas.getContext('2d');
                let drawing = false; let last = null;
                const getPos = (e) => {
                    const rect = canvas.getBoundingClientRect();
                    const x = (e.touches ? e.touches[0].clientX : e.clientX) - rect.left;
                    const y = (e.touches ? e.touches[0].clientY : e.clientY) - rect.top;
                    return { x, y };
                };
                const start = (e) => { drawing = true; last = getPos(e); };
                const move = (e) => {
                    if (!drawing) return;
                    const pos = getPos(e);
                    ctx.beginPath();
                    ctx.moveTo(last.x, last.y);
                    ctx.lineTo(pos.x, pos.y);
                    ctx.strokeStyle = '#111';
                    ctx.lineWidth = 2;
                    ctx.lineCap = 'round';
                    ctx.stroke();
                    last = pos;
                    e.preventDefault();
                };
                const end = () => { drawing = false; };
                canvas.addEventListener('mousedown', start);
                canvas.addEventListener('mousemove', move);
                window.addEventListener('mouseup', end);
                canvas.addEventListener('touchstart', start, { passive: false });
                canvas.addEventListener('touchmove', move, { passive: false });
                canvas.addEventListener('touchend', end);
                
                const clearBtn = document.getElementById('signatureClearBtn');
                if (clearBtn) {
                    clearBtn.addEventListener('click', () => {
                        ctx.clearRect(0, 0, canvas.width, canvas.height);
                    });
                }
            }
        } catch (e) {
            console.warn('Erreur initialisation signature pad:', e);
        }

        // Afficher le modal - version sécurisée
        try {
            if (typeof bootstrap !== 'undefined' && bootstrap.Modal) {
                const modalInstance = bootstrap.Modal.getOrCreateInstance(modalEl);
                if (modalInstance && typeof modalInstance.show === 'function') {
                    modalInstance.show();
                } else {
                    console.warn('Instance modal Bootstrap invalide');
                }
            } else {
                console.error('Bootstrap Modal non disponible');
            }
        } catch (e) {
            console.error('Erreur ouverture modal:', e);
            if (typeof showError === 'function') {
                showError('Erreur lors de l\'ouverture du formulaire de facture');
            }
        }
        
    } catch (error) {
        console.error('Erreur dans openInvoiceModal:', error);
        if (typeof showError === 'function') {
            showError('Erreur lors de l\'ouverture du formulaire de facture');
        }
    }
}

// Pré-chargement facture depuis un devis
async function preloadPrefilledInvoiceFromQuotation(prefill) {
    openInvoiceModal();
    document.getElementById('invoiceModalTitle').innerHTML = '<i class="bi bi-receipt me-2"></i>Facture depuis devis ' + (prefill?.quotation_number || '');
    try { if (prefill?.quotation_id) document.getElementById('invoiceForm').dataset.quotationId = String(prefill.quotation_id); } catch(e) {}
    // Client
    try {
        const clientSel = document.getElementById('clientSelect');
        if (clientSel && prefill?.client_id) clientSel.value = prefill.client_id;
        const input = document.getElementById('clientSearch');
        let c = (clients || []).find(x => Number(x.client_id) === Number(prefill?.client_id));
        if (!c && prefill?.client_id) {
            try {
                const { data } = await axios.get(`/api/clients/${prefill.client_id}`);
                if (data) {
                    c = data;
                    try {
                        if (!Array.isArray(clients)) clients = [];
                        if (!clients.some(x => Number(x.client_id) === Number(data.client_id))) clients.push(data);
                    } catch(e) {}
                }
            } catch(e) {}
        }
        if (input) input.value = c ? (c.name || '') : (prefill?.client_name || '');
    } catch(e) {}
    // Date: pour une conversion, utiliser la date du jour (ne pas réutiliser la date du devis)
    try {
        const invDate = document.getElementById('invoiceDate');
        if (invDate) {
            const today = new Date().toISOString().split('T')[0];
            invDate.value = today;
        }
    } catch(e) {}
    // Articles
    invoiceItems = [];
    const _quoteAgg = new Map();
    (prefill?.items || []).forEach(it => {
        // Normalize product_id and detect custom lines coming from quotation
        const rawPid = it.product_id;
        const rawStr = String(rawPid);
        let isMissingPid = (rawPid === null || rawPid === undefined || rawStr === '' || rawStr.toLowerCase() === 'null' || rawStr.toLowerCase() === 'none' || Number(rawPid) === 0);
        // If prefill explicitly flags as custom, force it
        let forceCustom = !!it.is_custom;
        // If a pid is present but doesn't match any known product, treat as custom
        let normPid = (isMissingPid || forceCustom) ? '' : rawPid;
        try {
            const exists = products && products.some(p => Number(p.product_id) === Number(normPid));
            if (!exists) { isMissingPid = true; normPid = ''; }
        } catch(e) {}
        const hasVariants = (productVariantsByProductId.get(Number(normPid)) || []).length > 0;
        invoiceItems.push({
            id: Date.now() + Math.random(),
            product_id: normPid,
            product_name: (isMissingPid || forceCustom) ? (it.product_name || 'Service personnalisé') : it.product_name,
            is_custom: (forceCustom || isMissingPid) ? true : false,
            variant_id: null,
            variant_imei: null,
            scannedImeis: [],
            quantity: hasVariants ? Number(it.quantity || 0) : Number(it.quantity || 1),
            unit_price: Math.round(Number(it.price || 0)),
            total: Math.round(Number(it.total || 0))
        });
        // Agréger quantité demandée dans le devis pour info UI
        try {
            const pid = Number(it.product_id);
            const qty = Number(it.quantity || 0);
            if (!Number.isNaN(pid)) _quoteAgg.set(pid, (_quoteAgg.get(pid) || 0) + qty);
        } catch(e) {}
    });
    // Exposer les quantités du devis dans l'UI (affichage "Qté devis: X")
    quoteQtyByProductId = _quoteAgg;
    // Afficher X emplacements IMEI vides pour variants: on garde la quantité et on laisse l'utilisateur sélectionner
    updateInvoiceItemsDisplay();
    calculateTotals();
}

// Gestion des articles de facture
function addInvoiceItem() {
    const newItem = {
        id: Date.now(),
        product_id: '',
        product_name: '',
        variant_id: null,
        variant_imei: null,
        quantity: 1,
        unit_price: 0,
        total: 0
    };
    
    invoiceItems.push(newItem);
    // S'assurer que les produits sont chargés pour le select
    try { if (!Array.isArray(products) || products.length === 0) { loadProducts().catch(()=>{}); } } catch(e) {}
    updateInvoiceItemsDisplay();
}

// Ajouter une ligne libre/service (sans produit, propre à la facture)
function addCustomItem() {
    const newItem = {
        id: Date.now(),
        product_id: '',
        product_name: 'Service personnalisé',
        is_custom: true,
        variant_id: null,
        variant_imei: null,
        scannedImeis: [],
        quantity: 1,
        unit_price: 0,
        total: 0
    };
    invoiceItems.push(newItem);
    updateInvoiceItemsDisplay();
}

// Ajouter une section (titre uniquement, sans quantité ni prix)
function addSectionRow() {
    const newItem = {
        id: Date.now(),
        is_section: true,
        section_title: 'Nouvelle section',
        product_id: null,
        product_name: '',
        variant_id: null,
        variant_imei: null,
        scannedImeis: [],
        quantity: 0,
        unit_price: 0,
        total: 0
    };
    invoiceItems.push(newItem);
    updateInvoiceItemsDisplay();
}

// Mettre à jour le titre d'une section
function updateSectionTitle(itemId, title) {
    const item = invoiceItems.find(i => i.id === itemId);
    if (!item) return;
    item.section_title = String(title || '').trim();
}

// Exposer les fonctions pour les attributs onclick en HTML
window.addSectionRow = addSectionRow;
window.updateSectionTitle = updateSectionTitle;

function updateInvoiceItemsDisplay() {
    const tbody = document.getElementById('invoiceItemsBody');
    if (!tbody) return;

    if (invoiceItems.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="8" class="text-center text-muted py-3">
                    <i class="bi bi-inbox me-2"></i>Aucun article ajouté
                </td>
            </tr>
        `;
        calculateTotals();
        return;
    }

    const selectedProductIds = new Set(invoiceItems.map(r => Number(r.product_id)).filter(Boolean));
    tbody.innerHTML = invoiceItems.map((item, index) => {
        // Ligne de section: juste un titre, pas de quantité ni prix
        if (item.is_section) {
            const title = escapeHtml(String(item.section_title || 'Nouvelle section').trim());
            return `
        <tr data-item-id="${item.id}" class="table-secondary">
            <td class="text-center align-middle drag-handle" style="cursor:grab;" title="Glisser pour réordonner">
                <i class="bi bi-grip-vertical text-muted fs-5"></i>
            </td>
            <td colspan="6">
                <input type="text" class="form-control form-control-sm fw-bold" value="${title}"
                       placeholder="Nom de section (ex: Matériel, Main d'œuvre)"
                       oninput="updateSectionTitle(${item.id}, this.value)">
            </td>
            <td>
                <button class="btn btn-sm btn-outline-danger" onclick="removeInvoiceItem(${item.id})">
                    <i class="bi bi-trash"></i>
                </button>
            </td>
        </tr>`;
        }
        // Ligne standard (produit ou service custom)
        return `
        <tr data-item-id="${item.id}">
            <td class="text-center align-middle drag-handle" style="cursor:grab;" title="Glisser pour réordonner">
                <i class="bi bi-grip-vertical text-muted fs-5"></i>
            </td>
            <td>
                ${item.is_custom ? `
                <input type="text" class="form-control form-control-sm" value="${escapeHtml(item.product_name || '')}" placeholder="Libellé (ex: Service d'installation)" oninput="updateCustomName(${item.id}, this.value)">
                ` : `
                <div class="position-relative" style="min-width: 24rem; z-index: 2000;">
                    <div class="input-group input-group-sm">
                        <input type="text" class="form-control form-control-sm product-search-input" placeholder="Nom, code-barres ou n° série..." data-item-id="${item.id}" />
                        <select class="form-select form-select-sm" onchange="selectProduct(${item.id}, this.value)">
                            <option value="">Sélectionner un produit</option>
                            ${(() => {
                                const productList = Array.isArray(products) && products.length
                                    ? products
                                    : (Array.isArray(window._latestProductResults) ? window._latestProductResults : []);
                                // Si le produit actuel n'est pas dans la liste, l'ajouter en premier
                                const currentProductInList = item.product_id && productList.some(p => p.product_id == item.product_id);
                                let options = '';
                                if (item.product_id && !currentProductInList) {
                                    options += `<option value="${item.product_id}" selected>${escapeHtml(item.product_name || 'Produit #' + item.product_id)} - ${formatCurrency(item.unit_price)}</option>`;
                                }
                                options += productList.map(product => {
                                    const variants = productVariantsByProductId.get(Number(product.product_id)) || [];
                                    const available = variants.length > 0 ? variants.filter(v => !v.is_sold).length : Number(product.quantity || 0);
                                    const alreadySelected = selectedProductIds.has(Number(product.product_id)) && Number(product.product_id) !== Number(item.product_id);
                                    const isOutOfStock = available === 0;
                                    const disabled = alreadySelected || isOutOfStock;
                                    return `
                                <option value="${product.product_id}" ${product.product_id == item.product_id ? 'selected' : ''} ${disabled ? 'disabled' : ''}>
                                    ${escapeHtml(product.name)} - ${formatCurrency(product.price)}${product.wholesale_price ? ` / Gros: ${formatCurrency(product.wholesale_price)}` : ''} ${isOutOfStock ? '(épuisé)' : `(Stock: ${available})`} ${alreadySelected ? '(déjà sélectionné)' : ''}
                                </option>`;
                                }).join('');
                                return options;
                            })()}
                        </select>
                    </div>
                    <div class="product-suggestions list-group d-none" style="position:absolute; left:0; right:0; top:100%; z-index:3000; max-height:240px; overflow:auto; width:100%; background:#fff; border:1px solid rgba(0,0,0,.125); border-radius:.25rem; box-shadow:0 2px 6px rgba(0,0,0,.15);"></div>
                </div>
                `}
                ${(() => {
                    const list = item.scannedImeis || [];
                    if (!list.length) return '';
                    const variants = productVariantsByProductId.get(Number(item.product_id)) || [];
                    return `<div class=\"small text-muted mt-1\">IMEI: ` + list.map((im, idx) => {
                        const v = variants.find(x => _normalizeCode(x.imei_serial) === _normalizeCode(im));
                        const condPart = (v && v.condition)
                            ? ' (' + escapeHtml(String(v.condition).charAt(0).toUpperCase() + String(v.condition).slice(1)) + ')'
                            : '';
                        return `
                        <span class=\"badge bg-light text-dark border me-1\">${escapeHtml(im)}${condPart}
                            <a href=\"#\" class=\"text-danger text-decoration-none ms-1\" onclick=\"removeScannedImeiAt(${item.id}, ${idx}); return false;\">&times;</a>
                        </span>`;
                    }).join('') + `<a href=\"#\" class=\"ms-1\" onclick=\"clearScannedImeis(${item.id}); return false;\">Tout supprimer</a></div>`;
                })()}
                ${(() => { try { const q = quoteQtyByProductId.get(Number(item.product_id)); return (typeof q === 'number') ? `<div class=\"small text-muted mt-1\">Qté devis: ${q}</div>` : ''; } catch(e){ return ''; } })()}
                ${(() => { try { const p = (products||[]).find(pp => Number(pp.product_id) === Number(item.product_id)) || (Array.isArray(window._latestProductResults)? window._latestProductResults.find(pp => Number(pp.product_id) === Number(item.product_id)) : null); if (!p) return ''; const stock = computeAvailableStock(p); const cls = stock>0 ? 'text-success' : 'text-danger'; return `<div class=\"small ${cls} mt-1\">Stock: ${stock}</div>`; } catch(e){ return ''; } })()}
            </td>
            <td>
                <select class="form-select form-select-sm" onchange="selectVariant(${item.id}, this.value)" ${(item.is_custom || !item.product_id) ? 'disabled' : ''}>
                    <option value="">(optionnel) Variante / IMEI</option>
                    ${renderVariantOptions(item.product_id, item.variant_id)}
                </select>
            </td>
            <td>
                <input type="number" class="form-control form-control-sm" value="${item.quantity}" min="0"
                       ${item.is_custom ? '' : (((productVariantsByProductId.get(Number(item.product_id)) || []).length > 0) ? 'disabled' : '')}
                       onchange="updateItemQuantity(${item.id}, this.value)">
            </td>
            <td>
                ${!item.is_custom && item.product_id ? (() => {
                    const product = (products||[]).find(p => p.product_id == item.product_id) || (Array.isArray(window._latestProductResults)? window._latestProductResults.find(p => p.product_id == item.product_id) : null);
                    const hasWholesale = product && product.wholesale_price;
                    return hasWholesale ? `
                        <select class="form-select form-select-sm" onchange="togglePriceType(${item.id}, this.value === 'wholesale')">
                            <option value="unit" ${item.price_type !== 'wholesale' ? 'selected' : ''}>Unitaire</option>
                            <option value="wholesale" ${item.price_type === 'wholesale' ? 'selected' : ''}>Gros</option>
                        </select>
                    ` : '<small class="text-muted">-</small>';
                })() : '<small class="text-muted">-</small>'}
            </td>
            <td>
                <input type="text" class="form-control form-control-sm" value="${(item.unit_price).toLocaleString('fr-FR') }"
                       oninput="handlePriceInput(${item.id}, this)"
                       onchange="handlePriceInput(${item.id}, this)">
            </td>
            <td><strong class="row-total">${formatCurrency(item.total)}</strong></td>
            <td>
                <button class="btn btn-sm btn-outline-danger" onclick="removeInvoiceItem(${item.id})">
                    <i class="bi bi-trash"></i>
                </button>
            </td>
        </tr>
    `;
    }).join('');
    // Ensure totals reflect any DOM-driven changes
    calculateTotals();
    // Initialiser le drag-and-drop si SortableJS est disponible
    initSortable();
}

// Initialiser SortableJS pour le drag-and-drop des lignes
function initSortable() {
    const tbody = document.getElementById('invoiceItemsBody');
    if (!tbody || typeof Sortable === 'undefined') return;
    // Détruire l'instance précédente si elle existe
    if (tbody._sortableInstance) {
        try { tbody._sortableInstance.destroy(); } catch(e) {}
    }
    tbody._sortableInstance = new Sortable(tbody, {
        animation: 150,
        handle: '.drag-handle',
        ghostClass: 'table-primary',
        chosenClass: 'table-info',
        onEnd: function(evt) {
            // Réordonner invoiceItems selon le nouvel ordre du DOM
            const rows = Array.from(tbody.querySelectorAll('tr[data-item-id]'));
            const newOrder = rows.map(row => Number(row.getAttribute('data-item-id')));
            const reordered = [];
            newOrder.forEach(id => {
                const item = invoiceItems.find(i => i.id === id);
                if (item) reordered.push(item);
            });
            // Ajouter les items qui n'auraient pas été trouvés (sécurité)
            invoiceItems.forEach(item => {
                if (!reordered.includes(item)) reordered.push(item);
            });
            invoiceItems = reordered;
        }
    });
}

function selectProduct(itemId, productId, useBulkPrice = false) {
    const item = invoiceItems.find(i => i.id === itemId);
    let product = products.find(p => String(p.product_id) == String(productId));
    // Fallback: utiliser les derniers résultats de recherche
    if (!product && Array.isArray(window._latestProductResults)) {
        product = window._latestProductResults.find(p => String(p.product_id) == String(productId));
    }
    // Si toujours pas trouvé, tenter un chargement ponctuel depuis l'API
    if (!product) {
        try {
            axios.get(`/api/products/${encodeURIComponent(productId)}`).then(({ data }) => {
                if (!data) return;
                // Mettre en cache local
                if (!products.some(p => Number(p.product_id) === Number(data.product_id))) {
                    products.push(data);
                }
                try { if (Array.isArray(data.variants)) productVariantsByProductId.set(Number(data.product_id), data.variants); } catch (e) {}
                // Appliquer la sélection
                selectProduct(itemId, data.product_id, useBulkPrice);
            }).catch(() => {
                // dernier recours: appliquer avec les infos minimales
                if (item) {
                    item.product_id = Number(productId);
                    item.product_name = item.product_name || '';
                    item.unit_price = Number(item.unit_price) || 0;
                    item.total = item.quantity * item.unit_price;
                    item.variant_id = null; item.variant_imei = null;
                    updateInvoiceItemsDisplay();
                    calculateTotals();
                }
            });
        } catch (e) {}
        return;
    }
    if (item && product) {
        item.product_id = product.product_id;
        item.product_name = product.name;
        // Choisir le prix: en gros si disponible et demandé, sinon unitaire
        item.unit_price = Math.round((useBulkPrice && product.wholesale_price) ? Number(product.wholesale_price) : Number(product.price) || 0);
        item.price_type = (useBulkPrice && product.wholesale_price) ? 'wholesale' : 'unit';
        item.total = item.quantity * item.unit_price;
        // Reset variant when product changes
        item.variant_id = null;
        item.variant_imei = null;
        // S'assurer que le produit est présent dans la liste locale pour l'affichage du <select>
        if (!products.some(p => Number(p.product_id) === Number(product.product_id))) {
            products.push(product);
        }
        // Précharger les variantes pour ce produit si fournies
        try {
            const variants = Array.isArray(product.variants) ? product.variants : [];
            if (variants.length) productVariantsByProductId.set(Number(product.product_id), variants);
        } catch (e) {}
        updateInvoiceItemsDisplay();
        calculateTotals();
    }
}

function togglePriceType(itemId, useBulkPrice) {
    const item = invoiceItems.find(i => i.id === itemId);
    if (!item || !item.product_id) return;
    selectProduct(itemId, item.product_id, useBulkPrice);
}

function updateCustomName(itemId, name) {
    const item = invoiceItems.find(i => i.id === itemId);
    if (!item) return;
    item.product_name = name || '';
}

function renderVariantOptions(productId, selectedVariantId) {
    if (!productId) return '';
    const variants = productVariantsByProductId.get(Number(productId)) || [];
    if (!variants.length) return '';
    // Ne proposer que les variantes disponibles (non vendues)
    return variants.filter(v => !v.is_sold).map(v => `
        <option value="${v.variant_id}" ${String(v.variant_id) === String(selectedVariantId || '') ? 'selected' : ''}>
            ${escapeHtml(v.imei_serial || v.barcode || ('Variante #' + v.variant_id))}
            ${v.condition ? '(' + escapeHtml(String(v.condition).charAt(0).toUpperCase() + String(v.condition).slice(1)) + ')' : ''}
        </option>
    `).join('');
}

function selectVariant(itemId, variantId) {
    const item = invoiceItems.find(i => i.id === itemId);
    if (!item) return;
    const variants = productVariantsByProductId.get(Number(item.product_id)) || [];
    const v = variants.find(x => String(x.variant_id) === String(variantId));
    if (v && v.is_sold) { showWarning('Cette variante est déjà vendue'); return; }
    const usedImeis = getUsedImeis(itemId);
    if (v && !usedImeis.has(_normalizeCode(v.imei_serial))) {
        item.scannedImeis = item.scannedImeis || [];
        if (!item.scannedImeis.some(x => _normalizeCode(x) === _normalizeCode(v.imei_serial))) {
            item.scannedImeis.push(v.imei_serial);
            // quantity becomes count of IMEIs
            item.quantity = item.scannedImeis.length;
            item.total = item.quantity * (Number(item.unit_price) || 0);
        }
    } else {
        if (v && usedImeis.has(_normalizeCode(v.imei_serial))) showWarning('Cette variante/IMEI est déjà scannée dans la facture');
    }
    // Keep dropdown option unselected to allow multiple adds
    const selectEl = document.querySelector(`select[onchange="selectVariant(${itemId}, this.value)"]`);
    if (selectEl) selectEl.value = '';
    // Merge same product rows
    mergeProductRows();
    updateInvoiceItemsDisplay();
}

// Retirer une IMEI spécifique déjà scannée dans la ligne (dé-sélection variante)
function removeScannedImeiAt(itemId, index) {
    const item = invoiceItems.find(i => i.id === itemId);
    if (!item) return;
    const list = item.scannedImeis || [];
    if (index < 0 || index >= list.length) return;
    list.splice(index, 1);
    item.scannedImeis = list;
    // Mettre à jour la quantité = nb IMEIs s'il s'agit d'un produit à variantes
    const hasVariants = (productVariantsByProductId.get(Number(item.product_id)) || []).length > 0;
    if (hasVariants) {
        item.quantity = (item.scannedImeis || []).length;
        item.total = item.quantity * (Number(item.unit_price) || 0);
    }
    mergeProductRows();
    updateInvoiceItemsDisplay();
}

// Vider toutes les IMEIs scannées pour la ligne
function clearScannedImeis(itemId) {
    const item = invoiceItems.find(i => i.id === itemId);
    if (!item) return;
    item.scannedImeis = [];
    const hasVariants = (productVariantsByProductId.get(Number(item.product_id)) || []).length > 0;
    if (hasVariants) {
        item.quantity = 0;
        item.total = 0;
    }
    mergeProductRows();
    updateInvoiceItemsDisplay();
}

function updateItemQuantity(itemId, quantity) {
    const item = invoiceItems.find(i => i.id === itemId);
    if (item) {
        const hasVariants = (productVariantsByProductId.get(Number(item.product_id)) || []).length > 0;
        if (hasVariants) {
            // lock quantity to number of scanned IMEIs for variant products
            const count = (item.scannedImeis || []).length;
            item.quantity = count > 0 ? count : 0;
        } else {
            const parsed = parseInt(quantity, 10);
            item.quantity = Number.isNaN(parsed) ? 0 : Math.max(0, parsed);
        }
        item.total = item.quantity * item.unit_price;
        
        updateInvoiceItemsDisplay();
        calculateTotals();
    }
}

function updateItemPrice(itemId, price) {
    const item = invoiceItems.find(i => i.id === itemId);
    if (item) {
        item.unit_price = Math.round(parseInt(price, 10) || 0);
        item.total = item.quantity * item.unit_price;
        // Update DOM in place to avoid re-render focus loss
        try {
            const row = document.querySelector(`tr[data-item-id="${itemId}"]`);
            if (row) {
                const qtyInput = row.querySelector('input[type="number"]');
                const priceInput = row.querySelector('input[type="text"]');
                const totalCell = row.querySelector('.row-total');
                if (qtyInput) qtyInput.value = String(item.quantity);
                if (priceInput) priceInput.value = (item.unit_price).toLocaleString('fr-FR');
                if (totalCell) totalCell.textContent = formatCurrency(item.total);
            }
        } catch (e) { /* ignore */ }
        calculateTotals();
    }
}

// Friendlier price input that preserves focus and caret
function handlePriceInput(itemId, inputEl) {
    if (!inputEl) return;
    const caret = inputEl.selectionStart;
    // sanitize but keep comma and dot
    const digitsOnly = (inputEl.value || '').replace(/\D/g, '');
    inputEl.value = digitsOnly;
    updateItemPrice(itemId, digitsOnly);
    // restore caret position if possible
    try { inputEl.setSelectionRange(caret, caret); } catch (e) {}
}

function removeInvoiceItem(itemId) {
    invoiceItems = invoiceItems.filter(i => i.id !== itemId);
    updateInvoiceItemsDisplay();
    calculateTotals();
}

// Réordonner les lignes d'articles (option B: boutons monter/descendre)
function moveItemUp(itemId) {
    const idx = invoiceItems.findIndex(i => i.id === itemId);
    if (idx <= 0) return;
    const tmp = invoiceItems[idx - 1];
    invoiceItems[idx - 1] = invoiceItems[idx];
    invoiceItems[idx] = tmp;
    updateInvoiceItemsDisplay();
}

function moveItemDown(itemId) {
    const idx = invoiceItems.findIndex(i => i.id === itemId);
    if (idx === -1 || idx >= invoiceItems.length - 1) return;
    const tmp = invoiceItems[idx + 1];
    invoiceItems[idx + 1] = invoiceItems[idx];
    invoiceItems[idx] = tmp;
    updateInvoiceItemsDisplay();
}

// Calculer les totaux
function calculateTotals() {
    // Re-read totals from current invoiceItems, but also parse DOM totals in case of desync
    let subtotal = invoiceItems.reduce((sum, item) => sum + (Number(item.total) || 0), 0);
    // If subtotal is zero but there are rows, compute from DOM unit price * qty
    if (subtotal === 0 && invoiceItems.length) {
        try {
            const tbody = document.getElementById('invoiceItemsBody');
            const rows = Array.from(tbody?.querySelectorAll('tr') || []);
            subtotal = rows.reduce((acc, tr) => {
                const qty = parseFloat((tr.querySelector('input[type="number"]')?.value || '0').replace(',', '.')) || 0;
                const inputs = tr.querySelectorAll('input[type="number"]');
                const priceInput = inputs.length > 1 ? inputs[1] : null;
                const unit = parseFloat((priceInput?.value || '0').replace(',', '.')) || 0;
                return acc + qty * unit;
            }, 0);
        } catch (e) { /* ignore fallback */ }
    }
    const showTax = document.getElementById('showTaxSwitch')?.checked ?? true;
    let taxRateRaw = (document.getElementById('taxRateInput')?.value || '0').toString().replace(',', '.');
    const taxRatePct = parseFloat(taxRateRaw) || 0;
    const rate = showTax ? Math.max(0, taxRatePct) / 100 : 0;
    const taxAmount = subtotal * rate;
    const total = subtotal + taxAmount;

    document.getElementById('subtotal').textContent = formatCurrency(subtotal);
    document.getElementById('taxAmount').textContent = formatCurrency(taxAmount);
    const taxLabel = document.getElementById('taxLabel');
    if (taxLabel) taxLabel.textContent = `TVA (${(rate * 100).toFixed(2)}%):`;
    document.getElementById('totalAmount').textContent = formatCurrency(total);

    // Store computed numbers on form for backend
    document.getElementById('invoiceForm').dataset.subtotal = String(subtotal);
    document.getElementById('invoiceForm').dataset.taxAmount = String(taxAmount);
    document.getElementById('invoiceForm').dataset.total = String(total);
    
    // Mettre à jour le montant maximum de paiement immédiat
    updatePaymentNowMaxAmount();
}

// Sauvegarder une facture
async function saveInvoice(status) {
    try {
        // UI lock: prevent double submit and indicate progress
        const modalEl = document.getElementById('invoiceModal');
        const footerButtons = modalEl ? modalEl.querySelectorAll('.modal-footer button, .modal-footer a') : [];
        const prevStates = [];
        try { footerButtons.forEach(btn => { prevStates.push([btn, btn.disabled, btn.innerHTML]); btn.disabled = true; }); } catch(e) {}
        document.body.style.cursor = 'wait';
        const invoiceData = {
            invoice_number: document.getElementById('invoiceNumber').value,
            client_id: parseInt(document.getElementById('clientSelect').value),
            date: document.getElementById('invoiceDate').value,
            due_date: document.getElementById('dueDate').value || null,
            payment_method: (document.getElementById('invoicePaymentMethod') && document.getElementById('invoicePaymentMethod').value) || null,
            notes: document.getElementById('invoiceNotes').value.trim() || '',
            show_tax: document.getElementById('showTaxSwitch')?.checked ?? true,
            tax_rate: parseFloat(document.getElementById('taxRateInput')?.value) || 0,
            show_item_prices: document.getElementById('showSectionPricesSwitch')?.checked ?? true,
            show_section_totals: document.getElementById('showSectionTotalsSwitch')?.checked ?? true,
            subtotal: parseFloat(document.getElementById('invoiceForm').dataset.subtotal || '0'),
            tax_amount: parseFloat(document.getElementById('invoiceForm').dataset.taxAmount || '0'),
            total: parseFloat(document.getElementById('invoiceForm').dataset.total || '0'),
            quotation_id: (function(){ try { const v = document.getElementById('invoiceForm').dataset.quotationId; return v ? Number(v) : null; } catch(e) { return null; } })(),
            // Champs de garantie
            has_warranty: document.getElementById('hasWarrantySwitch')?.checked || false,
            warranty_duration: (function() {
                const hasWarranty = document.getElementById('hasWarrantySwitch')?.checked;
                if (!hasWarranty) return null;
                const selectedDuration = document.querySelector('input[name="warrantyDuration"]:checked');
                return selectedDuration ? parseInt(selectedDuration.value) : 12;
            })(),
            items: invoiceItems
                .flatMap(item => {
                    // Ligne de section (aucun montant, juste un titre visuel)
                    if (item.is_section) {
                        const rawTitle = String(item.section_title || '').trim();
                        if (!rawTitle) return [];
                        const label = `[SECTION] ${rawTitle}`;
                        return [{
                            product_id: null,
                            product_name: label,
                            quantity: 0,
                            price: 0,
                            total: 0,
                            variant_id: null
                        }];
                    }
                    // Ligne personnalisée sans produit
                    if (!item.product_id && item.is_custom) {
                        return [{
                            product_id: null,
                            product_name: item.product_name || 'Service',
                            quantity: item.quantity || 1,
                            price: item.unit_price || 0,
                            total: item.total || ((item.quantity || 1) * (item.unit_price || 0)),
                            variant_id: null
                        }];
                    }
                    if (!item.product_id) return [];
                    const hasImeis = item.scannedImeis && item.scannedImeis.length > 0;
                    if (!hasImeis) {
                        return [{
                            product_id: item.product_id,
                            product_name: item.product_name,
                            quantity: item.quantity,
                            price: item.unit_price,
                            total: item.total,
                            variant_id: item.variant_id || null
                        }];
                    }
                    // Expand into one item per IMEI with variant_id resolved
                    const variants = productVariantsByProductId.get(Number(item.product_id)) || [];
                    return item.scannedImeis.map(imei => {
                        const v = variants.find(x => _normalizeCode(x.imei_serial) === _normalizeCode(imei));
                        return {
                            product_id: item.product_id,
                            // Keep product_name concise to avoid DB limits; IMEI is carried in variant_imei and notes meta
                            product_name: item.product_name,
                            quantity: 1,
                            price: item.unit_price,
                            total: item.unit_price,
                            variant_id: v ? v.variant_id : null,
                            variant_imei: imei
                        };
                    });
                })
        };

        // Inject IMEI meta into notes for detail rendering
        try {
            const serialsMeta = invoiceItems
                .filter(i => i.product_id && i.scannedImeis && i.scannedImeis.length)
                .map(i => ({ product_id: i.product_id, imeis: i.scannedImeis }));
            if (serialsMeta.length) {
                const cleaned = (invoiceData.notes || '').replace(/\n?\n?__SERIALS__=.*$/s, '');
                invoiceData.notes = cleaned ? `${cleaned}\n\n__SERIALS__=${JSON.stringify(serialsMeta)}` : `__SERIALS__=${JSON.stringify(serialsMeta)}`;
            }
        } catch (e) {}

    if (!invoiceData.client_id || !invoiceData.date || invoiceData.items.length === 0) {
            showError('Veuillez remplir tous les champs obligatoires et ajouter au moins un article');
            return;
        }

        const invoiceId = document.getElementById('invoiceId').value;
        const url = invoiceId ? `/api/invoices/${invoiceId}` : '/api/invoices/';
        const method = invoiceId ? 'PUT' : 'POST';

        // Attach signature data if any
        let signatureDataUrl = null;
        try {
            const fileInput = document.getElementById('signatureFile');
            const canvas = document.getElementById('signatureCanvas');
            if (fileInput && fileInput.files && fileInput.files[0]) {
                const file = fileInput.files[0];
                signatureDataUrl = await new Promise(res => { const r = new FileReader(); r.onload = () => res(String(r.result||'')); r.readAsDataURL(file); });
            } else if (canvas) {
                const tmp = document.createElement('canvas'); tmp.width = canvas.width; tmp.height = canvas.height;
                if (canvas.toDataURL() !== tmp.toDataURL()) signatureDataUrl = canvas.toDataURL('image/png');
            }
        } catch(e) {}
        if (signatureDataUrl) {
            invoiceData.notes = (invoiceData.notes || '') + `\n\n__SIGNATURE__=${signatureDataUrl}`;
        }

        // Utiliser axios (avec withCredentials configuré globalement) pour éviter les soucis d'auth avec fetch
        let responseData;
        try {
            if (method === 'POST') {
                const { data } = await axios.post(url, invoiceData, { withCredentials: true });
                responseData = data;
            } else {
                const { data } = await axios.put(url, invoiceData, { withCredentials: true });
                responseData = data;
            }
        } catch (err) {
            const msg = err?.response?.data?.detail || err?.message || 'Erreur lors de la sauvegarde';
            throw new Error(msg);
        }

        // Fermer le modal immédiatement après la sauvegarde principale pour une UX réactive
        try {
            const instance = bootstrap.Modal.getInstance(modalEl) || (modalEl ? new bootstrap.Modal(modalEl) : null);
            if (instance) instance.hide();
        } catch (e) { /* ignore */ }

        // Attacher meta IMEIs dans notes pour l'aperçu
        try {
            const serialsMeta = invoiceItems
                .filter(i => i.product_id && i.scannedImeis && i.scannedImeis.length)
                .map(i => ({ product_id: i.product_id, imeis: i.scannedImeis }));
            if (serialsMeta.length) {
                const existingNotes = (document.getElementById('invoiceNotes').value || '').trim();
                const cleaned = existingNotes.replace(/\n?\n?__SERIALS__=.*$/s, '');
                const meta = `__SERIALS__=${JSON.stringify(serialsMeta)}`;
                // patch invoice payload sent earlier
                const payloadObj = JSON.parse(document.getElementById('invoiceForm').dataset.lastPayload || '{}');
                // but since we already sent the request, we keep for next time; also update notes locally
                // no-op if not needed
            }
        } catch(e) {}

        // Paiement immédiat si demandé: on ATTEND le POST paiement
        const doPay = document.getElementById('paymentNowSwitch')?.checked;
        if (doPay) {
            try {
                const inv = responseData || {};
                const invId = inv.invoice_id || inv.id || document.getElementById('invoiceId').value;
                await axios.post(`/api/invoices/${invId || ''}/payments`, {
                    amount: Math.round(parseFloat(document.getElementById('paymentNowAmount').value || '0')),
                    payment_method: document.getElementById('paymentNowMethod').value,
                    reference: document.getElementById('paymentNowRef').value || null
                });
            } catch (e) { /* ignore payment error but continue */ }
        }

        // Recharger la liste + rafraîchir produits/variantes pour refléter les ventes
        await loadInvoices();
        await loadStats();
        await loadProducts();
        
        showSuccess(invoiceId ? 'Facture modifiée avec succès' : 'Facture créée avec succès');
        // Unlock UI using previous states
        try { prevStates.forEach(([btn, prevDisabled, prevHtml]) => { btn.disabled = prevDisabled; if (prevHtml !== undefined) btn.innerHTML = prevHtml; }); } catch(e) {}
        document.body.style.cursor = '';
        
    } catch (error) {
        console.error('Erreur lors de la sauvegarde:', error);
        showError(error.message || 'Erreur lors de la sauvegarde de la facture');
    } finally {
        // Ensure UI unlock if an error occurred before normal unlock
        const modalEl = document.getElementById('invoiceModal');
        const footerButtons = modalEl ? modalEl.querySelectorAll('.modal-footer button, .modal-footer a') : [];
        try { footerButtons.forEach(btn => { btn.disabled = false; }); } catch(e) {}
        document.body.style.cursor = '';
    }
}

async function saveQuickClient() {
    const name = (document.getElementById('qcName')?.value || '').trim();
    if (!name) { showWarning('Le nom du client est obligatoire'); return; }
    
    const payload = { 
        name, 
        phone: (document.getElementById('qcPhone')?.value || '').trim(), 
        email: (document.getElementById('qcEmail')?.value || '').trim() 
    };
    
    try {
        const { data: client } = await axios.post('/api/clients/', payload);
        // Rafraîchir via recherche serveur pour s'assurer de l'affichage immédiat
        try {
            const input = document.getElementById('clientSearch');
            if (input) {
                input.value = client.name || '';
            }
            // Mettre à jour le hidden
            selectClient(client.client_id, client.name);
        } catch(e) {}
        // Optionnel: recharger une courte liste de clients récents
        try {
            const { data } = await axios.get('/api/clients/', { params: { search: client.name, limit: 8 } });
            clients = Array.isArray(data) ? data : (data.items || []);
        } catch(e) {}
        const qm = bootstrap.Modal.getInstance(document.getElementById('clientQuickModal'));
        if (qm) qm.hide();
        showSuccess('Client ajouté');
    } catch (e) {
        console.error('Erreur lors de la création du client:', e);
        console.error('Payload envoyé:', payload);
        console.error('Response:', e.response);
        
        let errorMessage = 'Erreur lors de la création du client';
        if (e.response?.data?.detail) {
            errorMessage = e.response.data.detail;
        } else if (e.message) {
            errorMessage = e.message;
        }
        
        showError(errorMessage);
    }
}

// Actions sur les factures
function viewInvoice(invoiceId) {
    loadInvoiceDetail(invoiceId).catch(() => showError('Impossible de charger la facture'));
}

function editInvoice(invoiceId) {
    try {
        const detailModalEl = document.getElementById('invoiceDetailModal');
        if (detailModalEl) {
            const existing = bootstrap.Modal.getInstance(detailModalEl) || new bootstrap.Modal(detailModalEl);
            existing.hide();
            if (typeof existing.dispose === 'function') {
                existing.dispose();
            }
            detailModalEl.classList.remove('show');
            detailModalEl.setAttribute('aria-hidden', 'true');
            detailModalEl.style.display = 'none';
            try {
                document.body.classList.remove('modal-open');
                document.querySelectorAll('.modal-backdrop').forEach(el => el.remove());
            } catch (e) { /* ignore */ }
        }
    } catch (e) { /* ignore */ }

    preloadInvoiceIntoForm(invoiceId).catch(() => showError('Impossible de charger la facture pour édition'));
}

function viewQuotation(quotationId) {
    // Naviguer vers la page des devis avec le devis sélectionné
    sessionStorage.setItem('open_quotation_detail_id', quotationId);
    window.location.href = '/quotations';
}

function addPayment(invoiceId) {
    const invoice = invoices.find(i => i.invoice_id === invoiceId);
    if (!invoice) return;
    // If already fully paid, do not allow new payment
    if (String(invoice.status).toUpperCase() === 'PAID') {
        showWarning('Cette facture est déjà payée');
        return;
    }

    document.getElementById('paymentInvoiceId').value = invoiceId;
    // Show summary (paid/remaining)
    const container = document.getElementById('paymentForm');
    const paid = Number(invoice.paid_amount || invoice.paid || 0);
    const total = Number(invoice.total_amount || invoice.total || 0);
    const remaining = Math.max(0, total - paid);
    const remainingInt = Math.floor(remaining);
    let summary = document.getElementById('paymentSummary');
    if (!summary) {
        summary = document.createElement('div');
        summary.id = 'paymentSummary';
        summary.className = 'alert alert-info py-2';
        container.insertBefore(summary, container.firstChild);
    }
    summary.innerHTML = `<div><strong>Déjà payé:</strong> ${formatCurrency(paid)} &nbsp; | &nbsp; <strong>Restant:</strong> ${formatCurrency(remainingInt)}</div>`;
    const paymentAmountInput = document.getElementById('paymentAmount');
    if (paymentAmountInput) {
        paymentAmountInput.step = '1';
        paymentAmountInput.value = remainingInt;
        paymentAmountInput.addEventListener('input', () => {
            const raw = String(paymentAmountInput.value).replace(',', '.');
            const n = Math.floor(Number(raw));
            paymentAmountInput.value = Number.isFinite(n) && n >= 0 ? String(n) : '';
        });
    }

    const modal = new bootstrap.Modal(document.getElementById('paymentModal'));
    modal.show();
}

async function printInvoice(invoiceId) {
    // Ouvre une pop-up (fenêtre contrôlée) pour l'impression
    const features = [
        'width=980',
        'height=800',
        'menubar=0',
        'toolbar=0',
        'location=0',
        'status=0',
        'scrollbars=1',
        'resizable=1'
    ].join(',');
    const popup = window.open(`/invoices/print/${invoiceId}`, 'invoice_print_popup', features);
    if (!popup) {
        showWarning('La fenêtre pop-up a été bloquée par le navigateur');
    }
}

// Générer un bon de livraison à partir d'une facture
async function generateDeliveryNote(invoiceId) {
    if (!invoiceId) return;
    if (!confirm('Générer un bon de livraison pour cette facture ?')) return;
    try {
        const { data } = await axios.post(`/api/invoices/${invoiceId}/delivery-note`, {});
        showSuccess('Bon de livraison créé');
        // Ouvrir la page d'impression BL si disponible dans l'app
        try {
            const dnId = data?.delivery_note_id;
            if (dnId) {
                const popup = window.open(`/delivery-notes/print/${dnId}`, 'delivery_note_print', 'width=980,height=800,scrollbars=1,resizable=1');
                if (!popup) showWarning('La fenêtre pop-up a été bloquée par le navigateur');
            }
        } catch (e) {}
    } catch (error) {
        console.error('Erreur génération BL:', error);
        showError(error.response?.data?.detail || error.message || 'Erreur lors de la génération du bon de livraison');
    }
}

async function loadInvoiceDetail(invoiceId) {
    const { data: inv } = await axios.get(`/api/invoices/${invoiceId}`);
    const client = clients.find(c => c.client_id === inv.client_id);
    const body = document.getElementById('invoiceDetailBody');
    // Parse original quotation quantities from notes meta if present
    let quoteQtyByProductId = new Map();
    try {
        const mqq = ((inv.notes || '').match(/__QUOTE_QTYS__=(.*)$/s) || [])[1];
        if (mqq) {
            const arr = JSON.parse(mqq);
            (arr || []).forEach(e => {
                const pid = Number(e.product_id);
                const qty = Number(e.qty || 0);
                if (!Number.isNaN(pid)) quoteQtyByProductId.set(pid, qty);
            });
        }
    } catch(e){}
    // Fallback: if not present in notes, fetch the original quotation and compute quantities
    if (!quoteQtyByProductId.size && inv.quotation_id) {
        try {
            const { data: q } = await axios.get(`/api/quotations/${inv.quotation_id}`);
            (q.items || []).forEach(it => {
                const pid = Number(it.product_id);
                const qty = Number(it.quantity || 0);
                if (!Number.isNaN(pid)) {
                    quoteQtyByProductId.set(pid, (quoteQtyByProductId.get(pid) || 0) + qty);
                }
            });
        } catch(e) {}
    }

    const items = (inv.items || []).map(it => `
        <tr>
            <td>${escapeHtml(it.product_name || '')}</td>
            <td class="text-end">${it.quantity}${(() => { try { const q = quoteQtyByProductId.get(Number(it.product_id)); return (typeof q === 'number') ? `<div class=\"text-muted small\">Qté devis: ${q}</div>` : ''; } catch(e){ return ''; } })()}</td>
            <td class="text-end">${formatCurrency(it.price)}</td>
            <td class="text-end">${formatCurrency(it.total)}</td>
        </tr>
    `).join('');
    // Extract serials meta, prefer backend-parsed map if provided
    let serialsMap = new Map();
    try {
        const serverMap = inv.serials_by_product_id || {};
        Object.keys(serverMap).forEach(pid => {
            serialsMap.set(String(pid), Array.isArray(serverMap[pid]) ? serverMap[pid] : []);
        });
    } catch(e) {}
    if (serialsMap.size === 0) {
        try {
            const txt = String(inv.notes || '');
            if (txt.includes('__SERIALS__=')) {
                let sub = txt.split('__SERIALS__=', 1)[1];
                const cut = sub.indexOf('\n__');
                if (cut !== -1) sub = sub.slice(0, cut).trim();
                sub = sub.trim();
                let arr;
                try { arr = JSON.parse(sub); } catch (e) {
                    const m2 = txt.match(/__SERIALS__=(\[.*?\])/s);
                    arr = m2 ? JSON.parse(m2[1]) : [];
                }
                (arr || []).forEach(e => {
                    const pid = String(e.product_id);
                    if (!serialsMap.has(pid)) serialsMap.set(pid, []);
                    (e.imeis || []).forEach(im => serialsMap.get(pid).push(im));
                });
            }
        } catch(e){}
    }

    body.innerHTML = `
        <div class="mb-2"><strong>Numéro:</strong> ${escapeHtml(inv.invoice_number)}</div>
        <div class="mb-2"><strong>Client:</strong> ${(() => {
            try {
                const name = escapeHtml(client ? client.name : (inv.client_name || '-'));
                if (inv.client_id) {
                    return `<a href="/clients/detail?id=${inv.client_id}" class="text-decoration-none">${name}</a>`;
                }
                return name;
            } catch (e) { return escapeHtml(inv.client_name || '-'); }
        })()}</div>
        <div class="mb-2"><strong>Date:</strong> ${inv.date ? formatDate(inv.date) : '-'}</div>
        <div class="mb-2"><strong>Échéance:</strong> ${inv.due_date ? formatDate(inv.due_date) : '-'}</div>
        ${(inv.payments && inv.payments.length) ? `
        <div class=\"mb-2\"><strong>Paiements:</strong> ${inv.payments.length} paiement(s)</div>
        <div class=\"table-responsive\"> 
            <table class=\"table table-sm\"> 
                <thead><tr><th>Date</th><th class=\"text-end\">Montant</th><th>Mode</th><th>Réf.</th><th></th></tr></thead>
                <tbody>
                ${(() => {
                    try {
                        const sorted = [...(inv.payments || [])].sort((a, b) => {
                            const da = new Date(a.payment_date || a.date || 0).getTime();
                            const db = new Date(b.payment_date || b.date || 0).getTime();
                            return da - db; // tri chronologique croissant
                        });
                        return sorted.map(p => `
                            <tr>
                                <td>${formatDateTime(p.payment_date || p.date || '-') }</td>
                                <td class=\"text-end\">${formatCurrency(p.amount || 0)}</td>
                                <td>${escapeHtml(p.payment_method || '-')}</td>
                                <td>${escapeHtml(p.reference || '-')}</td>
                                <td class=\"text-end\">
                                    <button type=\"button\" class=\"btn btn-sm btn-outline-danger\" title=\"Supprimer le paiement\" onclick=\"deletePaymentFromInvoiceDetail(${p.payment_id || 0}, ${invoiceId})\">
                                        <i class=\"bi bi-trash\"></i>
                                    </button>
                                </td>
                            </tr>
                        `).join('');
                    } catch (e) { return (inv.payments || []).map(() => '').join(''); }
                })()}
                </tbody>
            </table>
        </div>` : ''}
        <hr/>
        <div class="table-responsive">
            <table class="table table-sm">
                <thead><tr><th>Article</th><th class="text-end">Qté</th><th class="text-end">PU</th><th class="text-end">Total</th></tr></thead>
                <tbody>
                ${(() => {
                    const rows = inv.items || [];
                    const groups = new Map();
                    rows.forEach(it => {
                        const key = `${it.product_id}|${Number(it.price||0)}`;
                        const baseName = (it.product_name || '').replace(/\s*\(IMEI:.*\)$/i, '');
                        if (!groups.has(key)) groups.set(key, { name: baseName, product_id: it.product_id, price: Number(it.price||0), qty: 0, total: 0, imeis: [] });
                        const g = groups.get(key);
                        g.qty += Number(it.quantity||0);
                        g.total += Number(it.total||0);
                        // Fallback: parse inline IMEI from product_name when notes meta not present
                        try {
                            const m = String(it.product_name || '').match(/\(IMEI:\s*([^\)]+)\)/i);
                            if (m && m[1]) {
                                const imei = String(m[1]).trim();
                                if (imei && !(g.imeis||[]).includes(imei)) g.imeis.push(imei);
                            }
                        } catch(e) {}
                    });
                    // Attach IMEIs from notes meta (takes priority)
                    groups.forEach(g => {
                        const list = serialsMap.get(String(g.product_id)) || [];
                        if (list.length) {
                            g.imeis = list;
                            g.qty = list.length; // align qty to count of IMEIs
                            g.total = g.qty * g.price;
                        } else if ((g.imeis||[]).length) {
                            g.qty = g.imeis.length;
                            g.total = g.qty * g.price;
                        }
                        try {
                            const q = quoteQtyByProductId.get(Number(g.product_id));
                            if (typeof q === 'number') {
                                g.quote_qty = q;
                            }
                        } catch(e){}
                    });
                    return Array.from(groups.values()).map(g => `
                        <tr>
                            <td>
                                <strong>${escapeHtml(g.name)}</strong>
                                ${g.imeis && g.imeis.length ? `<div class=\"text-muted small mt-1\">${g.imeis.map(escapeHtml).join('<br/>')}</div>` : ''}
                                ${(() => { try { const p = (products||[]).find(pp => Number(pp.product_id) === Number(g.product_id)); return (p && p.description) ? `<div class=\"text-muted small mt-1\" style=\"text-align:justify\">${escapeHtml(p.description)}</div>` : ''; } catch(e){ return ''; } })()}
                            </td>
                            <td class=\"text-end\">${g.qty}${(typeof g.quote_qty === 'number') ? `<div class=\"text-muted small\">Qté devis: ${g.quote_qty}</div>` : ''}</td>
                            <td class=\"text-end\">${formatCurrency(g.price)}</td>
                            <td class=\"text-end\">${formatCurrency(g.total)}</td>
                        </tr>
                    `).join('');
                })()}
                </tbody>
            </table>
        </div>
        <div class="text-end">
            <div><strong>Sous-total:</strong> ${formatCurrency(inv.subtotal || 0)}</div>
            ${inv.show_tax ? `<div><strong>TVA (${Number(inv.tax_rate || 0)}%):</strong> ${formatCurrency(inv.tax_amount || 0)}</div>` : ''}
            <div class="fs-5"><strong>Total:</strong> ${formatCurrency(inv.total || 0)}</div>
        </div>
    `;
    const modalEl = document.getElementById('invoiceDetailModal');
    if (modalEl) {
        modalEl.dataset.invoiceId = String(invoiceId);
        new bootstrap.Modal(modalEl).show();
    }
}

async function deletePaymentFromInvoiceDetail(paymentId, invoiceId) {
    try {
        if (!paymentId || !invoiceId) return;
        if (!confirm('Supprimer ce paiement ?')) return;
        await axios.delete(`/api/invoices/${invoiceId}/payments/${paymentId}`);
        showSuccess('Paiement supprimé');
        await loadInvoiceDetail(invoiceId);
    } catch (error) {
        console.error('Erreur suppression paiement:', error);
        showError(error?.response?.data?.detail || 'Erreur lors de la suppression du paiement');
    }
}

async function preloadInvoiceIntoForm(invoiceId) {
    // Charger les produits si pas encore chargés
    if (!products || products.length === 0) {
        try {
            const { data } = await axios.get('/api/products');
            products = data.items || data || [];
        } catch(e) {
            console.warn('Erreur chargement produits:', e);
        }
    }
    const { data: inv } = await axios.get(`/api/invoices/${invoiceId}`);
    openInvoiceModal();
    document.getElementById('invoiceModalTitle').innerHTML = '<i class="bi bi-pencil me-2"></i>Modifier la Facture';
    document.getElementById('invoiceId').value = inv.invoice_id;
    document.getElementById('invoiceNumber').value = inv.invoice_number;
    document.getElementById('invoiceDate').value = (inv.date || '').split('T')[0] || '';
    document.getElementById('dueDate').value = (inv.due_date || '').split('T')[0] || '';
    const clientSel = document.getElementById('clientSelect');
    if (clientSel) clientSel.value = inv.client_id;
    try {
        const c = (clients || []).find(x => Number(x.client_id) === Number(inv.client_id));
        const input = document.getElementById('clientSearch');
        if (input) input.value = c ? (c.name || '') : (inv.client_name || '');
    } catch(e) {}
    document.getElementById('showTaxSwitch').checked = !!inv.show_tax;
    document.getElementById('taxRateInput').value = Number(inv.tax_rate || 0);
    // Restaurer l'option d'affichage des prix par article
    const showItemPricesSwitch = document.getElementById('showSectionPricesSwitch');
    if (showItemPricesSwitch) {
        showItemPricesSwitch.checked = inv.show_item_prices !== false;
    }
    // Restaurer l'option d'affichage des totaux par section
    const showSectionTotalsSwitch = document.getElementById('showSectionTotalsSwitch');
    if (showSectionTotalsSwitch) {
        showSectionTotalsSwitch.checked = inv.show_section_totals !== false;
    }
    
    // Afficher les informations de paiement existants
    const totalAmount = Number(inv.total || inv.total_amount || 0);
    const paidAmount = Number(inv.paid_amount || inv.paid || 0);
    const remainingAmount = Math.max(0, totalAmount - paidAmount);
    const isFullyPaid = String(inv.status).toUpperCase() === 'PAID' || remainingAmount <= 0;
    
    // Afficher le résumé des paiements s'il y a des paiements existants ou si c'est payé
    if (paidAmount > 0 || isFullyPaid) {
        const paymentInfo = document.getElementById('existingPaymentInfo');
        const paymentSummary = document.getElementById('paymentStatusSummary');
        
        if (paymentInfo && paymentSummary) {
            paymentSummary.innerHTML = `
                <div><strong>Déjà payé:</strong> ${formatCurrency(paidAmount)}</div>
                <div><strong>Restant:</strong> ${formatCurrency(remainingAmount)}</div>
            `;
            paymentInfo.style.display = 'block';
            
            // Changer la couleur si entièrement payé
            if (isFullyPaid) {
                paymentInfo.className = 'alert alert-success py-2 mb-3';
                paymentSummary.innerHTML += '<div class="text-success"><i class="bi bi-check-circle"></i> <strong>Facture entièrement payée</strong></div>';
            } else {
                paymentInfo.className = 'alert alert-info py-2 mb-3';
            }
        }
    } else {
        // Cacher les informations de paiement s'il n'y a pas de paiements
        const paymentInfo = document.getElementById('existingPaymentInfo');
        if (paymentInfo) paymentInfo.style.display = 'none';
    }
    
    // Extraire les métadonnées IMEI des notes pour restaurer les variantes
    let serialsMap = new Map();
    try {
        const m = ((inv.notes || '').match(/__SERIALS__=(.*)$/s) || [])[1];
        if (m) {
            const arr = JSON.parse(m);
            arr.forEach(e => {
                const pid = String(e.product_id);
                if (!serialsMap.has(pid)) serialsMap.set(pid, []);
                (e.imeis || []).forEach(im => serialsMap.get(pid).push(im));
            });
        }
    } catch(e) {
        console.warn("Erreur lors de l'extraction des métadonnées IMEI:", e);
    }
    
    // Reconstituer les items EN PRÉSERVANT L'ORDRE ORIGINAL
    // On parcourt les items dans l'ordre et on groupe les produits identiques à leur première occurrence
    invoiceItems = [];
    const processedProductIds = new Set();
    const productItemsMap = new Map(); // product_id -> array of raw items
    
    // D'abord, grouper les items par product_id pour pouvoir fusionner les IMEI
    (inv.items || []).forEach(it => {
        const key = String(it.product_id || '');
        if (key) {
            if (!productItemsMap.has(key)) productItemsMap.set(key, []);
            productItemsMap.get(key).push(it);
        }
    });
    
    // Maintenant parcourir dans l'ordre original
    (inv.items || []).forEach(it => {
        const pname = String(it.product_name || '');
        const key = String(it.product_id || '');
        
        // Détecter les sections: product_id null et nom commençant par [SECTION]
        if (!key && pname.startsWith('[SECTION]')) {
            const title = pname.replace(/^\[SECTION\]\s*/, '').trim();
            invoiceItems.push({
                id: Date.now() + Math.random(),
                is_section: true,
                section_title: title || 'Section',
                product_id: null,
                product_name: '',
                variant_id: null,
                variant_imei: null,
                scannedImeis: [],
                quantity: 0,
                unit_price: 0,
                total: 0
            });
            return;
        }
        
        // Item custom sans product_id (service personnalisé, pas une section)
        if (!key) {
            invoiceItems.push({
                id: Date.now() + Math.random(),
                product_id: it.product_id,
                product_name: it.product_name,
                is_custom: true,
                variant_id: it.variant_id || null,
                variant_imei: it.variant_imei || null,
                scannedImeis: [],
                quantity: it.quantity,
                unit_price: Number(it.price),
                total: Number(it.total)
            });
            return;
        }
        
        // Produit normal: ne traiter qu'à la première occurrence
        if (processedProductIds.has(key)) return;
        processedProductIds.add(key);
        
        const groupItems = productItemsMap.get(key) || [it];
        const imeiList = serialsMap.get(key) || [];
        const totalQuantity = groupItems.reduce((sum, x) => sum + Number(x.quantity || 0), 0);
        const totalAmount = groupItems.reduce((sum, x) => sum + Number(x.total || 0), 0);
        
        invoiceItems.push({
            id: Date.now() + Math.random(),
            product_id: it.product_id,
            product_name: it.product_name,
            is_custom: false,
            variant_id: null,
            variant_imei: null,
            scannedImeis: [...imeiList],
            quantity: imeiList.length > 0 ? imeiList.length : totalQuantity,
            unit_price: Number(it.price),
            total: totalAmount
        });
    });
    
    // Fallback: si pas de métadonnées IMEI, essayer de parser depuis les noms de produits
    if (serialsMap.size === 0) {
        invoiceItems.forEach(item => {
            const imeis = [];
            // Chercher des IMEI dans les items originaux ayant le même product_id
            (inv.items || []).forEach(it => {
                if (Number(it.product_id) === Number(item.product_id)) {
                    // Parser IMEI depuis le nom du produit si présent
                    try {
                        const match = String(it.product_name || '').match(/\(IMEI:\s*([^\)]+)\)/i);
                        if (match && match[1]) {
                            const imei = String(match[1]).trim();
                            if (imei && !imeis.includes(imei)) {
                                imeis.push(imei);
                            }
                        }
                    } catch(e) {}
                    
                    // Ou utiliser variant_imei si disponible
                    if (it.variant_imei && !imeis.includes(it.variant_imei)) {
                        imeis.push(it.variant_imei);
                    }
                }
            });
            
            if (imeis.length > 0) {
                item.scannedImeis = imeis;
                item.quantity = imeis.length;
            }
        });
    }
    
    // Charger les quantités d'origine du devis
    quoteQtyByProductId = new Map();
    try {
        const mqq = ((inv.notes || '').match(/__QUOTE_QTYS__=(.*)$/s) || [])[1];
        if (mqq) {
            const arr = JSON.parse(mqq);
            (arr || []).forEach(e => {
                const pid = Number(e.product_id);
                const qty = Number(e.qty || 0);
                if (!Number.isNaN(pid)) quoteQtyByProductId.set(pid, qty);
            });
        }
    } catch(e){}
    if (!quoteQtyByProductId.size && inv.quotation_id) {
        try {
            const { data: q } = await axios.get(`/api/quotations/${inv.quotation_id}`);
            (q.items || []).forEach(it => {
                const pid = Number(it.product_id);
                const qty = Number(it.quantity || 0);
                if (!Number.isNaN(pid)) {
                    quoteQtyByProductId.set(pid, (quoteQtyByProductId.get(pid) || 0) + qty);
                }
            });
        } catch(e) {}
    }
    
    updateInvoiceItemsDisplay();
    calculateTotals();
    
    // Préselectionner la méthode de paiement si disponible
    try {
        const pmSel = document.getElementById('invoicePaymentMethod');
        if (pmSel && inv.payment_method) pmSel.value = inv.payment_method;
    } catch (e) {}
    
    // Charger les données de garantie
    const warrantySwitch = document.getElementById('hasWarrantySwitch');
    const warrantyOptions = document.getElementById('warrantyOptions');
    const warrantyInfo = document.getElementById('warrantyInfo');
    
    if (warrantySwitch) {
        warrantySwitch.checked = !!inv.has_warranty;
        
        // Afficher/masquer les options de garantie
        if (warrantyOptions) {
            warrantyOptions.style.display = inv.has_warranty ? 'block' : 'none';
        }
        if (warrantyInfo) {
            warrantyInfo.style.display = inv.has_warranty ? 'block' : 'none';
        }
        
        // Sélectionner la durée de garantie appropriée
        if (inv.has_warranty && inv.warranty_duration) {
            const durationRadio = document.querySelector(`input[name="warrantyDuration"][value="${inv.warranty_duration}"]`);
            if (durationRadio) {
                durationRadio.checked = true;
            }
        }
        
        // Afficher les dates de garantie si disponibles
        if (inv.has_warranty && warrantyInfo) {
            let warrantyInfoText = '';
            if (inv.warranty_start_date) {
                warrantyInfoText += `<strong>Début:</strong> ${formatDate(inv.warranty_start_date)} `;
            }
            if (inv.warranty_end_date) {
                warrantyInfoText += `<strong>Fin:</strong> ${formatDate(inv.warranty_end_date)}`;
            }
            if (warrantyInfoText) {
                const warrantyDatesDiv = warrantyInfo.querySelector('.warranty-dates') || 
                    (() => {
                        const div = document.createElement('div');
                        div.className = 'warranty-dates small text-muted mt-2';
                        warrantyInfo.appendChild(div);
                        return div;
                    })();
                warrantyDatesDiv.innerHTML = warrantyInfoText;
            }
        }
    }
}

async function duplicateInvoice(invoiceId) {
    try {
        console.log('[DuplicateInvoice] Début duplication facture:', invoiceId);
        
        // Récupérer les données de la facture originale
        const response = await axios.get(`/api/invoices/${invoiceId}`);
        const data = response.data;
        
        console.log('[DuplicateInvoice] Données récupérées:', data);
        
        // Ouvrir le modal manuellement sans passer par openInvoiceModal qui réinitialise tout
        const modalEl = document.getElementById('invoiceModal');
        if (!modalEl) {
            console.error('[DuplicateInvoice] Modal introuvable');
            showError('Erreur: formulaire de facture introuvable');
            return;
        }
        
        // Charger les produits si nécessaire
        try { if (!Array.isArray(products) || products.length === 0) { loadProducts().catch(()=>{}); } } catch(e) {}
        
        // Reset le formulaire d'abord
        const formEl = document.getElementById('invoiceForm');
        if (formEl) formEl.reset();
        
        // Titre du modal
        const titleEl = document.getElementById('invoiceModalTitle');
        if (titleEl) titleEl.innerHTML = '<i class="bi bi-copy me-2"></i>Dupliquer la Facture';
        
        // Ne pas renseigner l'ID pour créer une nouvelle facture
        const idEl = document.getElementById('invoiceId');
        if (idEl) idEl.value = '';
        
        // Dates
        const dateEl = document.getElementById('invoiceDate');
        const dueDateEl = document.getElementById('invoiceDueDate') || document.getElementById('dueDate');
        if (dateEl) dateEl.value = new Date().toISOString().split('T')[0];
        if (dueDateEl) dueDateEl.value = new Date(Date.now() + 30*24*60*60*1000).toISOString().split('T')[0];
        
        // Numéro de facture - récupérer le prochain
        const numberEl = document.getElementById('invoiceNumber');
        if (numberEl) {
            numberEl.value = '';
            numberEl.placeholder = 'Chargement...';
            axios.get('/api/invoices/next-number').then(({ data: numData }) => {
                if (numData && numData.invoice_number) {
                    numberEl.value = numData.invoice_number;
                    numberEl.placeholder = '';
                }
            }).catch(() => {
                numberEl.placeholder = 'Sera généré automatiquement';
            });
        }
        
        // Renseigner le client
        const clientHidden = document.getElementById('clientSelect');
        const clientInput = document.getElementById('clientSearch');
        if (clientHidden && data.client_id) {
            clientHidden.value = data.client_id;
            console.log('[DuplicateInvoice] Client ID défini:', data.client_id);
        }
        if (clientInput && data.client_name) {
            clientInput.value = data.client_name;
            console.log('[DuplicateInvoice] Client name défini:', data.client_name);
        }
        
        // Notes
        const notesEl = document.getElementById('invoiceNotes');
        if (notesEl) notesEl.value = data.notes || '';
        
        // TVA
        const taxInput = document.getElementById('taxRateInput');
        if (taxInput) taxInput.value = Number(data.tax_rate || 18);
        const showTaxSwitch = document.getElementById('showTaxSwitch');
        if (showTaxSwitch) showTaxSwitch.checked = data.show_tax !== false;
        
        // Méthode de paiement
        const pmSel = document.getElementById('invoicePaymentMethod');
        if (pmSel && data.payment_method) pmSel.value = data.payment_method;
        
        // Copier les articles comme lignes personnalisées
        const items = data.items || [];
        console.log('[DuplicateInvoice] Articles à copier:', items);
        
        invoiceItems = items.map((it, idx) => {
            return {
                id: Date.now() + idx + Math.random(),
                product_id: it.product_id,
                product_name: it.product_name || '',
                variant_id: null,
                quantity: it.quantity || 1,
                unit_price: it.price || 0,
                total: it.total || 0,
                is_custom: true
            };
        });
        
        console.log('[DuplicateInvoice] invoiceItems après copie:', invoiceItems);
        
        // Afficher les articles
        updateInvoiceItemsDisplay();
        calculateTotals();
        
        // Ouvrir le modal
        const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
        modal.show();
        
        showSuccess('Formulaire pré-rempli avec les données de la facture.');
    } catch (error) {
        console.error('Erreur lors de la duplication:', error);
        showError('Erreur lors de la duplication de la facture');
    }
}

async function deleteInvoice(invoiceId) {
    if (!confirm('Êtes-vous sûr de vouloir supprimer cette facture ?')) {
        return;
    }

    try {
        await axios.delete(`/api/invoices/${invoiceId}`);
        // Optimistic update: retirer la facture localement immédiatement
        try {
            invoices = Array.isArray(invoices) ? invoices.filter(inv => Number(inv.invoice_id) !== Number(invoiceId)) : [];
            filteredInvoices = Array.isArray(filteredInvoices) ? filteredInvoices.filter(inv => Number(inv.invoice_id) !== Number(invoiceId)) : [];
            displayInvoices();
        } catch (e) {}

        await loadInvoices();
        await loadStats();
        await loadProducts(); // refresh variants availability after deletion restore
        showSuccess('Facture supprimée avec succès');
        
    } catch (error) {
        console.error('Erreur lors de la suppression:', error);
        showError(error.response?.data?.detail || error.message || 'Erreur lors de la suppression de la facture');
    }
}

// Sauvegarder un paiement
async function savePayment() {
    try {
        const paymentData = {
            invoice_id: parseInt(document.getElementById('paymentInvoiceId').value),
            amount: Math.round(parseFloat(document.getElementById('paymentAmount').value)),
            payment_method: document.getElementById('paymentMethod').value,
            payment_date: document.getElementById('paymentDate').value,
            reference: document.getElementById('paymentReference').value.trim() || null,
            notes: document.getElementById('paymentNotes').value.trim() || null
        };

        if (!paymentData.amount || !paymentData.payment_method || !paymentData.payment_date) {
            showError('Veuillez remplir tous les champs obligatoires');
            return;
        }

        const { data: payRes } = await axios.post(`/api/invoices/${paymentData.invoice_id}/payments`, {
            amount: paymentData.amount,
            payment_method: paymentData.payment_method,
            payment_date: paymentData.payment_date ? `${paymentData.payment_date}T00:00:00` : null,
            reference: paymentData.reference,
            notes: paymentData.notes
        });

        // Fermer le modal
        const modal = bootstrap.Modal.getInstance(document.getElementById('paymentModal'));
        modal.hide();

        // Recharger la liste et les stats, et rafraîchir la ligne si visible
        await Promise.all([loadInvoices(), loadStats()]);
        try {
            // Si le détail est ouvert sur cette facture, recharger son contenu
            const modal = document.getElementById('invoiceDetailModal');
            if (modal && modal.classList.contains('show')) {
                const invId = Number(document.getElementById('paymentInvoiceId').value || 0);
                if (invId) await loadInvoiceDetail(invId);
            }
        } catch(e) {}
        
        showSuccess('Paiement enregistré avec succès');
        
    } catch (error) {
        console.error('Erreur lors de l\'enregistrement du paiement:', error);
        showError(error.response?.data?.detail || error.message || 'Erreur lors de l\'enregistrement du paiement');
    }
}

// Mettre à jour le montant maximum de paiement immédiat
function updatePaymentNowMaxAmount() {
    const amountInput = document.getElementById('paymentNowAmount');
    const maxAmountSpan = document.getElementById('maxPaymentAmount');
    const paymentSwitch = document.getElementById('paymentNowSwitch');
    
    // Ne pas traiter si le switch n'est pas activé
    if (!paymentSwitch || !paymentSwitch.checked || !amountInput) return;
    
    const totalAmount = Math.round(parseFloat(document.getElementById('invoiceForm').dataset.total || '0'));
    const invoiceId = document.getElementById('invoiceId').value;
    
    if (invoiceId) {
        // En édition : utiliser le montant restant depuis les infos affichées
        const paymentInfo = document.getElementById('existingPaymentInfo');
        if (paymentInfo && paymentInfo.style.display !== 'none') {
                    const paymentSummary = document.getElementById('paymentStatusSummary');
        if (paymentSummary) {
                        // Extraire le montant restant en cherchant tous les chiffres (y compris les espaces)
                        const remainingMatch = paymentSummary.innerHTML.match(/Restant:[^\d]*([\d\s,\.]+)\s*(?:FCFA|€|\$)?/i);
                        if (remainingMatch) {
                            // Nettoyer le montant puis arrondir à l'entier le plus proche
                            const cleanAmount = remainingMatch[1].replace(/\s/g, '').replace(',', '.');
                            const remainingAmount = Math.round(parseFloat(cleanAmount));
                            
                            if (!isNaN(remainingAmount)) {
                                // Ne mettre à jour que si le montant actuel dépasse le maximum autorisé
                                const currentAmount = Math.round(parseFloat(amountInput.value || '0'));
                                if (currentAmount > remainingAmount) {
                                    amountInput.value = remainingAmount.toString();
                                }
                                
                                amountInput.max = remainingAmount.toString();
                                if (maxAmountSpan) maxAmountSpan.textContent = formatCurrency(remainingAmount);
                                return;
                            }
                        }
                    }
        }
    }
    
    // Par défaut (nouvelle facture ou pas de paiements existants)
    const currentAmount = parseFloat(amountInput.value || '0');
    if (currentAmount > totalAmount) {
        amountInput.value = totalAmount.toString();
    }
    
    amountInput.max = totalAmount.toString();
    if (maxAmountSpan) maxAmountSpan.textContent = formatCurrency(totalAmount);
}

// Envoyer la facture par WhatsApp via n8n
async function sendInvoiceWhatsApp(invoiceId) {
    if (!invoiceId) return;
    try {
        // Récupérer les infos de la facture pour avoir le numéro du client
        const { data: invoice } = await axios.get(`/api/invoices/${invoiceId}`);
        let phone = invoice.client?.phone || '';
        
        // Vérifier si le numéro est renseigné
        if (!phone || phone.trim() === '') {
            phone = prompt('Le numéro de téléphone du client n\'est pas renseigné.\nVeuillez entrer le numéro WhatsApp (ex: +221771234567):');
            if (!phone || phone.trim() === '') {
                showError('Numéro de téléphone requis pour l\'envoi WhatsApp');
                return;
            }
        }
        
        // Normaliser le numéro (enlever espaces, tirets)
        phone = phone.replace(/[\s\-\.]/g, '').trim();
        if (!phone.startsWith('+')) {
            phone = '+221' + phone.replace(/^0/, ''); // Défaut Sénégal
        }
        
        // Appeler l'API n8n pour envoyer
        showSuccess('Envoi en cours via WhatsApp...');
        const response = await axios.post('/api/invoices/send-whatsapp', {
            invoice_id: invoiceId,
            phone: phone
        });
        
        if (response.data?.success) {
            showSuccess('Facture envoyée par WhatsApp avec succès!');
        } else {
            showError(response.data?.message || 'Erreur lors de l\'envoi WhatsApp');
        }
    } catch (error) {
        console.error('Erreur envoi WhatsApp:', error);
        showError(error.response?.data?.detail || 'Erreur lors de l\'envoi par WhatsApp');
    }
}

// Envoyer la facture par Email via n8n
async function sendInvoiceEmail(invoiceId) {
    if (!invoiceId) return;
    try {
        // Récupérer les infos de la facture pour avoir l'email du client
        const { data: invoice } = await axios.get(`/api/invoices/${invoiceId}`);
        let email = invoice.client?.email || '';
        
        // Vérifier si l'email est renseigné
        if (!email || email.trim() === '') {
            email = prompt('L\'email du client n\'est pas renseigné.\nVeuillez entrer l\'adresse email:');
            if (!email || email.trim() === '') {
                showError('Adresse email requise pour l\'envoi');
                return;
            }
        }
        
        // Validation basique de l'email
        if (!email.includes('@') || !email.includes('.')) {
            showError('Adresse email invalide');
            return;
        }
        
        // Appeler l'API n8n pour envoyer
        showSuccess('Envoi en cours par email...');
        const response = await axios.post('/api/invoices/send-email', {
            invoice_id: invoiceId,
            email: email.trim()
        });
        
        if (response.data?.success) {
            showSuccess('Facture envoyée par email avec succès!');
        } else {
            showError(response.data?.message || 'Erreur lors de l\'envoi email');
        }
    } catch (error) {
        console.error('Erreur envoi email:', error);
        showError(error.response?.data?.detail || 'Erreur lors de l\'envoi par email');
    }
}

// Charger et appliquer les méthodes de paiement configurées
async function populatePaymentMethodSelects(selectFirst = false) {
    try {
        // Récupération directe (SQLite via API), pas de cache local
        let methods = await apiStorage.getInvoicePaymentMethods();
        if (!Array.isArray(methods)) methods = [];
        methods = methods.map(v => String(v || '').trim()).filter(v => v.length);
        if (!methods.length) methods = ["Espèces", "Virement bancaire", "Mobile Money", "Chèque", "Carte bancaire"]; // fallback

        const mapToOptions = (arr, withEmpty) => {
            const opts = [];
            if (withEmpty) opts.push('<option value="">Sélectionner un mode</option>');
            arr.forEach(label => {
                // Utiliser l'étiquette comme valeur pour conserver l'affichage côté backend/rapports
                const value = label;
                opts.push(`<option value="${escapeHtml(value)}">${escapeHtml(label)}</option>`);
            });
            return opts.join('');
        };

        // Paiement immédiat dans le formulaire de facture
        const nowSel = document.getElementById('paymentNowMethod');
        if (nowSel) {
            nowSel.innerHTML = mapToOptions(methods, false);
            if (selectFirst && methods.length) nowSel.value = methods[0];
            // Forcer un change pour UI réactive
            try { nowSel.dispatchEvent(new Event('change')); } catch(e) {}
        }

        // Modal paiement
        const modalSel = document.getElementById('paymentMethod');
        if (modalSel) {
            modalSel.innerHTML = mapToOptions(methods, true);
            if (selectFirst && !modalSel.value && methods.length) modalSel.value = methods[0];
        }
    } catch (e) {
        // En cas d'erreur on ne bloque pas l'UI, on garde les valeurs par défaut du HTML
        console.warn('Impossible de charger les méthodes de paiement configurées:', e);
    }
}
