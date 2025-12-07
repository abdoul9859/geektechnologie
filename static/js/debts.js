// Gestion des dettes
let debts = [];
let clients = [];
let suppliers = [];
let currentDebtId = null;
let currentPage = 1;
const itemsPerPage = 15;

// Initialisation (cookie-based auth readiness)
document.addEventListener('DOMContentLoaded', function() {
    const ready = () => {
        const hasAuthManager = !!window.authManager;
        const hasUser = !!(hasAuthManager && window.authManager.userData && Object.keys(window.authManager.userData).length);
        return hasAuthManager && (window.authManager.isAuthenticatedSync() || hasUser);
    };

    const boot = () => {
        loadDebts();
        loadClients();
        loadSuppliers();
        setupEventListeners();
        setupEntityAutocomplete();
        setDefaultDate();
    };

    // Initialiser immédiatement sans délai pour un chargement instantané
    boot();
});

// Configuration des écouteurs d'événements
function setupEventListeners() {
    // Filtres
    document.getElementById('searchInput').addEventListener('input', debounce(filterDebts, 300));
    document.getElementById('typeFilter').addEventListener('change', filterDebts);
    document.getElementById('statusFilter').addEventListener('change', filterDebts);
    document.getElementById('dateFromFilter').addEventListener('change', filterDebts);
    document.getElementById('dateToFilter').addEventListener('change', filterDebts);

    // Type de dette change
    document.getElementById('debtType').addEventListener('change', handleDebtTypeChange);
    
    // Auto-ajuster l'échéance si la date de création change (pour créance client)
    document.getElementById('debtDate').addEventListener('change', () => {
        if (document.getElementById('debtType').value === 'client') {
            const creationDateInput = document.getElementById('debtDate');
            const dueDateInput = document.getElementById('dueDate');
            if (creationDateInput && dueDateInput) {
                const creationDate = new Date(creationDateInput.value);
                if (!isNaN(creationDate.getTime())) {
                    const dueDate = new Date(creationDate);
                    dueDate.setMonth(dueDate.getMonth() + 1);
                    dueDateInput.value = dueDate.toISOString().split('T')[0];
                }
            }
        }
    });

    // Modal de suppression
    const confirmDeleteBtn = document.getElementById('confirmDeleteBtn');
    if (confirmDeleteBtn) {
        confirmDeleteBtn.addEventListener('click', confirmDelete);
    }

    // Formulaires
    const debtForm = document.getElementById('debtForm');
    if (debtForm) {
        debtForm.addEventListener('submit', handleDebtFormSubmit);
    }

    const paymentForm = document.getElementById('paymentForm');
    if (paymentForm) {
        paymentForm.addEventListener('submit', handlePaymentFormSubmit);
    }

    // Enforcer des montants entiers dans les champs numériques pertinents
    const amountInput = document.getElementById('amount');
    if (amountInput) enforceIntegerInput(amountInput);
    const paymentAmountInput = document.getElementById('paymentAmount');
    if (paymentAmountInput) enforceIntegerInput(paymentAmountInput);
    const initPaymentAmountInput = document.getElementById('initPaymentAmount');
    if (initPaymentAmountInput) enforceIntegerInput(initPaymentAmountInput);
}

// Gérer l'autocomplétion du champ client/fournisseur
function setupEntityAutocomplete() {
    const input = document.getElementById('entitySelect');
    const dropdown = document.getElementById('entityDropdown');
    if (!input || !dropdown) return;

    input.addEventListener('focus', () => {
        showAllEntities();
    });

    input.addEventListener('input', () => {
        const query = input.value.toLowerCase().trim();
        if (query.length === 0) {
            showAllEntities();
        } else {
            filterEntities(query);
        }
    });

    input.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            hideDropdown();
        }
    });

    document.addEventListener('click', (e) => {
        if (!input.contains(e.target) && !dropdown.contains(e.target)) {
            hideDropdown();
        }
    });
}

function showAllEntities() {
    const dropdown = document.getElementById('entityDropdown');
    const debtType = document.getElementById('debtType').value;
    const entities = debtType === 'client' ? clients : suppliers;
    if (!dropdown || !Array.isArray(entities)) return;
    dropdown.innerHTML = '';
    entities.forEach(entity => {
        const li = document.createElement('li');
        const name = entity.name || 'Sans nom';
        const id = entity.client_id ?? entity.supplier_id ?? entity.id;
        li.innerHTML = `<a class="dropdown-item" href="#" data-id="${id}">${escapeHtml(name)}</a>`;
        li.addEventListener('click', (e) => {
            e.preventDefault();
            selectEntity(id, name);
        });
        dropdown.appendChild(li);
    });
    dropdown.style.display = 'block';
}

function filterEntities(query) {
    const dropdown = document.getElementById('entityDropdown');
    const debtType = document.getElementById('debtType').value;
    const entities = debtType === 'client' ? clients : suppliers;
    if (!dropdown || !Array.isArray(entities)) return;
    dropdown.innerHTML = '';
    const filtered = entities.filter(entity =>
        (entity.name || '').toLowerCase().includes(query)
    );
    if (filtered.length === 0) {
        const li = document.createElement('li');
        li.innerHTML = `<span class="dropdown-item text-muted">Aucun résultat</span>`;
        dropdown.appendChild(li);
    } else {
        filtered.forEach(entity => {
            const li = document.createElement('li');
            const name = entity.name || 'Sans nom';
            const id = entity.client_id ?? entity.supplier_id ?? entity.id;
            li.innerHTML = `<a class="dropdown-item" href="#" data-id="${id}">${escapeHtml(name)}</a>`;
            li.addEventListener('click', (e) => {
                e.preventDefault();
                selectEntity(id, name);
            });
            dropdown.appendChild(li);
        });
    }
    dropdown.style.display = 'block';
}

function selectEntity(id, name) {
    const input = document.getElementById('entitySelect');
    input.value = name;
    input.dataset.selectedId = id;
    hideDropdown();
}

function hideDropdown() {
    const dropdown = document.getElementById('entityDropdown');
    if (dropdown) dropdown.style.display = 'none';
}

function getSelectedEntityId() {
    const input = document.getElementById('entitySelect');
    return input ? parseInt(input.dataset.selectedId || input.value) : null;
}

// Toggle affichage création rapide client (global)
function toggleNewClient(show = true) {
    try {
        const t = document.getElementById('debtType').value;
        const section = document.getElementById('newClientSection');
        if (!section) return;
        if (t !== 'client') { section.style.display = 'none'; return; }
        section.style.display = show ? '' : 'none';
    } catch (e) {}
}

// Création rapide de client depuis la modale (global)
async function quickAddClient() {
    try {
        const name = (document.getElementById('newClientName').value || '').trim();
        const phone = (document.getElementById('newClientPhone').value || '').trim();
        const email = (document.getElementById('newClientEmail').value || '').trim();
        if (!name) { showError('Veuillez saisir le nom du client'); return; }
        const payload = { name, phone: phone || undefined, email: email || undefined };
        const { data } = await axios.post('/api/clients', payload);
        if (!data || (!data.client_id && !data.id)) {
            showError('Création du client échouée');
            return;
        }
        try {
            if (!Array.isArray(clients)) clients = [];
            // Normaliser l'objet client pour notre usage
            const newClient = {
                client_id: data.client_id ?? data.id,
                name: data.name,
                email: data.email,
                phone: data.phone
            };
            // Éviter les doublons
            if (!clients.some(c => Number(c.client_id ?? c.id) === Number(newClient.client_id))) {
                clients.push(newClient);
            }
        } catch (e) {}
        updateEntitySelect();
        selectEntity(data.client_id ?? data.id, data.name);
        toggleNewClient(false);
        showSuccess('Client créé');
    } catch (error) {
        console.error('Erreur création client:', error);
        showError(error?.response?.data?.detail || 'Erreur lors de la création du client');
    }
}

// Définir les dates par défaut
function setDefaultDate() {
    const today = new Date().toISOString().split('T')[0];
    document.getElementById('debtDate').value = today;
    document.getElementById('paymentDate').value = today;
    const initPayEl = document.getElementById('initPaymentDate');
    if (initPayEl) initPayEl.value = today;
}

// Charger les dettes
async function loadDebts() {
    try {
        showLoading();
        const response = await safeLoadData(
            () => axios.get('/api/debts/', { params: { limit: 10000, skip: 0 } }),
            {
                timeout: 8000,
                fallbackData: [],
                errorMessage: 'Erreur lors du chargement des dettes'
            }
        );
        const payload = response?.data ?? [];
        if (Array.isArray(payload)) {
            debts = payload;
        } else if (payload && Array.isArray(payload.debts)) {
            debts = payload.debts;
        } else if (payload && Array.isArray(payload.data)) {
            debts = payload.data;
        } else {
            debts = [];
        }

        displayDebts();
        updateStatistics();
    } catch (error) {
        console.error('Erreur:', error);
        showError(error.response?.data?.detail || 'Erreur lors du chargement des dettes');
        // Afficher un état vide pour éviter le spinner infini
        const tbody = document.getElementById('debtsTableBody');
        if (tbody) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="10" class="text-center py-4">
                        <i class="bi bi-credit-card display-4 text-muted"></i>
                        <p class="text-muted mt-2 mb-0">Aucune dette trouvée</p>
                    </td>
                </tr>
            `;
        }
    }
}

// Charger les clients
async function loadClients() {
    try {
        const response = await safeLoadData(
            () => axios.get('/api/clients'),
            { timeout: 8000, fallbackData: [], errorMessage: 'Erreur lors du chargement des clients' }
        );
        const data = response?.data ?? [];
        clients = Array.isArray(data) ? data : (data.items || data.clients || []);
    } catch (error) {
        console.error('Erreur lors du chargement des clients:', error);
    }
}

// Charger les fournisseurs
async function loadSuppliers() {
    try {
        const response = await safeLoadData(
            () => axios.get('/api/suppliers/'),
            { timeout: 8000, fallbackData: [], errorMessage: 'Erreur lors du chargement des fournisseurs' }
        );
        const data = response?.data ?? [];
        suppliers = Array.isArray(data) ? data : (data.items || data.suppliers || []);
    } catch (error) {
        console.error('Erreur lors du chargement des fournisseurs:', error);
    }
}

// Afficher les dettes
function displayDebts() {
    const filteredDebts = getFilteredDebts();
    const startIndex = (currentPage - 1) * itemsPerPage;
    const endIndex = startIndex + itemsPerPage;
    const paginatedDebts = filteredDebts.slice(startIndex, endIndex);

    const tbody = document.getElementById('debtsTableBody');
    tbody.innerHTML = '';

    if (paginatedDebts.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="10" class="text-center py-4">
                    <i class="bi bi-credit-card display-4 text-muted"></i>
                    <p class="text-muted mt-2 mb-0">Aucune dette trouvée</p>
                </td>
            </tr>
        `;
    } else {
        paginatedDebts.forEach(debt => {
            const row = createDebtRow(debt);
            tbody.appendChild(row);
        });
    }

    updateResultsCount(filteredDebts.length);
    updatePagination(filteredDebts.length);
}

// Créer une ligne de dette
function createDebtRow(debt) {
    const row = document.createElement('tr');
    const entityName = getEntityName(debt);
    const remaining = (debt.amount || 0) - (debt.paid_amount || 0);
    const canAddPayment = (debt.type === 'client' && !debt.has_invoice && remaining > 0);
    
    row.innerHTML = `
        <td>
            <strong>${escapeHtml(debt.reference)}</strong>
        </td>
        <td>
            <span class="badge ${getTypeBadgeClass(debt.type)}">
                <i class="bi ${getTypeIcon(debt.type)} me-1"></i>
                ${getTypeLabel(debt.type)}
            </span>
        </td>
        <td>${(debt.type === 'client' && debt.entity_id) ? `<a href="/clients/debts?client_id=${encodeURIComponent(debt.entity_id)}">${escapeHtml(entityName)}</a>` : escapeHtml(entityName)}</td>
        <td>${formatDate(debt.date)}</td>
        <td>
            ${debt.due_date ? formatDate(debt.due_date) : '-'}
            ${debt.due_date && isOverdue(debt.due_date, debt.status) ? 
                '<span class="badge bg-danger ms-1">En retard</span>' : ''
            }
        </td>
        <td>
            <strong>${formatCurrency(debt.amount)}</strong>
        </td>
        <td>
            <span class="text-success">${formatCurrency(debt.paid_amount || 0)}</span>
        </td>
        <td>
            <span class="text-${remaining > 0 ? 'danger' : 'success'}">${formatCurrency(remaining)}</span>
        </td>
        <td>
            <span class="badge ${getStatusBadgeClass(debt.status)}">
                ${getStatusLabel(debt.status)}
            </span>
        </td>
        <td>
            <div class="btn-group btn-group-sm">
                ${canAddPayment ? `
                <button class="btn btn-outline-success" onclick="addPayment(${debt.id})" title="Ajouter un paiement">
                    <i class="bi bi-cash-coin"></i>
                </button>` : ''}
                ${debt.has_invoice ? `
                <button class="btn btn-outline-primary" onclick="viewDebt(${debt.id})" title="Voir la facture">
                    <i class="bi bi-receipt"></i>
                </button>` : ''}
                ${(debt.type === 'client' && !debt.has_invoice) ? `
                <button class="btn btn-outline-danger" onclick="deleteDebt(${debt.id})" title="Supprimer la créance">
                    <i class="bi bi-trash"></i>
                </button>` : ''}
            </div>
        </td>
    `;

    return row;
}

// Obtenir les dettes filtrées
function getFilteredDebts() {
    // Vérifier si debts est un tableau valide
    if (!Array.isArray(debts)) {
        console.error('La variable debts n\'est pas un tableau:', debts);
        return [];
    }
    
    let filtered = [...debts];

    const searchTerm = document.getElementById('searchInput').value.toLowerCase();
    const typeFilter = document.getElementById('typeFilter').value;
    const statusFilter = document.getElementById('statusFilter').value;
    const dateFromFilter = document.getElementById('dateFromFilter').value;
    const dateToFilter = document.getElementById('dateToFilter').value;

    if (searchTerm) {
        filtered = filtered.filter(debt => 
            debt.reference.toLowerCase().includes(searchTerm) ||
            debt.description?.toLowerCase().includes(searchTerm) ||
            getEntityName(debt).toLowerCase().includes(searchTerm)
        );
    }

    if (typeFilter) {
        filtered = filtered.filter(debt => debt.type === typeFilter);
    }

    if (statusFilter) {
        filtered = filtered.filter(debt => debt.status === statusFilter);
    }

    if (dateFromFilter) {
        filtered = filtered.filter(debt => debt.date >= dateFromFilter);
    }

    if (dateToFilter) {
        filtered = filtered.filter(debt => debt.date <= dateToFilter);
    }

    // Trier par date (plus récent en premier)
    filtered.sort((a, b) => new Date(b.date) - new Date(a.date));

    return filtered;
}

// Filtrer les dettes
function filterDebts() {
    currentPage = 1;
    displayDebts();
}

// Effacer les filtres
function clearFilters() {
    document.getElementById('searchInput').value = '';
    document.getElementById('typeFilter').value = '';
    document.getElementById('statusFilter').value = '';
    document.getElementById('dateFromFilter').value = '';
    document.getElementById('dateToFilter').value = '';
    filterDebts();
}

// Mettre à jour les statistiques
function updateStatistics() {
    const clientDebts = debts.filter(d => d.type === 'client').reduce((sum, d) => sum + (d.amount || 0), 0);
    const supplierDebts = debts.filter(d => d.type === 'supplier').reduce((sum, d) => sum + (d.amount || 0), 0);
    const totalPaid = debts.reduce((sum, d) => sum + (d.paid_amount || 0), 0);
    const totalRemaining = debts.reduce((sum, d) => sum + ((d.amount || 0) - (d.paid_amount || 0)), 0);

    document.getElementById('totalClientDebts').textContent = formatCurrency(clientDebts);
    document.getElementById('totalSupplierDebts').textContent = formatCurrency(supplierDebts);
    document.getElementById('totalPaidDebts').textContent = formatCurrency(totalPaid);
    document.getElementById('totalRemainingDebts').textContent = formatCurrency(totalRemaining);
}

// Mettre à jour le compteur de résultats
function updateResultsCount(count) {
    const resultsCount = document.getElementById('resultsCount');
    if (resultsCount) {
        resultsCount.textContent = `${count} dette${count !== 1 ? 's' : ''}`;
    }
}

// Créer une nouvelle dette
function createDebt() {
    resetDebtForm();
    document.getElementById('debtType').value = 'client';
    handleDebtTypeChange();
    const modal = new bootstrap.Modal(document.getElementById('debtModal'));
    modal.show();
}

// Réinitialiser le formulaire de dette
function resetDebtForm() {
    const form = document.getElementById('debtForm');
    form.reset();
    
    document.getElementById('debtId').value = '';
    setDefaultDate();
    
    const modalTitle = document.getElementById('debtModalTitle');
    modalTitle.innerHTML = '<i class="bi bi-plus-circle me-2"></i>Nouvelle Dette';
    
    currentDebtId = null;
    updateEntitySelect();
}

// Gérer le changement de type de dette
function handleDebtTypeChange() {
    updateEntitySelect();
    const t = document.getElementById('debtType').value;
    const initSection = document.getElementById('initialPaymentSection');
    if (initSection) initSection.style.display = (t === 'client') ? '' : 'none';
    const newClientBtn = document.getElementById('newClientBtn');
    if (newClientBtn) newClientBtn.style.display = (t === 'client') ? '' : 'none';
    if (t !== 'client') toggleNewClient(false);
    // Auto-set due date to 1 month later for client debts
    if (t === 'client') {
        const creationDateInput = document.getElementById('debtDate');
        const dueDateInput = document.getElementById('dueDate');
        if (creationDateInput && dueDateInput) {
            const creationDate = new Date(creationDateInput.value);
            if (!isNaN(creationDate.getTime())) {
                const dueDate = new Date(creationDate);
                dueDate.setMonth(dueDate.getMonth() + 1);
                dueDateInput.value = dueDate.toISOString().split('T')[0];
            }
        }
    }
}

// Mettre à jour le sélecteur d'entité
function updateEntitySelect() {
    const debtType = document.getElementById('debtType').value;
    const entityLabel = document.getElementById('entityLabel');
    const entityInput = document.getElementById('entitySelect');
    
    if (debtType === 'client') {
        entityLabel.textContent = 'Client';
        entityInput.placeholder = 'Rechercher un client...';
    } else if (debtType === 'supplier') {
        entityLabel.textContent = 'Fournisseur';
        entityInput.placeholder = 'Rechercher un fournisseur...';
    } else {
        entityLabel.textContent = 'Client/Fournisseur';
        entityInput.placeholder = 'Rechercher...';
    }
    // Vider le champ et le dataset pour forcer une nouvelle sélection
    entityInput.value = '';
    delete entityInput.dataset.selectedId;
    // Masquer la dropdown au changement de type
    hideDropdown();
}

// Modifier une dette
function editDebt(id) {
    const d = debts.find(x => x.id === id);
    if (!d) return;
    if (d.type !== 'supplier') {
        showInfo('Les créances clients ne sont pas modifiables ici.');
        return;
    }
    document.getElementById('debtId').value = d.id;
    document.getElementById('debtType').value = 'supplier';
    handleDebtTypeChange();
    document.getElementById('reference').value = d.reference || '';
    document.getElementById('amount').value = Math.round(d.amount || 0);
    document.getElementById('debtDate').value = (d.date || '').split('T')[0] || '';
    document.getElementById('dueDate').value = d.due_date ? String(d.due_date).split('T')[0] : '';
    document.getElementById('paidAmount').value = d.paid_amount || 0;
    document.getElementById('debtStatus').value = d.status || 'pending';
    document.getElementById('description').value = d.description || '';
    document.getElementById('notes').value = d.notes || '';
    setTimeout(() => {
        const entityInput = document.getElementById('entitySelect');
        const entity = d.type === 'client' 
            ? clients.find(c => (c.client_id ?? c.id) === d.entity_id)
            : suppliers.find(s => (s.supplier_id ?? s.id) === d.entity_id);
        if (entity) {
            selectEntity(d.entity_id, entity.name);
        }
    }, 50);
    currentDebtId = d.id;
    const modal = new bootstrap.Modal(document.getElementById('debtModal'));
    modal.show();
}

// Gérer la soumission du formulaire de dette
async function handleDebtFormSubmit(e) {
    e.preventDefault();
    await saveDebt();
}

// Enregistrer la dette
async function saveDebt() {
    const debtData = {
        type: document.getElementById('debtType').value,
        reference: document.getElementById('reference').value,
        entity_id: getSelectedEntityId(),
        amount: Math.round(parseFloat(document.getElementById('amount').value)),
        date: document.getElementById('debtDate').value,
        due_date: document.getElementById('dueDate').value || null,
        description: document.getElementById('description').value,
        notes: document.getElementById('notes').value
    };

    if (!debtData.entity_id) { 
        showError(debtData.type === 'client' ? 'Veuillez sélectionner un client' : 'Veuillez sélectionner un fournisseur'); 
        return; 
    }
    if (!debtData.reference.trim()) { showError('Veuillez saisir une référence'); return; }
    if (!debtData.amount || debtData.amount <= 0) { showError('Veuillez saisir un montant valide'); return; }
    // Création: toujours non payé au départ

    try {
        if (debtData.type === 'client') {
            // Créer une créance client manuelle (sans facture)
            const { data: created } = await axios.post('/api/debts', debtData);
            // Paiement initial optionnel
            try {
                const rawAmt = document.getElementById('initPaymentAmount').value;
                const payAmt = Math.floor(Number(String(rawAmt).replace(',', '.')));
                if (Number.isFinite(payAmt) && payAmt > 0) {
                    const payDateStr = document.getElementById('initPaymentDate').value;
                    const payMethod = document.getElementById('initPaymentMethod').value || 'especes';
                    const payRef = document.getElementById('initPaymentReference').value || undefined;
                    const payload = {
                        amount: payAmt,
                        date: payDateStr || undefined,
                        method: payMethod,
                        reference: payRef,
                        notes: 'Paiement initial (créance client)'
                    };
                    const debtId = created?.id;
                    if (debtId) {
                        await axios.post(`/api/debts/${debtId}/payments`, payload);
                    }
                }
            } catch (e) {
                console.warn('Erreur paiement initial:', e);
            }
        } else {
            if (currentDebtId) {
                await axios.put(`/api/debts/${currentDebtId}`, debtData);
            } else {
                await axios.post('/api/debts', debtData);
            }
        }
        const createdMsg = debtData.type === 'client' 
            ? (currentDebtId ? 'Créance client modifiée' : 'Créance client créée') 
            : (currentDebtId ? 'Dette fournisseur modifiée' : 'Dette fournisseur créée');
        showSuccess(createdMsg);
        const modal = bootstrap.Modal.getInstance(document.getElementById('debtModal'));
        modal.hide();
        loadDebts();
    } catch (error) {
        console.error('Erreur:', error);
        showError(error.response?.data?.detail || 'Erreur lors de l\'enregistrement');
    }
}

// Ajouter un paiement
function addPayment(id) {
    const debt = debts.find(d => d.id === id);
    if (!debt) return;

    const remaining = (debt.amount || 0) - (debt.paid_amount || 0);
    const remainingInt = Math.max(0, Math.floor(remaining));
    
    if (remainingInt <= 0) {
        showInfo('Cette dette est déjà entièrement payée');
        return;
    }

    // Remplir les informations de la dette
    document.getElementById('paymentDebtId').value = debt.id;
    document.getElementById('paymentAmount').value = remainingInt;
    document.getElementById('paymentAmount').max = remainingInt;
    
    const debtInfo = document.getElementById('paymentDebtInfo');
    debtInfo.innerHTML = `
        <div class="d-flex justify-content-between">
            <span><strong>Référence:</strong> ${escapeHtml(debt.reference)}</span>
            <span><strong>Type:</strong> ${getTypeLabel(debt.type)}</span>
        </div>
        <div class="d-flex justify-content-between mt-2">
            <span><strong>Montant total:</strong> ${formatCurrency(debt.amount)}</span>
            <span><strong>Déjà payé:</strong> ${formatCurrency(debt.paid_amount || 0)}</span>
        </div>
        <div class="d-flex justify-content-between mt-2">
            <span><strong>Restant à payer:</strong></span>
            <span class="text-danger"><strong>${formatCurrency(remaining)}</strong></span>
        </div>
    `;

    // Afficher la modal
    const modal = new bootstrap.Modal(document.getElementById('paymentModal'));
    modal.show();
}

// Gérer la soumission du formulaire de paiement
async function handlePaymentFormSubmit(e) {
    e.preventDefault();
    await savePayment();
}

// Enregistrer le paiement
async function savePayment() {
    const debtId = document.getElementById('paymentDebtId').value;
    const paymentData = {
        amount: Math.round(parseFloat(document.getElementById('paymentAmount').value)),
        date: document.getElementById('paymentDate').value,
        method: document.getElementById('paymentMethod').value,
        notes: document.getElementById('paymentNotes').value
    };

    // Validation
    if (!paymentData.amount || paymentData.amount <= 0) {
        showError('Veuillez saisir un montant valide');
        return;
    }

    if (!paymentData.date) {
        showError('Veuillez saisir une date de paiement');
        return;
    }

    try {
        await axios.post(`/api/debts/${debtId}/payments`, paymentData);

        showSuccess('Paiement enregistré avec succès');
        
        // Fermer la modal
        const modal = bootstrap.Modal.getInstance(document.getElementById('paymentModal'));
        modal.hide();
        
        // Recharger les données
        loadDebts();
    } catch (error) {
        console.error('Erreur:', error);
        showError(error.response?.data?.detail || 'Erreur lors de l\'enregistrement du paiement');
    }
}

// Voir une dette
function viewDebt(id) {
    try {
        sessionStorage.setItem('invoiceSearchQuery', String(id));
        sessionStorage.setItem('open_invoice_detail_id', String(id));
    } catch (e) {}
    // Rediriger vers la page des factures (elle ouvrira directement la modale de détail)
    window.location.href = `/invoices`;
}

// Supprimer une dette
function deleteDebt(id) {
    currentDebtId = id;
    const modal = new bootstrap.Modal(document.getElementById('deleteModal'));
    modal.show();
}

// Confirmer la suppression
async function confirmDelete() {
    if (!currentDebtId) return;
    const d = debts.find(x => x.id === currentDebtId);
    if (!d) return;
    // Interdire la suppression des créances issues d'une facture
    if (d.has_invoice) {
        showInfo("Impossible de supprimer une créance issue d'une facture.");
        return;
    }
    try {
        await axios.delete(`/api/debts/${currentDebtId}`);
        const modal = bootstrap.Modal.getInstance(document.getElementById('deleteModal'));
        if (modal) modal.hide();
        const msg = (d.type === 'client') ? 'Créance client supprimée' : 'Dette fournisseur supprimée';
        showSuccess(msg);
        loadDebts();
        currentDebtId = null;
    } catch (error) {
        showError(error.response?.data?.detail || 'Erreur lors de la suppression');
    }
}

// Utilitaires
function getEntityName(debt) {
    // Si le backend fournit déjà le nom, l'utiliser en priorité
    if (debt && debt.entity_name) return debt.entity_name;
    if (debt.type === 'client') {
        const client = clients.find(c => (c.client_id ?? c.id) === debt.entity_id);
        return client ? client.name : 'Client inconnu';
    } else if (debt.type === 'supplier') {
        const supplier = suppliers.find(s => (s.supplier_id ?? s.id) === debt.entity_id);
        return supplier ? supplier.name : 'Fournisseur inconnu';
    }
    return 'N/A';
}

function getTypeBadgeClass(type) {
    switch (type) {
        case 'client': return 'bg-primary';
        case 'supplier': return 'bg-warning text-dark';
        case 'invoice': return 'bg-info';
        default: return 'bg-secondary';
    }
}

function getTypeIcon(type) {
    switch (type) {
        case 'client': return 'bi-people';
        case 'supplier': return 'bi-truck';
        case 'invoice': return 'bi-receipt';
        default: return 'bi-tag';
    }
}

function getTypeLabel(type) {
    switch (type) {
        case 'client': return 'Créance client';
        case 'supplier': return 'Dette fournisseur';
        case 'invoice': return 'Facture';
        case 'other': return 'Autre';
        default: return type;
    }
}

function getStatusBadgeClass(status) {
    switch (status) {
        case 'paid': return 'bg-success';
        case 'partial': return 'bg-warning text-dark';
        case 'overdue': return 'bg-danger';
        default: return 'bg-secondary';
    }
}

function getStatusLabel(status) {
    switch (status) {
        case 'pending': return 'En attente';
        case 'partial': return 'Partiel';
        case 'paid': return 'Payé';
        case 'overdue': return 'En retard';
        default: return status;
    }
}

function isOverdue(dueDate, status) {
    if (status === 'paid') return false;
    return new Date(dueDate) < new Date();
}

function formatDate(dateString) {
    if (!dateString) return '-';
    const options = { year: 'numeric', month: 'short', day: 'numeric' };
    const d = new Date(dateString);
    if (isNaN(d.getTime())) return '-';
    return d.toLocaleDateString('fr-FR', options);
}

function formatCurrency(amount) {
    return new Intl.NumberFormat('fr-FR', { 
        style: 'currency', 
        currency: 'XOF',
        minimumFractionDigits: 0,
        maximumFractionDigits: 0
    }).format(Math.round(amount || 0));
}

function updatePagination(totalItems) {
    const totalPages = Math.ceil(totalItems / itemsPerPage);
    const pagination = document.getElementById('pagination');
    
    pagination.innerHTML = '';
    
    if (totalPages <= 1) return;
    
    // Bouton précédent
    const prevLi = document.createElement('li');
    prevLi.className = `page-item ${currentPage === 1 ? 'disabled' : ''}`;
    prevLi.innerHTML = `<a class="page-link" href="#" onclick="changePage(${currentPage - 1})">Précédent</a>`;
    pagination.appendChild(prevLi);
    
    // Pages
    for (let i = 1; i <= totalPages; i++) {
        const li = document.createElement('li');
        li.className = `page-item ${i === currentPage ? 'active' : ''}`;
        li.innerHTML = `<a class="page-link" href="#" onclick="changePage(${i})">${i}</a>`;
        pagination.appendChild(li);
    }
    
    // Bouton suivant
    const nextLi = document.createElement('li');
    nextLi.className = `page-item ${currentPage === totalPages ? 'disabled' : ''}`;
    nextLi.innerHTML = `<a class="page-link" href="#" onclick="changePage(${currentPage + 1})">Suivant</a>`;
    pagination.appendChild(nextLi);
}

// Force un entier dans un input type number (gère aussi la virgule française)
function enforceIntegerInput(input) {
    input.setAttribute('step', '1');
    input.addEventListener('input', () => {
        const raw = String(input.value).replace(',', '.');
        const n = Math.floor(Number(raw));
        if (!Number.isFinite(n) || n < 0) {
            input.value = '';
        } else {
            input.value = String(n);
        }
    });
}

function changePage(page) {
    const totalItems = getFilteredDebts().length;
    const totalPages = Math.ceil(totalItems / itemsPerPage);
    
    if (page < 1 || page > totalPages) return;
    
    currentPage = page;
    displayDebts();
}

function showLoading() {
    // No-op to avoid showing a loading spinner
}

function hideLoading() {
    // Le loading sera masqué par displayDebts()
}
