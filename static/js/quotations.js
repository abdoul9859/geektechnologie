async function loadQuotationDetail(quotationId) {
    const { data: q } = await axios.get(`/api/quotations/${quotationId}`);
    const cl = (clients || []).find(c => Number(c.client_id) === Number(q.client_id));
    const body = document.getElementById('quotationDetailBody');
    const items = (q.items || []).map(it => `
        <tr>
            <td>
                ${escapeHtml(it.product_name || '')}
                ${(() => { try { const p = (products||[]).find(pp => Number(pp.product_id) === Number(it.product_id)); return (p && p.description) ? `<div class="text-muted small mt-1" style="text-align:justify">${escapeHtml(p.description)}</div>` : ''; } catch(e){ return ''; } })()}
            </td>
            <td class="text-end">${it.quantity}</td>
            <td class="text-end">${formatCurrency(it.price)}</td>
            <td class="text-end">${formatCurrency(it.total)}</td>
        </tr>
    `).join('');
    body.innerHTML = `
        <div class="mb-2"><strong>Numéro:</strong> ${escapeHtml(q.quotation_number)}</div>
        <div class="mb-2"><strong>Client:</strong> ${escapeHtml(q.client_name || (q.client ? q.client.name : '') || (cl ? cl.name : '-'))}</div>
        <div class="mb-2"><strong>Date:</strong> ${q.date ? formatDate(q.date) : '-'}</div>
        <div class="mb-2"><strong>Valide jusqu'au:</strong> ${q.expiry_date ? formatDate(q.expiry_date) : '-'}</div>
        <div class="table-responsive"> 
            <table class="table table-sm"> 
                <thead><tr><th>Article</th><th class="text-end">Qté</th><th class="text-end">PU</th><th class="text-end">Total</th></tr></thead>
                <tbody>${items}</tbody>
            </table>
        </div>
        <div class="text-end">
            <div><strong>Sous-total:</strong> ${formatCurrency(q.subtotal || 0)}</div>
            <div><strong>TVA (${Number(q.tax_rate || 0)}%):</strong> ${formatCurrency(q.tax_amount || 0)}</div>
            <div class="fs-5"><strong>Total:</strong> ${formatCurrency(q.total || 0)}</div>
        </div>
    `;
    const modal = new bootstrap.Modal(document.getElementById('quotationDetailModal'));
    document.getElementById('quotationDetailModal').dataset.quotationId = quotationId;
    modal.show();
}

async function preloadQuotationIntoForm(quotationId) {
    // Charger les produits si pas encore chargés
    if (!products || products.length === 0) {
        try {
            const { data } = await axios.get('/api/products');
            products = data.items || data || [];
        } catch(e) {
            console.warn('Erreur chargement produits:', e);
        }
    }
    const { data: q } = await axios.get(`/api/quotations/${quotationId}`);
    openQuotationModal(true);
    document.getElementById('quotationModalTitle').innerHTML = '<i class=\"bi bi-pencil me-2\"></i>Modifier le Devis';
    document.getElementById('quotationId').value = q.quotation_id;
    document.getElementById('quotationNumber').value = q.quotation_number;
    document.getElementById('quotationDate').value = (q.date || '').split('T')[0] || '';
    document.getElementById('validUntil').value = (q.expiry_date || '').split('T')[0] || '';
    // Renseigner le client (champ caché + champ recherche)
    try {
        const hidden = document.getElementById('clientSelect');
        const input = document.getElementById('clientSearch');
        if (hidden) hidden.value = q.client_id || '';
        const cl = (clients || []).find(c => Number(c.client_id) === Number(q.client_id));
        if (input) input.value = cl ? (cl.name || '') : (q.client_name || '');
    } catch(e) {}
    document.getElementById('quotationNotes').value = q.notes || '';
    // TVA
    const taxInput = document.getElementById('taxRateInput');
    if (taxInput) taxInput.value = Number(q.tax_rate || 18);
    const showTaxSwitch = document.getElementById('showTaxSwitch');
    if (showTaxSwitch) showTaxSwitch.checked = (Number(q.tax_rate || 0) > 0);
    // Restaurer les options d'affichage
    const showItemPricesSwitch = document.getElementById('showItemPricesSwitch');
    if (showItemPricesSwitch) showItemPricesSwitch.checked = q.show_item_prices !== false;
    const showSectionTotalsSwitch = document.getElementById('showSectionTotalsSwitch');
    if (showSectionTotalsSwitch) showSectionTotalsSwitch.checked = q.show_section_totals !== false;
    // Items - reconstituer en préservant l'ordre et en détectant les sections
    quotationItems = (q.items || []).map(it => {
        const pname = String(it.product_name || '');
        // Détecter les sections: product_id null et nom commençant par [SECTION]
        if (!it.product_id && pname.startsWith('[SECTION]')) {
            const title = pname.replace(/^\[SECTION\]\s*/, '').trim();
            return {
                id: Date.now() + Math.random(),
                is_section: true,
                section_title: title || 'Section',
                product_id: null,
                product_name: '',
                quantity: 0,
                unit_price: 0,
                total: 0
            };
        }
        return {
            id: Date.now() + Math.random(),
            product_id: it.product_id,
            product_name: it.product_name,
            is_custom: !it.product_id,
            quantity: it.quantity,
            unit_price: Number(it.price),
            total: Number(it.total)
        };
    });
    updateQuotationItemsDisplay();
    calculateTotals();
}
// Gestion des devis
let currentPage = 1;
const itemsPerPage = 10;
let currentSort = { by: 'date', dir: 'desc' };
let quotations = [];
let filteredQuotations = [];
let clients = [];
let products = [];
let quotationItems = [];
let productVariantsByProductId = new Map(); // pour IMEI/variantes comme factures
let productIdToStock = new Map();

// Fallback: define buildSortHeader locally if not provided by products.js
if (typeof window.buildSortHeader !== 'function') {
    window.buildSortHeader = function(label, byKey) {
        const isActive = (window.currentSort && window.currentSort.by === byKey);
        const ascActive = isActive && window.currentSort.dir === 'asc';
        const descActive = isActive && window.currentSort.dir === 'desc';
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

// Initialisation (cookie-based auth readiness)
document.addEventListener('DOMContentLoaded', function() {
    const ready = () => {
        const hasAuthManager = !!window.authManager;
        const hasUser = !!(hasAuthManager && window.authManager.userData && Object.keys(window.authManager.userData).length);
        return hasAuthManager && (window.authManager.isAuthenticatedSync() || hasUser);
    };

    const boot = () => {
        // Masquer la carte "Valeur Totale" pour les non-admin
        try {
            if (window.authManager && !window.authManager.isAdmin()) {
                const valueEl = document.getElementById('totalValue');
                const valueCardCol = valueEl ? valueEl.closest('.col-md-3') : null;
                if (valueCardCol) valueCardCol.style.display = 'none';
            }
        } catch (e) { /* ignore */ }
        loadQuotations();
        try { loadStats(); } catch(e){}
        // Lazy load clients/products on demand only
        setupEventListeners();
        setDefaultDates();
        // Précharger les produits pour que le select fonctionne immédiatement
        try { loadProducts(); } catch(e) {}
        
        // Si on arrive depuis une facture pour voir un devis spécifique
        try {
            const openId = sessionStorage.getItem('open_quotation_detail_id');
            if (openId) {
                sessionStorage.removeItem('open_quotation_detail_id');
                // Attendre un court instant que la liste soit affichée puis ouvrir le devis
                setTimeout(() => {
                    try { viewQuotation(Number(openId)); } catch(e) {}
                }, 500);
            }
        } catch (e) {}
    };

    // Initialiser immédiatement sans délai pour un chargement instantané
    boot();
});

function setupEventListeners() {
    // Filtres
    document.getElementById('statusFilter')?.addEventListener('change', () => loadQuotations(1));
    document.getElementById('clientFilter')?.addEventListener('input', debounce(() => loadQuotations(1), 300));
    document.getElementById('dateFromFilter')?.addEventListener('change', () => loadQuotations(1));
    document.getElementById('dateToFilter')?.addEventListener('change', () => loadQuotations(1));

    // Initialiser la recherche client
    try { setupClientSearch(); } catch(e) {}

    // Bouton nouveau client (modal rapide)
    document.getElementById('openQuickClientBtnQ')?.addEventListener('click', () => {
        const m = new bootstrap.Modal(document.getElementById('clientQuickModalQ'));
        m.show();
        setTimeout(() => document.getElementById('qcNameQ')?.focus(), 150);
    });
    document.getElementById('saveQuickClientBtnQ')?.addEventListener('click', saveQuickClientFromQuote);

    // TVA controls
    const taxSwitch = document.getElementById('showTaxSwitch');
    const taxRateInput = document.getElementById('taxRateInput');
    if (taxSwitch) taxSwitch.addEventListener('change', calculateTotals);
    if (taxRateInput) {
        const handler = () => calculateTotals();
        taxRateInput.addEventListener('input', handler);
        taxRateInput.addEventListener('change', handler);
        taxRateInput.addEventListener('keyup', handler);
        taxRateInput.addEventListener('blur', handler);
    }

    // Recherche produit dans lignes devis
    document.getElementById('quotationItemsBody')?.addEventListener('input', debounce(async (e) => {
        const target = e.target;
        if (!(target && target.classList && target.classList.contains('quotation-search-input'))) return;
        const query = String(target.value || '').trim();
        const row = target.closest('tr');
        if (!row) return;
        const suggestBox = row.querySelector('.quotation-suggestions');
        if (!suggestBox) return;
        if (query.length < 2) { suggestBox.classList.add('d-none'); suggestBox.innerHTML = ''; return; }
        try {
            const res = await axios.get('/api/products/', { params: { search: query, limit: 20 } });
            const list = res.data?.items || res.data || [];
            // Conserver la dernière liste de résultats produit (aligné sur factures)
            try { window._latestProductResults = list; } catch (e) {}
            suggestBox.innerHTML = list.map(p => {
                const variants = Array.isArray(p.variants) ? p.variants : [];
                const hasVariants = variants.length > 0;
                const available = hasVariants ? variants.filter(v => !v.is_sold).length : Number(p.quantity || 0);
                const stockBadge = `<span class="badge ${available>0?'bg-success':'bg-danger'} ms-2">${available>0?('Stock: '+available):'Rupture'}</span>`;
                const isOutOfStock = available === 0;
                return `
                <div class="list-group-item list-group-item-action d-flex justify-content-between align-items-center ${isOutOfStock ? 'text-muted' : ''}" data-product-id="${p.product_id}">
                    <div class="d-flex align-items-center gap-2">
                        ${p.image_path ? `
                            <img src="/${p.image_path}" alt="${escapeHtml(p.name)}"
                                 style="width: 40px; height: 40px; object-fit: cover; border-radius: 4px;">
                        ` : ''}
                        <div>
                            <div class="fw-semibold d-flex align-items-center">${escapeHtml(p.name)} ${stockBadge}</div>
                            <div class="text-muted small">${p.barcode ? 'Code: '+escapeHtml(p.barcode) : ''}</div>
                        </div>
                    </div>
                    <div class="text-nowrap ms-3">
                        <div class="fw-semibold">${formatCurrency(p.price)}</div>
                        ${p.wholesale_price ? `<div class="text-muted small">Gros: ${formatCurrency(p.wholesale_price)}</div>` : ''}
                    </div>
                </div>`;
            }).join('');
            suggestBox.classList.toggle('d-none', list.length === 0);
        } catch (err) {
            suggestBox.classList.add('d-none');
            suggestBox.innerHTML = '';
        }
    }, 250));

    // Sélection d'une suggestion - utiliser mousedown pour capturer avant blur
    document.addEventListener('mousedown', (e) => {
        const item = e.target.closest('.list-group-item[data-product-id]');
        if (!item) return;
        // Vérifier qu'on est bien dans le contexte des devis (quotation-suggestions)
        const suggestBox = item.closest('.quotation-suggestions');
        if (!suggestBox) return;

        // Empêcher le comportement par défaut et la propagation
        e.preventDefault();
        e.stopPropagation();

        const productId = item.getAttribute('data-product-id');
        if (!productId) return;

        // Trouver le conteneur de la ligne via l'attribut data-item-id
        const row = suggestBox.closest('[data-item-id]');
        const input = row?.querySelector('.quotation-search-input');

        // Récupérer l'id logique de l'item via structure JS
        let idAttr = null;
        try {
            idAttr = row?.querySelector('button.btn-outline-danger')?.getAttribute('onclick')?.match(/removeQuotationItem\((\d+)\)/)?.[1] || null;
        } catch (err) {}
        const explicitId = Number(row?.dataset?.itemId || idAttr || 0);
        const realId = explicitId || (quotationItems.find(it => !it.product_id)?.id);

        console.log('[QuotationProductSelect] click on product', productId, 'row:', row, 'realId:', realId);

        if (productId && realId) {
            selectProduct(Number(realId), productId);
            if (suggestBox) { suggestBox.innerHTML = ''; suggestBox.classList.add('d-none'); }
            if (input) input.value = '';
        }
    }, true);

    // Cacher le dropdown si clic en dehors
    document.addEventListener('click', (e) => {
        // Ne pas fermer si le clic est sur un élément de suggestion (déjà géré ci-dessus)
        if (e.target.closest('.list-group-item[data-product-id]')) return;
        document.querySelectorAll('.quotation-suggestions').forEach(box => {
            // Vérifier si le clic est dans le conteneur parent (input-group ou le box lui-même)
            const container = box.closest('.position-relative');
            if (container && container.contains(e.target)) return;
            if (box.contains(e.target)) return;
            box.classList.add('d-none');
        });
    });
}
async function saveQuickClientFromQuote() {
    const name = (document.getElementById('qcNameQ')?.value || '').trim();
    if (!name) { showWarning('Le nom du client est obligatoire'); return; }
    try {
        const payload = { name, phone: (document.getElementById('qcPhoneQ')?.value || '').trim(), email: (document.getElementById('qcEmailQ')?.value || '').trim() };
        const { data: client } = await axios.post('/api/clients/', payload);
        // Renseigner le champ client directement
        try {
            const input = document.getElementById('clientSearch');
            if (input) input.value = client.name || '';
            const hidden = document.getElementById('clientSelect');
            if (hidden) hidden.value = String(client.client_id || '');
        } catch(e) {}
        // Optionnel: rafraîchir une courte liste pour l'autocomplete
        try {
            const { data } = await axios.get('/api/clients/', { params: { search: client.name, limit: 8 } });
            clients = Array.isArray(data) ? data : (data.items || []);
        } catch(e) {}
        const qm = bootstrap.Modal.getInstance(document.getElementById('clientQuickModalQ'));
        if (qm) qm.hide();
        showSuccess('Client ajouté');
    } catch (e) {
        showError('Erreur lors de la création du client');
    }
}

function setDefaultDates() {
    const today = new Date().toISOString().split('T')[0];
    const quotationDate = document.getElementById('quotationDate');
    
    if (quotationDate) quotationDate.value = today;
    
    // Date de validité par défaut (30 jours)
    const validUntil = new Date();
    validUntil.setDate(validUntil.getDate() + 30);
    const validUntilInput = document.getElementById('validUntil');
    if (validUntilInput) validUntilInput.value = validUntil.toISOString().split('T')[0];
}

// Statistiques: ne pas recalculer côté client pour éviter d'écraser les totaux serveur
async function loadStats() { /* no-op: cards are updated from server payload */ }

function updateStats() {
    try {
        const list = Array.isArray(filteredQuotations) && filteredQuotations.length ? filteredQuotations : (Array.isArray(quotations) ? quotations : []);
        const total = list.length;
        // Acceptés
        const accepted = list.filter(q => String(q.status || '').toLowerCase() === 'accepté').length;
        // En attente = statut 'en attente' uniquement
        const pending = list.filter(q => String(q.status || '').toLowerCase() === 'en attente').length;
        // Valeur totale
        const totalValue = list.reduce((s, q) => s + (Number(q.total) || 0), 0);
        const elTotal = document.getElementById('totalQuotations');
        const elAccepted = document.getElementById('acceptedQuotations');
        const elPending = document.getElementById('pendingQuotations');
        const elValue = document.getElementById('totalValue');
        if (elTotal) elTotal.textContent = String(total);
        if (elAccepted) elAccepted.textContent = String(accepted);
        if (elPending) elPending.textContent = String(pending);
        if (elValue) elValue.textContent = typeof formatCurrency === 'function' ? formatCurrency(totalValue) : `${(totalValue||0).toLocaleString('fr-FR')} XOF`;
    } catch (e) {
        // silencieux
    }
}

// Charger la liste des devis
async function loadQuotations(page = 1) {
    try {
        showLoading();
        const params = new URLSearchParams({ page, page_size: itemsPerPage, sort_by: currentSort.by, sort_dir: currentSort.dir });
        const statusVal = document.getElementById('statusFilter')?.value || '';
        const clientVal = document.getElementById('clientFilter')?.value || '';
        const dateFrom = document.getElementById('dateFromFilter')?.value || '';
        const dateTo = document.getElementById('dateToFilter')?.value || '';
        if (statusVal) params.append('status_filter', statusVal);
        if (clientVal) params.append('client_search', clientVal);
        if (dateFrom) params.append('start_date', dateFrom);
        if (dateTo) params.append('end_date', dateTo);

        const response = await safeLoadData(
            () => axios.get(`/api/quotations/paginated?${params.toString()}`),
            {
                timeout: 8000,
                fallbackData: { items: [], total: 0, total_accepted: 0, total_pending: 0, total_value: 0 },
                errorMessage: 'Erreur lors du chargement des devis'
            }
        );
        const payload = response?.data ?? { items: [], total: 0 };
        quotations = Array.isArray(payload.items) ? payload.items : [];
        filteredQuotations = [...quotations];

        // Maj stats agrégées fournies par l'API (conserver au-dessus de tout recalcul local)
        try {
            document.getElementById('totalQuotations').textContent = String(payload.total || 0);
            document.getElementById('acceptedQuotations').textContent = String(payload.total_accepted || 0);
            document.getElementById('pendingQuotations').textContent = String(payload.total_pending || 0);
            document.getElementById('totalValue').textContent = formatCurrency(Number(payload.total_value || 0));
        } catch (e) {}

        if (!Array.isArray(filteredQuotations) || filteredQuotations.length === 0) {
            showEmptyState();
            renderQuotationsPagination(page, payload.total || 0);
            return;
        }

        currentPage = page;
        displayQuotations();
        renderQuotationsPagination(page, payload.total || 0);
    } catch (error) {
        console.error('Erreur lors du chargement des devis:', error);
        showError(error.response?.data?.detail || 'Erreur lors du chargement des devis');
        showEmptyState();
    }
}

// Charger les clients
async function loadClients() {
    try {
        const { data } = await axios.get('/api/clients/');
        clients = data?.items || data || [];
        populateClientSelect();
        // Re-rendre la liste des devis pour injecter le nom client après chargement
        try { if (Array.isArray(quotations) && quotations.length) displayQuotations(); } catch(e) {}
    } catch (error) {
        console.error('Erreur lors du chargement des clients:', error);
    }
}

// Charger les produits
async function loadProducts() {
    try {
        const { data } = await axios.get('/api/products/?limit=200');
        products = data?.items || data || [];
        // Préparer variations par produit (même logique que factures)
        productVariantsByProductId.clear();
        productIdToStock.clear();
        await Promise.all((products || []).map(async (p) => {
            try {
                const variants = (p.variants && p.variants.length) ? p.variants : [];
                productVariantsByProductId.set(p.product_id, variants);
                // Stock info (non bloquant)
                const available = variants.length ? variants.filter(v => !v.is_sold).length : (p.quantity || 0);
                productIdToStock.set(p.product_id, available);
            } catch (e) {}
        }));
    } catch (error) {
        console.error('Erreur lors du chargement des produits:', error);
    }
}

// Remplir le select des clients
function populateClientSelect() {
    // Conservé pour compat, mais le champ clientSelect est désormais un input hidden
    const clientSelect = document.getElementById('clientSelect');
    if (!clientSelect) return;
    // rien à faire ici pour l'UI recherche
}

// Recherche client avec autocomplétion (aligné sur factures)
function setupClientSearch() {
    const searchInput = document.getElementById('clientSearch');
    const resultsBox = document.getElementById('clientSearchResults');
    if (!searchInput || !resultsBox) return;

    const closeResults = () => { resultsBox.style.display = 'none'; };
    let _latestClientResults = [];

    const renderList = (term) => {
        const t = String(term || '').toLowerCase().trim();
        const safe = v => String(v || '').toLowerCase();
        // Rechercher côté serveur pour garantir la mise à jour en temps réel
        axios.get('/api/clients/', { params: { search: t || undefined, limit: 8 } })
            .then(({ data }) => {
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
            })
            .catch(() => {
                resultsBox.innerHTML = '<div class="list-group-item text-muted small">Erreur de chargement</div>';
                resultsBox.style.display = 'block';
            });
    };

    // Ouvrir la liste au focus (même sans terme)
    searchInput.addEventListener('focus', () => {
        renderList(searchInput.value);
    });

    // Mettre à jour la liste au fil de la frappe (sans minimum de caractères)
    searchInput.addEventListener('input', debounce(function(e){
        const inputVal = (e && e.target && typeof e.target.value === 'string') ? e.target.value : (searchInput.value || '');
        renderList(inputVal);
    }, 200));

    // Sélection par clic
    resultsBox.addEventListener('click', function(e){
        const btn = e.target.closest('[data-client-id]');
        if (!btn) return;
        const id = Number(btn.getAttribute('data-client-id'));
        const c = (_latestClientResults || []).find(x => Number(x.client_id) === id) || (clients || []).find(x => Number(x.client_id) === id);
        if (c) {
            selectClient(c.client_id, c.name);
        } else {
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

// Afficher les devis
function displayQuotations() {
    const tbody = document.getElementById('quotationsTableBody');
    if (!tbody) return;

    if (filteredQuotations.length === 0) {
        showEmptyState();
        return;
    }

    // Server-side pagination: render page items directly
    const quotationsToShow = filteredQuotations;

    tbody.innerHTML = quotationsToShow.map(quotation => {
        const cl = (clients || []).find(c => Number(c.client_id) === Number(quotation.client_id));
        const clientName = cl ? cl.name : (quotation.client_name || quotation.client?.name || '-');
        const currentStatusFr = String(quotation.status || '').toLowerCase();
        const hasInvoice = !!Number(quotation.invoice_id || 0);
        return `
        <tr>
            <td>
                <strong>${escapeHtml(quotation.quotation_number)}</strong>
            </td>
            <td>${escapeHtml(clientName)}</td>
            <td>${quotation.date ? formatDate(quotation.date) : '-'}</td>
            <td>${quotation.expiry_date ? formatDate(quotation.expiry_date) : '-'}</td>
            <td><strong>${formatCurrency(Number(quotation.total || 0))}</strong></td>
            <td>
                <select class="form-select form-select-sm" onchange="changeQuotationStatus(${quotation.quotation_id}, this.value)">
                    <option value="en attente" ${currentStatusFr==='en attente'?'selected':''}>En attente</option>
                    <option value="accepté" ${currentStatusFr==='accepté'?'selected':''}>Accepté</option>
                    <option value="refusé" ${currentStatusFr==='refusé'?'selected':''}>Refusé</option>
                    <option value="expiré" ${currentStatusFr==='expiré'?'selected':''}>Expiré</option>
                </select>
            </td>
            <td class="text-center">
                <div class="form-check form-switch d-inline-flex align-items-center">
                    <input class="form-check-input" type="checkbox" ${quotation.is_sent ? 'checked' : ''} onchange="toggleQuotationSent(${quotation.quotation_id}, this.checked)">
                    <label class="form-check-label ms-2">Envoyé</label>
                </div>
            </td>
            <td>
                <div class="btn-group" role="group">
                    <button class="btn btn-sm btn-outline-info" onclick="viewQuotation(${quotation.quotation_id})" title="Voir">
                        <i class="bi bi-eye"></i>
                    </button>
                    <button class="btn btn-sm btn-outline-primary" onclick="editQuotation(${quotation.quotation_id})" title="Modifier">
                        <i class="bi bi-pencil"></i>
                    </button>
                    ${hasInvoice ? `
                        <button class="btn btn-sm btn-success" onclick="goToInvoice(${Number(quotation.invoice_id)})" title="Voir la facture">
                            <i class="bi bi-receipt"></i>
                        </button>
                    ` : ((String(quotation.status||'').toLowerCase()==='accepté') ? `
                        <button class="btn btn-sm btn-outline-success" onclick="convertToInvoice(${quotation.quotation_id})" title="Convertir en facture">
                            <i class="bi bi-receipt"></i>
                        </button>
                    ` : '')}
                    <button class="btn btn-sm btn-outline-secondary" onclick="printQuotation(${quotation.quotation_id})" title="Imprimer">
                        <i class="bi bi-printer"></i>
                    </button>
                    <button class="btn btn-sm btn-outline-success" onclick="sendQuotationWhatsApp(${quotation.quotation_id})" title="Envoyer par WhatsApp">
                        <i class="bi bi-whatsapp"></i>
                    </button>
                    <button class="btn btn-sm btn-outline-secondary" onclick="duplicateQuotation(${quotation.quotation_id})" title="Dupliquer">
                        <i class="bi bi-copy"></i>
                    </button>
                    <button class="btn btn-sm btn-outline-danger" onclick="deleteQuotation(${quotation.quotation_id})" title="Supprimer">
                        <i class="bi bi-trash"></i>
                    </button>
                </div>
            </td>
        </tr>
    `}).join('');
}
function goToInvoice(invoiceId) {
    if (!invoiceId) return;
    try {
        sessionStorage.setItem('invoiceSearchQuery', String(invoiceId));
    } catch(e) {}
    window.location.href = '/invoices';
}

async function toggleQuotationSent(quotationId, isSent) {
    try {
        await axios.put(`/api/quotations/${quotationId}/sent`, { is_sent: !!isSent });
        await loadQuotations();
        await loadStats();
        showSuccess(isSent ? 'Devis marqué comme envoyé' : 'Devis marqué comme non envoyé');
    } catch (e) {
        showError(e.response?.data?.detail || 'Impossible de changer l\'état envoyé');
    }
}

// Changer le statut directement depuis la liste
async function changeQuotationStatus(quotationId, newStatus) {
    try {
        const statusFr = String(newStatus || '').toLowerCase();
        await axios.put(`/api/quotations/${quotationId}/status`, { status: statusFr });
        await loadQuotations();
        await loadStats();
        showSuccess('Statut du devis mis à jour');
    } catch (e) {
        showError(e.response?.data?.detail || 'Impossible de mettre à jour le statut');
    }
}

function showEmptyState() {
    const tbody = document.getElementById('quotationsTableBody');
    if (tbody) {
        tbody.innerHTML = `
            <tr>
                <td colspan="7" class="text-center text-muted py-4">
                    <i class="bi bi-inbox fs-1 d-block mb-2"></i>
                    Aucun devis trouvé
                </td>
            </tr>
        `;
    }
}

function showLoading() {
    // Ne pas afficher d'indicateur de chargement pour une expérience instantanée
}

// Utilitaires pour les statuts de devis
function getQuotationStatusBadgeColor(status) {
    switch (status) {
        case 'DRAFT': return 'secondary';
        case 'SENT': return 'primary';
        case 'ACCEPTED': return 'success';
        case 'REJECTED': return 'danger';
        case 'EXPIRED': return 'warning';
        default: return 'secondary';
    }
}

function getQuotationStatusLabel(status) {
    switch (status) {
        case 'DRAFT': return 'Brouillon';
        case 'SENT': return 'Envoyé';
        case 'ACCEPTED': return 'Accepté';
        case 'REJECTED': return 'Refusé';
        case 'EXPIRED': return 'Expiré';
        default: return status;
    }
}

// Tri dans l'en-tête
(function wireQuotationSortHeader(){
    try {
        const map = [
            ['number','Numéro'], ['client','Client'], ['date','Date'], ['total','Montant'], ['status','Statut'], ['sent','Envoyé']
        ];
        map.forEach(([key, label]) => {
            const th = document.querySelector(`#quotationsTable thead th[data-col="${key}"]`);
            if (!th) return;
            th.innerHTML = buildSortHeader(label, key);
        });
        // utiliser les helpers de products.js: buildSortHeader/wireSortHeaderButtons si présents
        document.querySelectorAll('#quotationsTable [data-sort-by]')?.forEach(btn => {
            btn.addEventListener('click', (e) => {
                const by = btn.getAttribute('data-sort-by');
                const dir = btn.getAttribute('data-sort-dir');
                currentSort = { by, dir };
                loadQuotations(1);
            });
        });
    } catch (e) { /* ignore */ }
})();

// Filtrer les devis
function filterQuotations() {
    // Au lieu de filtrer côté client, recharge directement depuis l'API paginée
    loadQuotations(1);
}

// Pagination
function renderQuotationsPagination(page, total) {
    const totalPages = Math.max(1, Math.ceil(Number(total || 0) / itemsPerPage));
    const paginationContainer = document.getElementById('pagination-container');
    if (!paginationContainer) return;
    if (totalPages <= 1) { paginationContainer.innerHTML = ''; return; }

    let html = '<nav><ul class="pagination justify-content-center">';
    const prev = Math.max(1, page - 1);
    const next = Math.min(totalPages, page + 1);
    html += `
        <li class="page-item ${page === 1 ? 'disabled' : ''}">
            <a class="page-link" href="#" data-page="${prev}">Précédent</a>
        </li>
    `;
    // Windowed numbers
    const start = Math.max(1, page - 2);
    const end = Math.min(totalPages, page + 2);
    if (start > 1) {
        html += `<li class="page-item"><a class="page-link" href="#" data-page="1">1</a></li>`;
        if (start > 2) html += '<li class="page-item disabled"><span class="page-link">...</span></li>';
    }
    for (let i = start; i <= end; i++) {
        html += `<li class="page-item ${i === page ? 'active' : ''}"><a class="page-link" href="#" data-page="${i}">${i}</a></li>`;
    }
    if (end < totalPages) {
        if (end < totalPages - 1) html += '<li class="page-item disabled"><span class="page-link">...</span></li>';
        html += `<li class="page-item"><a class="page-link" href="#" data-page="${totalPages}">${totalPages}</a></li>`;
    }
    html += `
        <li class="page-item ${page === totalPages ? 'disabled' : ''}">
            <a class="page-link" href="#" data-page="${next}">Suivant</a>
        </li>
    `;
    html += '</ul></nav>';
    paginationContainer.innerHTML = html;
    paginationContainer.querySelectorAll('a[data-page]').forEach(a => {
        a.addEventListener('click', (e) => {
            e.preventDefault();
            const p = Number(a.getAttribute('data-page'));
            if (p && p !== page) loadQuotations(p);
        });
    });
}

// Ouvrir le modal pour nouveau devis
function openQuotationModal(isEdit = false) {
    document.getElementById('quotationModalTitle').innerHTML = '<i class="bi bi-plus-circle me-2"></i>Nouveau Devis';
    document.getElementById('quotationForm').reset();
    document.getElementById('quotationId').value = '';
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
    
    // Pré-remplir un numéro depuis le serveur uniquement en création (évite d'écraser en édition)
    try {
        const input = document.getElementById('quotationNumber');
        if (input) {
            if (!isEdit) {
                input.value = '';
                input.placeholder = 'Chargement du numéro...';
                axios.get('/api/quotations/next-number').then(({ data }) => {
                    if (data && data.quotation_number) {
                        input.value = data.quotation_number;
                        input.placeholder = '';
                    } else {
                        // Laisser vide: le backend générera automatiquement un numéro séquentiel
                        input.value = '';
                        input.placeholder = 'Sera généré automatiquement';
                    }
                }).catch(() => {
                    // En cas d'échec API, laisser le serveur générer le numéro
                    input.value = '';
                    input.placeholder = 'Sera généré automatiquement';
                });
            } else {
                // En édition: ne pas modifier le numéro existant
                input.placeholder = '';
            }
        }
    } catch(e) { /* ignore */ }
    
    // Vider les articles
    quotationItems = [];
    updateQuotationItemsDisplay();
    calculateTotals();

    // Setup signature pad comme facture
    try {
        const canvas = document.getElementById('signatureCanvas');
        if (canvas) {
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
            document.getElementById('signatureClearBtn')?.addEventListener('click', () => {
                ctx.clearRect(0, 0, canvas.width, canvas.height);
            });
        }
    } catch (e) { /* ignore */ }

    // Toujours afficher le modal (utile pour l'édition où on ouvre par JS)
    try {
        const modalEl = document.getElementById('quotationModal');
        if (modalEl) bootstrap.Modal.getOrCreateInstance(modalEl).show();
    } catch (e) {}
}

// Gestion des articles de devis
function addQuotationItem() {
    const newItem = {
        id: Date.now(),
        product_id: '',
        product_name: '',
        is_custom: false,
        quantity: 1,
        unit_price: 0,
        total: 0
    };
    
    quotationItems.push(newItem);
    updateQuotationItemsDisplay();
}

// Ajouter une ligne libre/service (sans produit)
function addCustomItem() {
    const newItem = {
        id: Date.now(),
        product_id: null,
        product_name: 'Service personnalisé',
        is_custom: true,
        quantity: 1,
        unit_price: 0,
        total: 0
    };
    quotationItems.push(newItem);
    updateQuotationItemsDisplay();
}

// Ajouter une section (titre uniquement, sans quantité ni prix)
function addSectionRow() {
    const newItem = {
        id: Date.now(),
        is_section: true,
        section_title: 'Nouvelle section',
        product_id: null,
        product_name: '',
        quantity: 0,
        unit_price: 0,
        total: 0
    };
    quotationItems.push(newItem);
    updateQuotationItemsDisplay();
}

// Mettre à jour le titre d'une section
function updateSectionTitle(itemId, title) {
    const item = quotationItems.find(i => i.id === itemId);
    if (!item) return;
    item.section_title = String(title || '').trim();
}

// Exposer les fonctions pour les attributs onclick en HTML
window.addSectionRow = addSectionRow;
window.updateSectionTitle = updateSectionTitle;

function updateQuotationItemsDisplay() {
    const tbody = document.getElementById('quotationItemsBody');
    if (!tbody) return;

    if (quotationItems.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="7" class="text-center text-muted py-3">
                    <i class="bi bi-inbox me-2"></i>Aucun article ajouté
                </td>
            </tr>
        `;
        return;
    }

    tbody.innerHTML = quotationItems.map((item, index) => {
        // Ligne de section: juste un titre, pas de quantité ni prix
        if (item.is_section) {
            const title = escapeHtml(String(item.section_title || 'Nouvelle section').trim());
            return `
        <tr data-item-id="${item.id}" class="table-secondary">
            <td class="text-center align-middle drag-handle" style="cursor:grab;" title="Glisser pour réordonner">
                <i class="bi bi-grip-vertical text-muted fs-5"></i>
            </td>
            <td colspan="5">
                <input type="text" class="form-control form-control-sm fw-bold" value="${title}"
                       placeholder="Nom de section (ex: Matériel, Main d'œuvre)"
                       oninput="updateSectionTitle(${item.id}, this.value)">
            </td>
            <td>
                <button class="btn btn-sm btn-outline-danger" onclick="removeQuotationItem(${item.id})">
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
                <input type="text" class="form-control form-control-sm" value="${escapeHtml(item.product_name || '')}" placeholder="Libellé (ex: Installation Windows)" oninput="updateCustomName(${item.id}, this.value)">
                ` : `
                <div class="input-group input-group-sm">
                    <input type="text" class="form-control form-control-sm quotation-search-input" placeholder="Nom, code-barres ou n° série..." data-item-id="${item.id}" />
                    <select class="form-select" onchange="selectProduct(${item.id}, this.value)">
                    <option value="">Sélectionner un produit</option>
                        ${(() => {
                            const productList = Array.isArray(products) ? products : [];
                            // Si le produit actuel n'est pas dans la liste, l'ajouter en premier
                            const currentProductInList = item.product_id && productList.some(p => p.product_id == item.product_id);
                            let options = '';
                            if (item.product_id && !currentProductInList) {
                                options += `<option value="${item.product_id}" selected>${escapeHtml(item.product_name || 'Produit #' + item.product_id)} - ${formatCurrency(item.unit_price)}</option>`;
                            }
                            options += productList.map(product => {
                                const variants = productVariantsByProductId.get(Number(product.product_id)) || [];
                                const available = variants.filter(v => !v.is_sold).length;
                                const disabled = variants.length > 0 && available === 0;
                                const stock = productIdToStock.get(product.product_id) ?? 0;
                                return `
                                <option value="${product.product_id}" ${product.product_id == item.product_id ? 'selected' : ''} ${disabled ? 'disabled' : ''}>
                                    ${escapeHtml(product.name)} - ${formatCurrency(product.price)}${product.wholesale_price ? ` / Gros: ${formatCurrency(product.wholesale_price)}` : ''} ${disabled ? '(épuisé)' : `(stock: ${stock})`}
                                </option>`;
                            }).join('');
                            return options;
                        })()}
                </select>
                    <span class="input-group-text bg-light text-muted">${item.product_id ? `(stock: ${productIdToStock.get(Number(item.product_id)) ?? 0})` : ''}</span>
                </div>
                <div class="quotation-suggestions d-none" style="position:absolute; z-index:1050; max-height:240px; overflow:auto; width:28rem; box-shadow:0 2px 6px rgba(0,0,0,.15);"></div>`}
            </td>
            <td>
                <input type="number" class="form-control form-control-sm" value="${item.quantity}" min="1"
                       onchange="updateItemQuantity(${item.id}, this.value)">
            </td>
            <td>
                ${!item.is_custom && item.product_id ? (() => {
                    const product = products.find(p => p.product_id == item.product_id);
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
                <input type="number" class="form-control form-control-sm" value="${item.unit_price}" step="0.01" min="0"
                       onchange="updateItemPrice(${item.id}, this.value)">
            </td>
            <td><strong>${formatCurrency(item.total)}</strong></td>
            <td>
                <button class="btn btn-sm btn-outline-danger" onclick="removeQuotationItem(${item.id})">
                    <i class="bi bi-trash"></i>
                </button>
            </td>
        </tr>`;
    }).join('');
    
    // Initialiser le drag-and-drop si SortableJS est disponible
    initSortable();
}

// Initialiser SortableJS pour le drag-and-drop des lignes
function initSortable() {
    const tbody = document.getElementById('quotationItemsBody');
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
            // Réordonner quotationItems selon le nouvel ordre du DOM
            const rows = Array.from(tbody.querySelectorAll('tr[data-item-id]'));
            const newOrder = rows.map(row => Number(row.getAttribute('data-item-id')));
            const reordered = [];
            newOrder.forEach(id => {
                const item = quotationItems.find(i => i.id === id);
                if (item) reordered.push(item);
            });
            // Ajouter les items qui n'auraient pas été trouvés (sécurité)
            quotationItems.forEach(item => {
                if (!reordered.includes(item)) reordered.push(item);
            });
            quotationItems = reordered;
        }
    });
}

function updateCustomName(itemId, name) {
    const item = quotationItems.find(i => i.id === itemId);
    if (!item) return; item.product_name = name || '';
}

function selectProduct(itemId, productId, useBulkPrice = false) {
    const item = quotationItems.find(i => i.id === itemId);
    if (!item) return;
    
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
                    updateQuotationItemsDisplay();
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
        item.unit_price = (useBulkPrice && product.wholesale_price) ? product.wholesale_price : product.price;
        item.price_type = (useBulkPrice && product.wholesale_price) ? 'wholesale' : 'unit';
        // Si le produit a des variantes, la quantité suit le nombre d'IMEI sélectionnés (ici, 1 par défaut à la création du devis)
        const hasVariants = (productVariantsByProductId.get(Number(product.product_id)) || []).length > 0;
        if (hasVariants) {
            item.quantity = 1;
        }
        item.total = item.quantity * item.unit_price;
        // S'assurer que le produit est présent dans la liste locale pour l'affichage du <select>
        if (!products.some(p => Number(p.product_id) === Number(product.product_id))) {
            products.push(product);
        }
        // Précharger les variantes pour ce produit si fournies
        try {
            const variants = Array.isArray(product.variants) ? product.variants : [];
            if (variants.length) productVariantsByProductId.set(Number(product.product_id), variants);
        } catch (e) {}
        updateQuotationItemsDisplay();
        calculateTotals();
    }
}

function togglePriceType(itemId, useBulkPrice) {
    const item = quotationItems.find(i => i.id === itemId);
    if (!item || !item.product_id) return;
    selectProduct(itemId, item.product_id, useBulkPrice);
}

function updateItemQuantity(itemId, quantity) {
    const item = quotationItems.find(i => i.id === itemId);
    if (item) {
        item.quantity = parseInt(quantity) || 1;
        item.total = item.quantity * item.unit_price;

        updateQuotationItemsDisplay();
        calculateTotals();
    }
}

function updateItemPrice(itemId, price) {
    const item = quotationItems.find(i => i.id === itemId);
    if (item) {
        item.unit_price = parseFloat(price) || 0;
        item.total = item.quantity * item.unit_price;
        
        updateQuotationItemsDisplay();
        calculateTotals();
    }
}

function removeQuotationItem(itemId) {
    quotationItems = quotationItems.filter(i => i.id !== itemId);
    updateQuotationItemsDisplay();
    calculateTotals();
}

// Calculer les totaux
function calculateTotals() {
    const subtotal = quotationItems.reduce((sum, item) => sum + (Number(item.total)||0), 0);
    // Alignement facture: TVA activable, taux dynamique depuis UI
    const showTax = document.getElementById('showTaxSwitch')?.checked ?? true;
    let taxRatePct = parseFloat((document.getElementById('taxRateInput')?.value || '18').toString().replace(',', '.')) || 18;
    const taxRate = showTax ? Math.max(0, taxRatePct) / 100 : 0;
    const taxAmount = subtotal * taxRate;
    const total = subtotal + taxAmount;

    document.getElementById('subtotal').textContent = formatCurrency(subtotal);
    document.getElementById('taxAmount').textContent = formatCurrency(taxAmount);
    const taxLabel = document.getElementById('taxLabel');
    if (taxLabel) taxLabel.textContent = `TVA (${(taxRate * 100).toFixed(2)}%):`;
    document.getElementById('totalAmount').textContent = formatCurrency(total);
}

// Sauvegarder un devis
async function saveQuotation(status) {
    try {
        const quotationData = {
            quotation_number: document.getElementById('quotationNumber')?.value,
            client_id: parseInt(document.getElementById('clientSelect').value),
            date: document.getElementById('quotationDate').value,
            expiry_date: document.getElementById('validUntil').value || null,
            notes: document.getElementById('quotationNotes').value.trim() || null,
            status: status || 'SENT',
            show_item_prices: document.getElementById('showItemPricesSwitch')?.checked ?? true,
            show_section_totals: document.getElementById('showSectionTotalsSwitch')?.checked ?? true,
            // Inclure aussi les lignes personnalisées et sections (product_id null)
            items: quotationItems.flatMap(item => {
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
                        total: 0
                    }];
                }
                return [{
                    product_id: item.product_id ?? null,
                    product_name: item.product_name,
                    quantity: item.quantity,
                    price: item.unit_price,
                    total: item.total
                }];
            })
        };

        if (!quotationData.client_id || !quotationData.date || quotationData.items.length === 0) {
            showError('Veuillez remplir tous les champs obligatoires et ajouter au moins un article');
            return;
        }

        // Totaux côté client -> envoyer au backend
        try {
            const subtotal = quotationItems.reduce((s, it) => s + (Number(it.total)||0), 0);
            const showTax = document.getElementById('showTaxSwitch')?.checked ?? true;
            let taxRatePct = parseFloat((document.getElementById('taxRateInput')?.value || '18').toString().replace(',', '.')) || 18;
            const taxRate = showTax ? Math.max(0, taxRatePct) : 0;
            const taxAmount = subtotal * (taxRate/100);
            const total = subtotal + taxAmount;
            quotationData.subtotal = subtotal;
            quotationData.tax_rate = taxRate;
            quotationData.tax_amount = taxAmount;
            quotationData.total = total;
        } catch(e) {}

        // Ajouter signature (fichier PNG ou canvas)
        try {
            const fileInput = document.getElementById('signatureFile');
            const canvas = document.getElementById('signatureCanvas');
            let signatureDataUrl = null;
            if (fileInput && fileInput.files && fileInput.files[0]) {
                const file = fileInput.files[0];
                signatureDataUrl = await new Promise(res => { const r = new FileReader(); r.onload = () => res(String(r.result||'')); r.readAsDataURL(file); });
            } else if (canvas) {
                const tmp = document.createElement('canvas'); tmp.width = canvas.width; tmp.height = canvas.height;
                if (canvas.toDataURL() !== tmp.toDataURL()) signatureDataUrl = canvas.toDataURL('image/png');
            }
            if (signatureDataUrl) {
                quotationData.notes = (quotationData.notes || '') + `\n\n__SIGNATURE__=${signatureDataUrl}`;
            }
        } catch(e) {}

        const quotationId = document.getElementById('quotationId').value;
        const url = quotationId ? `/api/quotations/${quotationId}` : '/api/quotations/';
        if (quotationId) {
            await axios.put(url, quotationData);
        } else {
            await axios.post(url, quotationData);
        }

        // Fermer le modal
        const modal = bootstrap.Modal.getInstance(document.getElementById('quotationModal'));
        modal.hide();

        // Recharger la liste
        await loadQuotations();
        await loadStats();
        
        showSuccess(quotationId ? 'Devis enregistré avec succès' : 'Devis enregistré avec succès');
        
    } catch (error) {
        console.error('Erreur lors de la sauvegarde:', error);
        showError(error.response?.data?.detail || error.message || 'Erreur lors de la sauvegarde du devis');
    }
}

// Actions sur les devis
function viewQuotation(quotationId) {
    loadQuotationDetail(quotationId).catch(() => showError('Impossible de charger le devis'));
}

function editQuotation(quotationId) {
    preloadQuotationIntoForm(quotationId).catch(() => showError('Impossible de charger le devis pour édition'));
}

function convertToInvoice(quotationId) {
    if (!confirm('Convertir ce devis en facture ?')) return;
    (async () => {
        try {
            // Charger le devis pour préremplir le formulaire facture sans créer la facture tout de suite
            const { data: q } = await axios.get(`/api/quotations/${quotationId}`);
            const prefill = {
                fromQuotation: true,
                quotation_id: q.quotation_id,
                quotation_number: q.quotation_number,
                client_id: q.client_id,
                date: q.date,
                items: (q.items || []).map(it => ({
                    // keep null for custom lines; avoid 0/undefined surprises
                    product_id: (it.product_id === null || it.product_id === undefined) ? null : it.product_id,
                    product_name: it.product_name,
                    is_custom: (it.product_id === null || it.product_id === undefined),
                    quantity: Number(it.quantity || 0),
                    price: Number(it.price || 0),
                    total: Number(it.total || 0)
                }))
            };
            try { sessionStorage.setItem('prefill_invoice_from_quotation', JSON.stringify(prefill)); } catch(e) {}
            window.location.href = '/invoices';
        } catch (err) {
            const msg = err?.response?.data?.detail || err?.message || 'Erreur lors de la conversion';
            showError(msg);
        }
    })();
}

function printQuotation(quotationId) {
    // Impression dans une popup contrôlée (même UX que facture)
    const features = ['width=980','height=800','menubar=0','toolbar=0','location=0','status=0','scrollbars=1','resizable=1'].join(',');
    const w = window.open('', 'quotation_print_popup', features);
    if (!w) { showWarning('La fenêtre pop-up a été bloquée par le navigateur'); return; }
    w.document.write('<!DOCTYPE html><html><head><meta charset="utf-8"><title>Impression devis</title></head><body>Chargement...</body></html>');
    w.document.close();
    // Charger le HTML d'impression via fetch pour l'injecter
    fetch(`/quotations/print/${quotationId}`, { credentials: 'include' })
        .then(res => res.text())
        .then(html => { w.document.open(); w.document.write(html); w.document.close(); })
        .catch(() => { try { w.close(); } catch(e) {} showError('Impossible de charger la page d\'impression'); });
}

// Envoyer le devis par WhatsApp via n8n
async function sendQuotationWhatsApp(quotationId) {
    if (!quotationId) return;
    try {
        // Récupérer les infos du devis pour avoir le numéro du client
        const { data: quotation } = await axios.get(`/api/quotations/${quotationId}`);
        let phone = quotation.client?.phone || '';
        
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
        const response = await axios.post('/api/quotations/send-whatsapp', {
            quotation_id: parseInt(quotationId),
            phone: phone
        });
        
        if (response.data?.success) {
            showSuccess('Devis envoyé par WhatsApp avec succès!');
        } else {
            showError(response.data?.message || 'Erreur lors de l\'envoi WhatsApp');
        }
    } catch (error) {
        console.error('Erreur envoi WhatsApp:', error);
        showError(error.response?.data?.detail || 'Erreur lors de l\'envoi par WhatsApp');
    }
}

// Envoyer le devis par Email via n8n
async function sendQuotationEmail(quotationId) {
    if (!quotationId) return;
    try {
        // Récupérer les infos du devis pour avoir l'email du client
        const { data: quotation } = await axios.get(`/api/quotations/${quotationId}`);
        let email = quotation.client?.email || '';
        
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
        const response = await axios.post('/api/quotations/send-email', {
            quotation_id: parseInt(quotationId),
            email: email.trim()
        });
        
        if (response.data?.success) {
            showSuccess('Devis envoyé par email avec succès!');
        } else {
            showError(response.data?.message || 'Erreur lors de l\'envoi email');
        }
    } catch (error) {
        console.error('Erreur envoi email:', error);
        showError(error.response?.data?.detail || 'Erreur lors de l\'envoi par email');
    }
}

async function duplicateQuotation(quotationId) {
    try {
        // Récupérer les données du devis original
        const response = await axios.get(`/api/quotations/${quotationId}`);
        const data = response.data;
        
        console.log('[DuplicateQuotation] Données récupérées:', data);
        
        // Ouvrir le modal manuellement sans passer par openQuotationModal qui réinitialise tout
        const modalEl = document.getElementById('quotationModal');
        if (!modalEl) {
            showError('Erreur: formulaire de devis introuvable');
            return;
        }
        
        // Reset le formulaire d'abord
        const formEl = document.getElementById('quotationForm');
        if (formEl) formEl.reset();
        
        // Titre du modal
        document.getElementById('quotationModalTitle').innerHTML = '<i class="bi bi-copy me-2"></i>Dupliquer le Devis';
        
        // Ne pas renseigner l'ID pour créer un nouveau devis
        document.getElementById('quotationId').value = '';
        
        // Dates
        document.getElementById('quotationDate').value = new Date().toISOString().split('T')[0];
        document.getElementById('validUntil').value = new Date(Date.now() + 30*24*60*60*1000).toISOString().split('T')[0];
        
        // Numéro de devis - récupérer le prochain
        const numberEl = document.getElementById('quotationNumber');
        if (numberEl) {
            numberEl.value = '';
            numberEl.placeholder = 'Chargement...';
            axios.get('/api/quotations/next-number').then(({ data: numData }) => {
                if (numData && numData.quotation_number) {
                    numberEl.value = numData.quotation_number;
                    numberEl.placeholder = '';
                }
            }).catch(() => {
                numberEl.placeholder = 'Sera généré automatiquement';
            });
        }
        
        // Renseigner le client
        const hidden = document.getElementById('clientSelect');
        const input = document.getElementById('clientSearch');
        if (hidden && data.client_id) {
            hidden.value = data.client_id;
            console.log('[DuplicateQuotation] Client ID défini:', data.client_id);
        }
        // Chercher le nom du client dans la liste des clients
        if (input && data.client_id) {
            const cl = (clients || []).find(c => Number(c.client_id) === Number(data.client_id));
            if (cl) {
                input.value = cl.name || '';
                console.log('[DuplicateQuotation] Client name défini:', cl.name);
            }
        }
        
        // Notes
        const notesEl = document.getElementById('quotationNotes');
        if (notesEl) notesEl.value = data.notes || '';
        
        // TVA
        const taxInput = document.getElementById('taxRateInput');
        if (taxInput) taxInput.value = Number(data.tax_rate || 18);
        const showTaxSwitch = document.getElementById('showTaxSwitch');
        if (showTaxSwitch) showTaxSwitch.checked = (Number(data.tax_rate || 0) > 0);
        
        // Options d'affichage
        const showItemPricesSwitch = document.getElementById('showItemPricesSwitch');
        if (showItemPricesSwitch) showItemPricesSwitch.checked = data.show_item_prices !== false;
        const showSectionTotalsSwitch = document.getElementById('showSectionTotalsSwitch');
        if (showSectionTotalsSwitch) showSectionTotalsSwitch.checked = data.show_section_totals !== false;
        
        // Copier les articles comme lignes personnalisées
        const items = data.items || [];
        console.log('[DuplicateQuotation] Articles à copier:', items);
        
        quotationItems = items.map((it, idx) => {
            const pname = String(it.product_name || '');
            // Détecter les sections
            if (!it.product_id && pname.startsWith('[SECTION]')) {
                const title = pname.replace(/^\[SECTION\]\s*/, '').trim();
                return {
                    id: Date.now() + idx + Math.random(),
                    is_section: true,
                    section_title: title || 'Section',
                    product_id: null,
                    product_name: '',
                    quantity: 0,
                    unit_price: 0,
                    total: 0
                };
            }
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
        
        console.log('[DuplicateQuotation] quotationItems après copie:', quotationItems);
        
        // Afficher les articles
        updateQuotationItemsDisplay();
        calculateQuotationTotals();
        
        // Ouvrir le modal
        const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
        modal.show();
        
        showSuccess('Formulaire pré-rempli avec les données du devis.');
    } catch (error) {
        console.error('Erreur lors de la duplication:', error);
        showError('Erreur lors de la duplication du devis');
    }
}

async function deleteQuotation(quotationId) {
    if (!confirm('Êtes-vous sûr de vouloir supprimer ce devis ?')) {
        return;
    }

    try {
        await axios.delete(`/api/quotations/${quotationId}`);

        await loadQuotations();
        await loadStats();
        showSuccess('Devis supprimé avec succès');
        
    } catch (error) {
        console.error('Erreur lors de la suppression:', error);
        showError(error.response?.data?.detail || error.message || 'Erreur lors de la suppression du devis');
    }
}

// Envoyer le devis par WhatsApp via n8n
async function sendQuotationWhatsApp(quotationId) {
    if (!quotationId) return;
    try {
        // Récupérer les infos du devis depuis l'API pour avoir le client complet
        const { data: quotation } = await axios.get(`/api/quotations/${quotationId}`);
        let phone = quotation.client?.phone || '';
        
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
        const response = await axios.post('/api/quotations/send-whatsapp', {
            quotation_id: quotationId,
            phone: phone
        });
        
        if (response.data?.success) {
            showSuccess('Devis envoyé par WhatsApp avec succès!');
        } else {
            showError(response.data?.message || 'Erreur lors de l\'envoi WhatsApp');
        }
    } catch (error) {
        console.error('Erreur envoi WhatsApp:', error);
        showError(error.response?.data?.detail || 'Erreur lors de l\'envoi par WhatsApp');
    }
}
