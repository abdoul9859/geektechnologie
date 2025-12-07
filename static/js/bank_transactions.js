// Gestion des transactions bancaires
let transactions = [];
let currentTransactionId = null;
let currentPage = 1;
const itemsPerPage = 15;

// Initialisation
document.addEventListener('DOMContentLoaded', function() {
    // Toujours initialiser l'UI et tenter un chargement.
    // En cas de 401, le catch de loadTransactions() affichera un message et retirera le spinner.
    setupEventListeners();
    setDefaultDate();
    loadTransactions();
});

// Utilitaire sûr pour lire une valeur d'input
function getInputValue(id) {
    const el = document.getElementById(id);
    return el ? el.value : '';
}

// Utilitaire: définir une valeur si l'élément existe
function setInputValue(id, value) {
    const el = document.getElementById(id);
    if (el) el.value = value;
}

// Configuration des écouteurs d'événements
function setupEventListeners() {
    // Filtres
    document.getElementById('typeFilter').addEventListener('change', filterTransactions);
    document.getElementById('methodFilter').addEventListener('change', filterTransactions);
    document.getElementById('dateFromFilter').addEventListener('change', filterTransactions);
    document.getElementById('dateToFilter').addEventListener('change', filterTransactions);

    // Modal de suppression
    const confirmDeleteBtn = document.getElementById('confirmDeleteBtn');
    if (confirmDeleteBtn) {
        confirmDeleteBtn.addEventListener('click', confirmDelete);
    }

    // Formulaire de transaction
    const form = document.getElementById('transactionForm');
    if (form) {
        form.addEventListener('submit', handleFormSubmit);
    }
}

// Définir la date par défaut
function setDefaultDate() {
    const today = new Date().toISOString().split('T')[0];
    setInputValue('transactionDate', today);
}

// Charger les transactions
async function loadTransactions() {
    try {
        showLoading();
        const { data } = await axios.get('/api/bank-transactions/');

        // Vérifier si la réponse est un tableau ou contient un tableau
        if (Array.isArray(data)) {
            transactions = data;
        } else if (data && Array.isArray(data.transactions)) {
            transactions = data.transactions;
        } else if (data && Array.isArray(data.data)) {
            transactions = data.data;
        } else {
            transactions = [];
        }

        // Normaliser les montants en nombres pour éviter NaN / concaténations
        transactions = transactions.map(t => ({
            ...t,
            amount: isFinite(Number(t.amount)) ? Number(t.amount) : 0
        }));

        displayTransactions();
        updateStatistics();
        hideLoading();
    } catch (error) {
        console.error('Erreur:', error);
        const msg = error.response?.data?.detail || ('Erreur lors du chargement des transactions: ' + (error.message || ''));
        showError(msg);
        hideLoading(msg);
    }
}

// Afficher les transactions
function displayTransactions() {
    const filteredTransactions = getFilteredTransactions();
    const startIndex = (currentPage - 1) * itemsPerPage;
    const endIndex = startIndex + itemsPerPage;
    const paginatedTransactions = filteredTransactions.slice(startIndex, endIndex);

    const tbody = document.getElementById('transactionsTableBody');
    tbody.innerHTML = '';

    if (paginatedTransactions.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="7" class="text-center py-4">
                    <i class="bi bi-bank display-4 text-muted"></i>
                    <p class="text-muted mt-2 mb-0">Aucune transaction trouvée</p>
                </td>
            </tr>
        `;
    } else {
        paginatedTransactions.forEach(transaction => {
            const row = createTransactionRow(transaction);
            tbody.appendChild(row);
        });
    }

    updateResultsCount(filteredTransactions.length);
    updatePagination(filteredTransactions.length);
}

// Créer une ligne de transaction
function createTransactionRow(transaction) {
    const row = document.createElement('tr');
    
    row.innerHTML = `
        <td>${formatDate(transaction.date)}</td>
        <td>
            <span class="badge ${getTypeBadgeClass(transaction.type)}">
                <i class="bi ${getTypeIcon(transaction.type)} me-1"></i>
                ${getTypeLabel(transaction.type)}
            </span>
        </td>
        <td>
            <div class="fw-bold">${escapeHtml(transaction.description)}</div>
            ${transaction.notes ? `<small class="text-muted">${escapeHtml(transaction.notes)}</small>` : ''}
        </td>
        <td>
            <span class="badge bg-secondary">${getMethodLabel(transaction.method)}</span>
        </td>
        <td>
            <span class="fw-bold ${transaction.type === 'entry' ? 'text-success' : 'text-danger'}">
                ${transaction.type === 'entry' ? '+' : '-'}${formatCurrency(transaction.amount)}
            </span>
        </td>
        <td>
            ${transaction.reference ? `<code>${escapeHtml(transaction.reference)}</code>` : '-'}
        </td>
        <td>
            <div class="btn-group btn-group-sm">
                <button class="btn btn-outline-secondary" onclick="editTransaction(${transaction.id})" title="Modifier">
                    <i class="bi bi-pencil"></i>
                </button>
                <button class="btn btn-outline-danger" onclick="deleteTransaction(${transaction.id})" title="Supprimer">
                    <i class="bi bi-trash"></i>
                </button>
            </div>
        </td>
    `;

    return row;
}

// Obtenir les transactions filtrées
function getFilteredTransactions() {
    // Vérifier que transactions est un tableau
    if (!Array.isArray(transactions)) {
        console.error('transactions is not an array:', transactions);
        return [];
    }
    
    let filtered = [...transactions];

    const typeFilter = document.getElementById('typeFilter').value;
    const methodFilter = document.getElementById('methodFilter').value;
    const dateFromFilter = document.getElementById('dateFromFilter').value;
    const dateToFilter = document.getElementById('dateToFilter').value;

    if (typeFilter) {
        filtered = filtered.filter(t => t.type === typeFilter);
    }

    if (methodFilter) {
        filtered = filtered.filter(t => t.method === methodFilter);
    }

    if (dateFromFilter) {
        filtered = filtered.filter(t => t.date >= dateFromFilter);
    }

    if (dateToFilter) {
        filtered = filtered.filter(t => t.date <= dateToFilter);
    }

    // Trier par date (plus récent en premier)
    filtered.sort((a, b) => new Date(b.date) - new Date(a.date));

    return filtered;
}

// Filtrer les transactions
function filterTransactions() {
    currentPage = 1;
    displayTransactions();
}

// Effacer les filtres
function clearFilters() {
    document.getElementById('typeFilter').value = '';
    document.getElementById('methodFilter').value = '';
    document.getElementById('dateFromFilter').value = '';
    document.getElementById('dateToFilter').value = '';
    filterTransactions();
}

// Mettre à jour les statistiques
function updateStatistics() {
    const totalIncome = transactions
        .filter(t => t.type === 'entry')
        .reduce((sum, t) => sum + (isFinite(Number(t.amount)) ? Number(t.amount) : 0), 0);
    
    const totalExpense = transactions
        .filter(t => t.type === 'exit')
        .reduce((sum, t) => sum + (isFinite(Number(t.amount)) ? Number(t.amount) : 0), 0);
    
    const currentBalance = totalIncome - totalExpense;

    document.getElementById('currentBalance').textContent = formatCurrency(currentBalance);
    document.getElementById('totalIncome').textContent = formatCurrency(totalIncome);
    document.getElementById('totalExpense').textContent = formatCurrency(totalExpense);
}

// Mettre à jour le compteur de résultats
function updateResultsCount(count) {
    const resultsCount = document.getElementById('resultsCount');
    if (resultsCount) {
        resultsCount.textContent = `${count} transaction${count !== 1 ? 's' : ''}`;
    }
}

// Créer une nouvelle transaction
function createTransaction() {
    resetTransactionForm();
    const modal = new bootstrap.Modal(document.getElementById('transactionModal'));
    modal.show();
}

// Réinitialiser le formulaire
function resetTransactionForm() {
    const form = document.getElementById('transactionForm');
    form.reset();
    
    document.getElementById('transactionId').value = '';
    setDefaultDate();
    
    const modalTitle = document.getElementById('transactionModalTitle');
    modalTitle.innerHTML = '<i class="bi bi-plus-circle me-2"></i>Nouvelle Transaction';
    
    currentTransactionId = null;
}

// Modifier une transaction
function editTransaction(id) {
    const transaction = transactions.find(t => t.id === id);
    if (!transaction) return;

    // Remplir le formulaire
    setInputValue('transactionId', transaction.id);
    setInputValue('transactionType', transaction.type);
    setInputValue('transactionDate', transaction.date);
    setInputValue('amount', transaction.amount);
    setInputValue('paymentMethod', transaction.method);
    setInputValue('reference', transaction.reference || '');
    setInputValue('description', transaction.description);
    setInputValue('motif', transaction.motif || '');
    setInputValue('reference', transaction.reference || '');

    // Mettre à jour l'interface
    const modalTitle = document.getElementById('transactionModalTitle');
    modalTitle.innerHTML = '<i class="bi bi-pencil me-2"></i>Modifier la Transaction';
    
    currentTransactionId = id;

    // Afficher la modal
    const modal = new bootstrap.Modal(document.getElementById('transactionModal'));
    modal.show();
}

// Gérer la soumission du formulaire
async function handleFormSubmit(e) {
    e.preventDefault();
    await saveTransaction();
}

// Enregistrer la transaction
async function saveTransaction() {
    const formEl = document.getElementById('transactionForm');
    if (!formEl) {
        showError('Formulaire de transaction introuvable. Veuillez rafraîchir la page.');
        return;
    }
    const formData = new FormData(formEl);
    
    const transactionData = {
        type: getInputValue('transactionType'),
        motif: getInputValue('motif'),
        description: getInputValue('description'),
        amount: parseFloat(getInputValue('amount')),
        date: getInputValue('transactionDate'),
        method: getInputValue('paymentMethod'),
        reference: getInputValue('reference')
    };

    // Validation
    if (!transactionData.type) {
        showError('Veuillez sélectionner un type de transaction');
        return;
    }

    if (!transactionData.date) {
        showError('Veuillez saisir une date');
        return;
    }

    if (!transactionData.amount || transactionData.amount <= 0) {
        showError('Veuillez saisir un montant valide');
        return;
    }

    if (!transactionData.motif || !transactionData.motif.trim()) {
        showError('Veuillez saisir un motif');
        return;
    }

    try {
        if (currentTransactionId) {
            await axios.put(`/api/bank-transactions/${currentTransactionId}`, transactionData);
        } else {
            await axios.post('/api/bank-transactions/', transactionData);
        }

        showSuccess(currentTransactionId ? 'Transaction modifiée avec succès' : 'Transaction créée avec succès');
        
        // Fermer la modal
        const modal = bootstrap.Modal.getInstance(document.getElementById('transactionModal'));
        modal.hide();
        
        // Recharger les données
        loadTransactions();
    } catch (error) {
        console.error('Erreur:', error);
        showError(error.response?.data?.detail || 'Erreur lors de l\'enregistrement de la transaction');
    }
}

// Supprimer une transaction
function deleteTransaction(id) {
    currentTransactionId = id;
    const modal = new bootstrap.Modal(document.getElementById('deleteModal'));
    modal.show();
}

// Confirmer la suppression
async function confirmDelete() {
    if (!currentTransactionId) return;

    try {
        await axios.delete(`/api/bank-transactions/${currentTransactionId}`);

        showSuccess('Transaction supprimée avec succès');
        
        // Fermer la modal
        const modal = bootstrap.Modal.getInstance(document.getElementById('deleteModal'));
        modal.hide();
        
        // Recharger les données
        loadTransactions();
        currentTransactionId = null;
    } catch (error) {
        console.error('Erreur:', error);
        showError(error.response?.data?.detail || 'Erreur lors de la suppression de la transaction');
    }
}

// Utilitaires pour les types
function getTypeBadgeClass(type) {
    return type === 'entry' ? 'bg-success' : 'bg-danger';
}

function getTypeIcon(type) {
    return type === 'entry' ? 'bi-arrow-down-circle' : 'bi-arrow-up-circle';
}

function getTypeLabel(type) {
    return type === 'entry' ? 'Entrée' : 'Sortie';
}



function getMethodLabel(method) {
    const labels = {
        'virement': 'Virement',
        'cheque': 'Chèque'
    };
    return labels[method] || method;
}

function formatDate(dateString) {
    const options = { 
        year: 'numeric', 
        month: 'short', 
        day: 'numeric',
        weekday: 'short'
    };
    return new Date(dateString).toLocaleDateString('fr-FR', options);
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

function changePage(page) {
    const totalItems = getFilteredTransactions().length;
    const totalPages = Math.ceil(totalItems / itemsPerPage);
    
    if (page < 1 || page > totalPages) return;
    
    currentPage = page;
    displayTransactions();
}

function showLoading() {
    // No-op to avoid showing a loading spinner
}

function hideLoading(message) {
    const tbody = document.getElementById('transactionsTableBody');
    if (!tbody) return;
    const hasSpinner = tbody.querySelector('.spinner-border');
    // If spinner is present and no rows have replaced it, show an empty/error state
    if (hasSpinner) {
        const text = message || 'Aucune transaction trouvée';
        tbody.innerHTML = `
            <tr>
                <td colspan="7" class="text-center py-4">
                    <i class="bi bi-bank display-4 text-muted"></i>
                    <p class="text-muted mt-2 mb-0">${escapeHtml(text)}</p>
                </td>
            </tr>
        `;
    }
}
