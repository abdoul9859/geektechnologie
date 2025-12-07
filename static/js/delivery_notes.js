// Gestion des bons de livraison
let deliveryNotes = [];
let clients = [];
let products = [];
let currentDeliveryNoteId = null;
let deliveryItems = [];
let currentPage = 1;
const itemsPerPage = 10;

// Initialisation
document.addEventListener('DOMContentLoaded', function() {
    // Éviter les appels non authentifiés
    const isAuthenticated = !!(window.authManager && window.authManager.token);
    if (!isAuthenticated) {
        return;
    }
    loadDeliveryNotes();
    loadClients();
    loadProducts();
    setupEventListeners();
});

// Configuration des écouteurs d'événements
function setupEventListeners() {
    // Filtres
    document.getElementById('statusFilter').addEventListener('change', filterDeliveryNotes);
    document.getElementById('clientFilter').addEventListener('change', filterDeliveryNotes);
    document.getElementById('dateFromFilter').addEventListener('change', filterDeliveryNotes);
    document.getElementById('dateToFilter').addEventListener('change', filterDeliveryNotes);

    // Modal de suppression
    const confirmDeleteBtn = document.getElementById('confirmDeleteBtn');
    if (confirmDeleteBtn) {
        confirmDeleteBtn.addEventListener('click', confirmDelete);
    }

    // Formulaire de bon de livraison
    const form = document.getElementById('deliveryNoteForm');
    if (form) {
        form.addEventListener('submit', handleDeliveryNoteFormSubmit);
    }
}

// Charger les bons de livraison
async function loadDeliveryNotes() {
    try {
        showLoading();
        const { data } = await axios.get('/api/delivery-notes');
        deliveryNotes = Array.isArray(data) ? data : (data.delivery_notes || []);
        displayDeliveryNotes();
        updateStatistics();
        hideLoading();
    } catch (error) {
        console.error('Erreur:', error);
        showError(error.response?.data?.detail || 'Erreur lors du chargement des bons de livraison');
        hideLoading();
    }
}

// Charger les clients
async function loadClients() {
    try {
        const { data } = await axios.get('/api/clients');
        clients = Array.isArray(data) ? data : (data.items || data.clients || []);
        populateClientSelects();
    } catch (error) {
        console.error('Erreur lors du chargement des clients:', error);
    }
}

// Charger les produits
async function loadProducts() {
    try {
        const { data } = await axios.get('/api/products');
        products = Array.isArray(data) ? data : (data.items || data.products || []);
    } catch (error) {
        console.error('Erreur lors du chargement des produits:', error);
    }
}

// Remplir les sélecteurs de clients
function populateClientSelects() {
    const clientFilter = document.getElementById('clientFilter');
    const clientSelect = document.getElementById('clientSelect');

    // Vider les options existantes
    clientFilter.innerHTML = '<option value="">Tous les clients</option>';
    clientSelect.innerHTML = '<option value="">Sélectionner un client</option>';

    clients.forEach(client => {
        const option1 = new Option(client.name, client.id);
        const option2 = new Option(client.name, client.id);
        clientFilter.appendChild(option1);
        clientSelect.appendChild(option2);
    });
}

// Afficher les bons de livraison
function displayDeliveryNotes() {
    const filteredNotes = getFilteredDeliveryNotes();
    const startIndex = (currentPage - 1) * itemsPerPage;
    const endIndex = startIndex + itemsPerPage;
    const paginatedNotes = filteredNotes.slice(startIndex, endIndex);

    const tbody = document.getElementById('deliveryNotesTableBody');
    tbody.innerHTML = '';

    if (paginatedNotes.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="8" class="text-center py-4">
                    <i class="bi bi-truck display-4 text-muted"></i>
                    <p class="text-muted mt-2 mb-0">Aucun bon de livraison trouvé</p>
                </td>
            </tr>
        `;
    } else {
        paginatedNotes.forEach(note => {
            const row = createDeliveryNoteRow(note);
            tbody.appendChild(row);
        });
    }

    updateResultsCount(filteredNotes.length);
    updatePagination(filteredNotes.length);
}

// Créer une ligne de bon de livraison
function createDeliveryNoteRow(note) {
    const row = document.createElement('tr');
    const client = clients.find(c => c.id === note.client_id);
    const clientName = client ? client.name : 'Client inconnu';

    row.innerHTML = `
        <td>
            <strong>BL-${note.number || note.id}</strong>
        </td>
        <td>${escapeHtml(clientName)}</td>
        <td>${formatDate(note.date)}</td>
        <td>
            <span class="badge status-badge ${getStatusBadgeClass(note.status)}">
                ${getStatusLabel(note.status)}
            </span>
        </td>
        <td>
            <span class="badge bg-light text-dark">${note.items_count || 0} article(s)</span>
        </td>
        <td>
            <strong>${formatCurrency(note.total_amount || 0)}</strong>
        </td>
        <td>
            ${note.invoice_id ? 
                `<span class="badge bg-success">Facture #${note.invoice_id}</span>` : 
                '<span class="badge bg-secondary">Non facturé</span>'
            }
        </td>
        <td>
            <div class="btn-group btn-group-sm">
                <button class="btn btn-outline-primary" onclick="viewDeliveryNote(${note.id})" title="Voir">
                    <i class="bi bi-eye"></i>
                </button>
                <button class="btn btn-outline-secondary" onclick="editDeliveryNote(${note.id})" title="Modifier">
                    <i class="bi bi-pencil"></i>
                </button>
                <button class="btn btn-outline-info" onclick="printDeliveryNote(${note.id})" title="Imprimer">
                    <i class="bi bi-printer"></i>
                </button>
                <button class="btn btn-outline-danger" onclick="deleteDeliveryNote(${note.id})" title="Supprimer">
                    <i class="bi bi-trash"></i>
                </button>
            </div>
        </td>
    `;

    return row;
}

// Obtenir les bons de livraison filtrés
function getFilteredDeliveryNotes() {
    let filtered = [...deliveryNotes];

    const statusFilter = document.getElementById('statusFilter').value;
    const clientFilter = document.getElementById('clientFilter').value;
    const dateFromFilter = document.getElementById('dateFromFilter').value;
    const dateToFilter = document.getElementById('dateToFilter').value;

    if (statusFilter) {
        filtered = filtered.filter(note => note.status === statusFilter);
    }

    if (clientFilter) {
        filtered = filtered.filter(note => note.client_id === parseInt(clientFilter));
    }

    if (dateFromFilter) {
        filtered = filtered.filter(note => note.date >= dateFromFilter);
    }

    if (dateToFilter) {
        filtered = filtered.filter(note => note.date <= dateToFilter);
    }

    // Trier par date de création (plus récent en premier)
    filtered.sort((a, b) => new Date(b.created_at || b.date) - new Date(a.created_at || a.date));

    return filtered;
}

// Filtrer les bons de livraison
function filterDeliveryNotes() {
    currentPage = 1;
    displayDeliveryNotes();
}

// Effacer les filtres
function clearFilters() {
    document.getElementById('statusFilter').value = '';
    document.getElementById('clientFilter').value = '';
    document.getElementById('dateFromFilter').value = '';
    document.getElementById('dateToFilter').value = '';
    filterDeliveryNotes();
}

// Mettre à jour les statistiques
function updateStatistics() {
    const total = deliveryNotes.length;
    const preparation = deliveryNotes.filter(n => n.status === 'en_preparation').length;
    const inProgress = deliveryNotes.filter(n => n.status === 'en_cours').length;
    const delivered = deliveryNotes.filter(n => n.status === 'livre').length;

    document.getElementById('totalDeliveryNotes').textContent = total;
    document.getElementById('preparationDeliveryNotes').textContent = preparation;
    document.getElementById('inProgressDeliveryNotes').textContent = inProgress;
    document.getElementById('deliveredDeliveryNotes').textContent = delivered;
}

// Mettre à jour le compteur de résultats
function updateResultsCount(count) {
    const resultsCount = document.getElementById('resultsCount');
    if (resultsCount) {
        resultsCount.textContent = `${count} bon${count !== 1 ? 's' : ''}`;
    }
}

// Créer un nouveau bon de livraison
function createDeliveryNote() {
    resetDeliveryNoteForm();
    const modal = new bootstrap.Modal(document.getElementById('deliveryNoteModal'));
    modal.show();
}

// Réinitialiser le formulaire
function resetDeliveryNoteForm() {
    const form = document.getElementById('deliveryNoteForm');
    form.reset();
    
    document.getElementById('deliveryNoteId').value = '';
    document.getElementById('deliveryDate').value = new Date().toISOString().split('T')[0];
    
    const modalTitle = document.getElementById('deliveryNoteModalTitle');
    modalTitle.innerHTML = '<i class="bi bi-plus-circle me-2"></i>Nouveau Bon de Livraison';
    
    deliveryItems = [];
    updateDeliveryItemsTable();
    updateTotals();
    
    currentDeliveryNoteId = null;
}

// Ajouter un article de livraison
function addDeliveryItem() {
    const item = {
        id: Date.now(),
        product_id: '',
        product_name: '',
        quantity: 1,
        unit_price: 0,
        total: 0
    };
    
    deliveryItems.push(item);
    updateDeliveryItemsTable();
}

// Mettre à jour le tableau des articles
function updateDeliveryItemsTable() {
    const tbody = document.getElementById('deliveryItemsTable');
    
    if (deliveryItems.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="5" class="text-center text-muted py-3">
                    Aucun article ajouté
                </td>
            </tr>
        `;
        return;
    }
    
    tbody.innerHTML = '';
    
    deliveryItems.forEach((item, index) => {
        const row = document.createElement('tr');
        row.innerHTML = `
            <td>
                <select class="form-select" onchange="updateItemProduct(${index}, this.value)">
                    <option value="">Sélectionner un produit</option>
                    ${products.map(p => `
                        <option value="${p.id}" ${item.product_id == p.id ? 'selected' : ''}>
                            ${escapeHtml(p.name)}
                        </option>
                    `).join('')}
                </select>
            </td>
            <td>
                <input type="number" class="form-control" value="${item.quantity}" 
                       min="1" onchange="updateItemQuantity(${index}, this.value)">
            </td>
            <td>
                <input type="number" class="form-control" value="${item.unit_price}" 
                       min="0" step="1" inputmode="numeric" pattern="[0-9]*" onchange="updateItemPrice(${index}, this.value)">
            </td>
            <td>
                <strong>${formatCurrency(item.total)}</strong>
            </td>
            <td>
                <button type="button" class="btn btn-outline-danger btn-sm" 
                        onclick="removeDeliveryItem(${index})">
                    <i class="bi bi-trash"></i>
                </button>
            </td>
        `;
        tbody.appendChild(row);
    });
}

// Mettre à jour le produit d'un article
function updateItemProduct(index, productId) {
    const product = products.find(p => p.id == productId);
    if (product) {
        deliveryItems[index].product_id = productId;
        deliveryItems[index].product_name = product.name;
        deliveryItems[index].unit_price = product.price || 0;
        deliveryItems[index].total = deliveryItems[index].quantity * deliveryItems[index].unit_price;
    }
    updateDeliveryItemsTable();
    updateTotals();
}

// Mettre à jour la quantité d'un article
function updateItemQuantity(index, quantity) {
    deliveryItems[index].quantity = parseInt(quantity) || 1;
    deliveryItems[index].total = deliveryItems[index].quantity * deliveryItems[index].unit_price;
    updateDeliveryItemsTable();
    updateTotals();
}

// Mettre à jour le prix d'un article
function updateItemPrice(index, price) {
    deliveryItems[index].unit_price = Math.round(parseInt(price, 10) || 0);
    deliveryItems[index].total = deliveryItems[index].quantity * deliveryItems[index].unit_price;
    updateDeliveryItemsTable();
    updateTotals();
}

// Supprimer un article de livraison
function removeDeliveryItem(index) {
    deliveryItems.splice(index, 1);
    updateDeliveryItemsTable();
    updateTotals();
}

// Mettre à jour les totaux
function updateTotals() {
    const totalHT = deliveryItems.reduce((sum, item) => sum + item.total, 0);
    const totalTVA = totalHT * 0.18;
    const totalTTC = totalHT + totalTVA;
    
    document.getElementById('totalHT').textContent = formatCurrency(totalHT);
    document.getElementById('totalTVA').textContent = formatCurrency(totalTVA);
    document.getElementById('totalTTC').textContent = formatCurrency(totalTTC);
}

// Gérer la soumission du formulaire
function handleDeliveryNoteFormSubmit(event) {
    event.preventDefault();
    saveDeliveryNote();
}

// Enregistrer le bon de livraison
async function saveDeliveryNote() {
    const formData = new FormData(document.getElementById('deliveryNoteForm'));
    
    const deliveryNoteData = {
        client_id: parseInt(formData.get('clientSelect')) || formData.get('client_id'),
        date: document.getElementById('deliveryDate').value,
        status: document.getElementById('statusSelect').value,
        notes: document.getElementById('notes').value,
        items: deliveryItems.filter(item => item.product_id && item.quantity > 0)
    };

    // Validation
    if (!deliveryNoteData.client_id) {
        showError('Veuillez sélectionner un client');
        return;
    }

    if (deliveryNoteData.items.length === 0) {
        showError('Veuillez ajouter au moins un article');
        return;
    }

    try {
        let response;
        if (currentDeliveryNoteId) {
            response = await fetch(`/api/delivery-notes/${currentDeliveryNoteId}`, {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json'
                },
                credentials: 'include',
                body: JSON.stringify(deliveryNoteData)
            });
        } else {
            response = await fetch('/api/delivery-notes', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                credentials: 'include',
                body: JSON.stringify(deliveryNoteData)
            });
        }

        if (!response.ok) {
            throw new Error('Erreur lors de l\'enregistrement du bon de livraison');
        }

        showSuccess(currentDeliveryNoteId ? 'Bon de livraison modifié avec succès' : 'Bon de livraison créé avec succès');
        
        // Fermer la modal
        const modal = bootstrap.Modal.getInstance(document.getElementById('deliveryNoteModal'));
        modal.hide();
        
        // Recharger les données
        loadDeliveryNotes();
    } catch (error) {
        console.error('Erreur:', error);
        showError('Erreur lors de l\'enregistrement du bon de livraison');
    }
}

// Voir un bon de livraison
function viewDeliveryNote(id) {
    // Rediriger vers la page de détail
    window.location.href = `/delivery-notes/${id}`;
}

// Modifier un bon de livraison
function editDeliveryNote(id) {
    const note = deliveryNotes.find(n => n.id === id);
    if (!note) return;

    // Remplir le formulaire
    document.getElementById('deliveryNoteId').value = note.id;
    document.getElementById('clientSelect').value = note.client_id;
    document.getElementById('deliveryDate').value = note.date;
    document.getElementById('statusSelect').value = note.status;
    document.getElementById('notes').value = note.notes || '';

    // Charger les articles
    deliveryItems = note.items || [];
    updateDeliveryItemsTable();
    updateTotals();

    // Mettre à jour l'interface
    const modalTitle = document.getElementById('deliveryNoteModalTitle');
    modalTitle.innerHTML = '<i class="bi bi-pencil me-2"></i>Modifier le Bon de Livraison';
    
    currentDeliveryNoteId = id;

    // Afficher la modal
    const modal = new bootstrap.Modal(document.getElementById('deliveryNoteModal'));
    modal.show();
}

// Imprimer un bon de livraison
function printDeliveryNote(id) {
    window.open(`/delivery-notes/print/${id}`, '_blank');
}

// Supprimer un bon de livraison
function deleteDeliveryNote(id) {
    currentDeliveryNoteId = id;
    const modal = new bootstrap.Modal(document.getElementById('deleteModal'));
    modal.show();
}

// Confirmer la suppression
async function confirmDelete() {
    if (!currentDeliveryNoteId) return;

    try {
        await axios.delete(`/api/delivery-notes/${currentDeliveryNoteId}`);

        showSuccess('Bon de livraison supprimé avec succès');
        
        // Fermer la modal
        const modal = bootstrap.Modal.getInstance(document.getElementById('deleteModal'));
        modal.hide();
        
        // Recharger les données
        loadDeliveryNotes();
        currentDeliveryNoteId = null;
    } catch (error) {
        console.error('Erreur:', error);
        showError(error.response?.data?.detail || 'Erreur lors de la suppression du bon de livraison');
    }
}

// Utilitaires
function getStatusBadgeClass(status) {
    switch (status) {
        case 'livre':
            return 'bg-success';
        case 'en_cours':
            return 'bg-info';
        case 'en_preparation':
            return 'bg-warning text-dark';
        case 'annule':
            return 'bg-danger';
        default:
            return 'bg-secondary';
    }
}

function getStatusLabel(status) {
    switch (status) {
        case 'livre':
            return 'Livré';
        case 'en_cours':
            return 'En cours';
        case 'en_preparation':
            return 'En préparation';
        case 'annule':
            return 'Annulé';
        default:
            return status;
    }
}

function formatDate(dateString) {
    const options = { year: 'numeric', month: 'short', day: 'numeric' };
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
    const totalItems = getFilteredDeliveryNotes().length;
    const totalPages = Math.ceil(totalItems / itemsPerPage);
    
    if (page < 1 || page > totalPages) return;
    
    currentPage = page;
    displayDeliveryNotes();
}

function showLoading() {
    // No-op to avoid showing a loading spinner
}

function hideLoading() {
    // Le loading sera masqué par displayDeliveryNotes()
}
