// Gestion des mouvements de stock
let currentPage = 1;
const itemsPerPage = 20;
let movements = [];
let filteredMovements = [];
let selectedProductId = null; // défini lors du choix d'une variante

// Initialisation
document.addEventListener('DOMContentLoaded', function() {
    const ready = () => {
        const hasAuthManager = !!window.authManager;
        const hasUser = !!(hasAuthManager && window.authManager.userData && Object.keys(window.authManager.userData).length);
        return hasAuthManager && (window.authManager.isAuthenticatedSync?.() || hasUser);
    };

    const init = () => {
        Promise.all([
            loadMovements(),
            loadStats()
        ]).finally(() => {
            setupEventListeners();
            setDefaultDate();
        });
    };

    // Initialiser immédiatement sans délai pour un chargement instantané
    init();
});

function setupEventListeners() {
    // Filtres
    document.getElementById('typeFilter')?.addEventListener('change', () => { filterMovements(); loadStats(); });
    document.getElementById('productFilter')?.addEventListener('input', debounce(() => { filterMovements(); loadStats(); }, 300));
    document.getElementById('dateFromFilter')?.addEventListener('change', () => { filterMovements(); loadStats(); });
    document.getElementById('dateToFilter')?.addEventListener('change', () => { filterMovements(); loadStats(); });
}

function setDefaultDate() {
    const now = new Date();
    now.setMinutes(now.getMinutes() - now.getTimezoneOffset());
    const movementDate = document.getElementById('movementDate');
    if (movementDate) {
        movementDate.value = now.toISOString().slice(0, 16);
    }
}

// Charger les statistiques
async function loadStats() {
    try {
        // Utiliser la période choisie si fournie, sinon aujourd'hui (locale)
        const fromEl = document.getElementById('dateFromFilter');
        const toEl = document.getElementById('dateToFilter');

        let start = fromEl && fromEl.value ? fromEl.value : null;
        let end = toEl && toEl.value ? toEl.value : null;

        if (!start || !end) {
            const now = new Date();
            const yyyy = now.getFullYear();
            const mm = String(now.getMonth() + 1).padStart(2, '0');
            const dd = String(now.getDate()).padStart(2, '0');
            const todayStr = `${yyyy}-${mm}-${dd}`;
            if (!start) start = todayStr;
            if (!end) end = todayStr;
        }

        const url = `/api/stock-movements/stats?start_date=${encodeURIComponent(start)}&end_date=${encodeURIComponent(end)}`;
        const { data } = await axios.get(url);
        document.getElementById('totalMovements').textContent = data.total_movements ?? '0';
        document.getElementById('todayEntries').textContent = String(data.today_entries ?? '0');
        document.getElementById('todayExits').textContent = String(data.today_exits ?? '0');
        const stockValueEl = document.getElementById('stockValue');
        if (stockValueEl) stockValueEl.textContent = '-';
    } catch (error) {
        console.error('Erreur lors du chargement des statistiques:', error);
    }
}

// Charger la liste des mouvements
async function loadMovements() {
    try {
        showLoading();
        const { data } = await axios.get('/api/stock-movements/');
        movements = Array.isArray(data) ? data : (data.items || []);
        filteredMovements = [...movements];
        displayMovements();
        updatePagination();
    } catch (error) {
        console.error('Erreur lors du chargement des mouvements:', error);
        showError(error.response?.data?.detail || 'Erreur lors du chargement des mouvements');
        showEmptyState();
    }
}

// Afficher les mouvements
function displayMovements() {
    const tbody = document.getElementById('movementsTableBody');
    if (!tbody) return;

    if (filteredMovements.length === 0) {
        showEmptyState();
        return;
    }

    const startIndex = (currentPage - 1) * itemsPerPage;
    const endIndex = startIndex + itemsPerPage;
    const movementsToShow = filteredMovements.slice(startIndex, endIndex);
    tbody.innerHTML = movementsToShow.map(movement => {
        const productName = movement.product_name || 'Produit supprimé';
        // Extraire IMEI / référence depuis les notes si présent (format libre)
        const imeiFromNotes = movement.notes && movement.notes.includes('IMEI:') 
            ? movement.notes.split('IMEI:')[1].split(/[;\n]/)[0].trim()
            : null;
        const refFromNotes = movement.notes && movement.notes.includes('REF:') 
            ? movement.notes.split('REF:')[1].split(/[;\n]/)[0].trim()
            : null;
        return `
        <tr>
            <td>${formatDateTime(movement.created_at)}</td>
            <td>
                <span class="badge bg-${getMovementTypeBadgeColor(movement.movement_type)}">
                    ${getMovementTypeLabel(movement.movement_type)}
                </span>
            </td>
            <td>
                <div>
                    <strong>${escapeHtml(productName)}</strong>
                    ${movement.product_id ? `<br><small class="text-muted">ID: ${movement.product_id}</small>` : ''}
                </div>
            </td>
            <td>
                ${imeiFromNotes ? `
                    <div>
                        <code>${escapeHtml(imeiFromNotes)}</code>
                        ${refFromNotes ? `<br><small class="text-muted">REF: ${escapeHtml(refFromNotes)}</small>` : ''}
                    </div>
                ` : '<span class="text-muted">-</span>'}
            </td>
            <td>
                <span class="badge bg-${movement.quantity > 0 ? 'success' : 'danger'}">
                    ${movement.quantity > 0 ? '+' : ''}${movement.quantity}
                </span>
            </td>
            <td>${escapeHtml(refFromNotes || '-')}</td>
            <td>${escapeHtml(movement.user_name || 'Système')}</td>
            <td>
                <div class="btn-group" role="group">
                    <button class="btn btn-sm btn-outline-info" onclick="viewMovementDetails(${movement.movement_id})" title="Voir détails">
                        <i class="bi bi-eye"></i>
                    </button>
                    ${movement.movement_type === 'ADJUSTMENT' ? `
                        <button class="btn btn-sm btn-outline-danger" onclick="deleteMovement(${movement.movement_id})" title="Supprimer">
                            <i class="bi bi-trash"></i>
                        </button>
                    ` : ''}
                </div>
            </td>
        </tr>`;
    }).join('');
}

// plus de préchargement/lookup produit: l'API renvoie product_name directement

function showEmptyState() {
    const tbody = document.getElementById('movementsTableBody');
    if (tbody) {
        tbody.innerHTML = `
            <tr>
                <td colspan="8" class="text-center text-muted py-4">
                    <i class="bi bi-inbox fs-1 d-block mb-2"></i>
                    Aucun mouvement de stock trouvé
                </td>
            </tr>
        `;
    }
}

function showLoading() {
    const tbody = document.getElementById('movementsTableBody');
    if (tbody) {
        tbody.innerHTML = `
            <tr>
                <td colspan="8" class="text-center py-4 text-muted">
                    Chargement des mouvements...
                </td>
            </tr>
        `;
    }
}

// Utilitaires pour les types de mouvements
function getMovementTypeBadgeColor(type) {
    switch (type) {
        case 'IN': return 'success';
        case 'OUT': return 'danger';
        case 'ADJUSTMENT': return 'warning';
        default: return 'secondary';
    }
}

function getMovementTypeLabel(type) {
    switch (type) {
        case 'IN': return 'Entrée';
        case 'OUT': return 'Sortie';
        case 'ADJUSTMENT': return 'Ajustement';
        default: return type;
    }
}

// Filtrer les mouvements
function filterMovements() {
    const typeFilter = document.getElementById('typeFilter').value;
    const productFilter = document.getElementById('productFilter').value.toLowerCase().trim();
    const dateFromFilter = document.getElementById('dateFromFilter').value;
    const dateToFilter = document.getElementById('dateToFilter').value;

    filteredMovements = movements.filter(movement => {
        // Filtre par type
        if (typeFilter && movement.movement_type !== typeFilter) {
            return false;
        }

        // Filtre par produit
        if (productFilter && !(movement.product_name || '').toLowerCase().includes(productFilter)) {
            return false;
        }

        // Filtre par date
        const movementDate = new Date(movement.created_at).toISOString().split('T')[0];
        if (dateFromFilter && movementDate < dateFromFilter) {
            return false;
        }
        if (dateToFilter && movementDate > dateToFilter) {
            return false;
        }

        return true;
    });

    currentPage = 1;
    displayMovements();
    updatePagination();
}

// Pagination
function updatePagination() {
    const totalPages = Math.ceil(filteredMovements.length / itemsPerPage);
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
    const totalPages = Math.ceil(filteredMovements.length / itemsPerPage);
    if (page >= 1 && page <= totalPages) {
        currentPage = page;
        displayMovements();
        updatePagination();
    }
}

// Ouvrir le modal pour nouveau mouvement
function openMovementModal() {
    document.getElementById('movementForm').reset();
    setDefaultDate();
    document.getElementById('selectedVariantId').value = '';
    document.getElementById('variantSearchResults').innerHTML = '';
}

// Rechercher des variantes
async function searchVariants() {
    const searchTerm = document.getElementById('variantSearch').value.trim();
    if (!searchTerm) {
        showError('Veuillez saisir un terme de recherche');
        return;
    }

    try {
        const response = await fetch(`/api/stock-movements/search-variants?q=${encodeURIComponent(searchTerm)}`, {
            credentials: 'include'
        });

        if (!response.ok) {
            throw new Error('Erreur lors de la recherche');
        }

        const variants = await response.json();
        displayVariantSearchResults(variants);
    } catch (error) {
        console.error('Erreur lors de la recherche de variantes:', error);
        showError('Erreur lors de la recherche de variantes');
    }
}

// Afficher les résultats de recherche de variantes
function displayVariantSearchResults(variants) {
    const resultsDiv = document.getElementById('variantSearchResults');
    
    if (variants.length === 0) {
        resultsDiv.innerHTML = '<div class="alert alert-warning">Aucune variante trouvée</div>';
        return;
    }

    resultsDiv.innerHTML = variants.map(variant => `
        <div class="card mb-2">
            <div class="card-body p-2">
                <div class="d-flex justify-content-between align-items-center">
                    <div>
                        <strong>${escapeHtml(variant.product_name)}</strong><br>
                        <small class="text-muted">IMEI: ${escapeHtml(variant.imei_serial)}</small>
                        ${variant.attributes ? `<br><small>${escapeHtml(variant.attributes)}</small>` : ''}
                    </div>
                    <button class="btn btn-sm btn-primary" onclick="selectVariant(${variant.variant_id}, ${variant.product_id}, '${escapeHtml(variant.product_name)}', '${escapeHtml(variant.imei_serial)}')">
                        Sélectionner
                    </button>
                </div>
            </div>
        </div>
    `).join('');
}

// Sélectionner une variante
function selectVariant(variantId, productId, productName, imei) {
    document.getElementById('selectedVariantId').value = variantId;
    selectedProductId = productId || null;
    document.getElementById('variantSearch').value = `${productName} - ${imei}`;
    document.getElementById('variantSearchResults').innerHTML = '';
}

// Sauvegarder un mouvement
async function saveMovement() {
    try {
        const rawType = document.getElementById('movementType').value;
        const mappedType = rawType === 'ENTRY' || rawType === 'IN' ? 'IN' : (rawType === 'OUT' || rawType === 'EXIT' ? 'OUT' : rawType);
        const qty = parseInt(document.getElementById('movementQuantity').value);
        const reference = (document.getElementById('movementReference').value || '').trim();
        const manualNotes = (document.getElementById('movementNotes').value || '').trim();
        const imeiText = (document.getElementById('variantSearch').value || '').split(' - ').pop();

        if (!mappedType || !selectedProductId || !qty) {
            showError('Veuillez remplir tous les champs obligatoires');
            return;
        }

        const notesParts = [];
        if (imeiText) notesParts.push(`IMEI: ${imeiText}`);
        if (reference) notesParts.push(`REF: ${reference}`);
        if (manualNotes) notesParts.push(manualNotes);

        const movementData = {
            product_id: selectedProductId,
            quantity: qty,
            movement_type: mappedType,
            reference_type: null,
            reference_id: null,
            notes: notesParts.length ? notesParts.join('; ') : null,
            unit_price: 0
        };

        await axios.post('/api/stock-movements/', movementData);

        // Fermer le modal
        const modal = bootstrap.Modal.getInstance(document.getElementById('movementModal'));
        modal.hide();

        // Recharger la liste
        await preloadProducts();
        await loadMovements();
        await loadStats();
        
        showSuccess('Mouvement de stock créé avec succès');
        selectedProductId = null;
        
    } catch (error) {
        console.error('Erreur lors de la sauvegarde:', error);
        showError(error.response?.data?.detail || error.message || 'Erreur lors de la sauvegarde du mouvement');
    }
}

// Voir les détails d'un mouvement
function viewMovementDetails(movementId) {
    const movement = movements.find(m => m.movement_id === movementId);
    if (!movement) return;

    showInfo(`Détails du mouvement #${movementId}: ${getMovementTypeLabel(movement.movement_type)}`);
}

// Supprimer un mouvement (seulement les ajustements)
async function deleteMovement(movementId) {
    if (!confirm('Êtes-vous sûr de vouloir supprimer ce mouvement ?')) {
        return;
    }

    try {
        await axios.delete(`/api/stock-movements/${movementId}`);

        await loadMovements();
        await loadStats();
        showSuccess('Mouvement supprimé avec succès');
        
    } catch (error) {
        console.error('Erreur lors de la suppression:', error);
        showError(error.response?.data?.detail || error.message || 'Erreur lors de la suppression du mouvement');
    }
}
