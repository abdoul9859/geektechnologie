// Gestion des clients
let currentPage = 1;
const itemsPerPage = 10;
let clients = [];
let filteredClients = [];

// Initialisation
document.addEventListener('DOMContentLoaded', function() {
    // Utiliser la nouvelle logique d'authentification basée sur cookies
    const ready = () => {
        const hasAuthManager = !!window.authManager;
        const hasUser = !!(hasAuthManager && window.authManager.userData && Object.keys(window.authManager.userData).length);
        return hasAuthManager && (window.authManager.isAuthenticatedSync() || hasUser);
    };

    // Initialiser immédiatement sans délai pour un chargement instantané
    loadClients();
    setupEventListeners();
    
    // Appliquer la recherche passée via la navbar (?q=...)
    try {
        const params = new URLSearchParams(window.location.search || '');
        const q = (params.get('q') || '').trim();
        if (q) {
            const input = document.getElementById('searchInput');
            if (input) {
                input.value = q;
                // Déclencher le filtrage une fois la liste chargée
                setTimeout(() => {
                    try { filterClients(); } catch(e) { /* ignore */ }
                }, 50);
            }
        }
    } catch(e) { /* ignore */ }
});

function setupEventListeners() {
    // Recherche en temps réel
    const searchInput = document.getElementById('searchInput');
    if (searchInput) {
        searchInput.addEventListener('input', debounce(filterClients, 300));
    }
    // Filtres
    document.getElementById('cityFilter')?.addEventListener('input', debounce(filterClients, 300));
    document.getElementById('countryFilter')?.addEventListener('input', debounce(filterClients, 300));
    document.getElementById('hasEmailFilter')?.addEventListener('change', filterClients);
    document.getElementById('hasPhoneFilter')?.addEventListener('change', filterClients);
    document.getElementById('createdFrom')?.addEventListener('change', filterClients);
    document.getElementById('createdTo')?.addEventListener('change', filterClients);
}

// Utilitaire debounce
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

// Charger la liste des clients
async function loadClients() {
    try {
        showLoading();
        
        // Utiliser safeLoadData pour éviter les chargements infinis
        const response = await safeLoadData(
            () => axios.get('/api/clients/', { params: { limit: 1000 } }),
            {
                timeout: 8000,
                fallbackData: [],
                errorMessage: 'Erreur lors du chargement des clients'
            }
        );
        
        const data = response.data || [];
        clients = data.items || data || [];
        filteredClients = [...clients];
        
        displayClients();
        updatePagination();
    } catch (error) {
        console.error('Erreur lors du chargement des clients:', error);
        
        // Afficher un message d'erreur dans le tableau
        const tbody = document.getElementById('clientsTableBody');
        if (tbody) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="6" class="text-center text-danger py-4">
                        <i class="bi bi-exclamation-triangle fs-1 d-block mb-2"></i>
                        Erreur lors du chargement des clients
                    </td>
                </tr>
            `;
        }
        
        if (typeof showAlert === 'function') {
            showAlert('Erreur lors du chargement des clients', 'danger');
        }
    }
}

// Afficher les clients
function displayClients() {
    const tbody = document.getElementById('clientsTableBody');
    if (!tbody) return;

    if (filteredClients.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="6" class="text-center text-muted py-4">
                    <i class="bi bi-inbox fs-1 d-block mb-2"></i>
                    Aucun client trouvé
                </td>
            </tr>
        `;
        return;
    }

    const startIndex = (currentPage - 1) * itemsPerPage;
    const endIndex = startIndex + itemsPerPage;
    const clientsToShow = filteredClients.slice(startIndex, endIndex);

    tbody.innerHTML = clientsToShow.map(client => `
        <tr>
            <td>
                <div class="d-flex align-items-center">
                    <div class="avatar-sm bg-primary rounded-circle d-flex align-items-center justify-content-center me-3">
                        <i class="bi bi-person text-white"></i>
                    </div>
                    <div>
                        <h6 class="mb-0">${escapeHtml(client.name || '')}</h6>
                        <small class="text-muted">ID: ${client.client_id}</small>
                    </div>
                </div>
            </td>
            <td>${escapeHtml(client.contact_person || '-')}</td>
            <td>
                ${client.email ? `<a href="mailto:${client.email}" class="text-decoration-none">${escapeHtml(client.email)}</a>` : '-'}
            </td>
            <td>
                ${client.phone ? `<a href="tel:${client.phone}" class="text-decoration-none">${escapeHtml(client.phone)}</a>` : '-'}
            </td>
            <td>${escapeHtml(client.city || '-')}</td>
            <td>
                <div class="btn-group" role="group">
                    <button class="btn btn-sm btn-outline-primary" onclick="editClient(${client.client_id})" title="Modifier">
                        <i class="bi bi-pencil"></i>
                    </button>
                    <button class="btn btn-sm btn-outline-info" onclick="viewClient(${client.client_id})" title="Voir détails">
                        <i class="bi bi-eye"></i>
                    </button>
                    <a class="btn btn-sm btn-outline-warning" href="/clients/debts?client_id=${client.client_id}" title="Créances">
                        <i class="bi bi-cash-coin"></i>
                    </a>
                    <button class="btn btn-sm btn-outline-danger" onclick="deleteClient(${client.client_id})" title="Supprimer">
                        <i class="bi bi-trash"></i>
                    </button>
                </div>
            </td>
        </tr>
    `).join('');
}

// Filtrer les clients
async function filterClients() {
    const searchTerm = (document.getElementById('searchInput')?.value || '').toLowerCase().trim();
    const city = (document.getElementById('cityFilter')?.value || '').toLowerCase().trim();
    const country = (document.getElementById('countryFilter')?.value || '').toLowerCase().trim();
    const hasEmail = !!document.getElementById('hasEmailFilter')?.checked;
    const hasPhone = !!document.getElementById('hasPhoneFilter')?.checked;
    const createdFrom = document.getElementById('createdFrom')?.value || '';
    const createdTo = document.getElementById('createdTo')?.value || '';

    // Source de base: si recherche saisie, interroger le serveur pour couvrir tous les enregistrements
    let baseList = clients || [];
    if (searchTerm) {
        try {
            const { data } = await axios.get('/api/clients/', { params: { search: searchTerm, limit: 1000 } });
            baseList = data.items || data || [];
        } catch (e) {
            baseList = clients || [];
        }
    }

    filteredClients = baseList.filter(client => {
        // Recherche globale
        if (searchTerm) {
            const hay = [client.name, client.email, client.phone, client.contact_person, client.city]
                .map(v => String(v || '').toLowerCase());
            if (!hay.some(h => h.includes(searchTerm))) return false;
        }
        // Ville
        if (city && !(String(client.city || '').toLowerCase().includes(city))) return false;
        // Pays
        if (country && !(String(client.country || '').toLowerCase().includes(country))) return false;
        // A email / téléphone
        if (hasEmail && !client.email) return false;
        if (hasPhone && !client.phone) return false;
        // Date de création entre
        try {
            if (createdFrom || createdTo) {
                const raw = client.created_at || client.updated_at || null;
                if (!raw) return false; // si on filtre par date mais pas de date côté client, on exclut
                const d = new Date(raw);
                if (Number.isNaN(d.getTime())) return false;
                const ymd = d.toISOString().split('T')[0];
                if (createdFrom && ymd < createdFrom) return false;
                if (createdTo && ymd > createdTo) return false;
            }
        } catch (e) {}
        return true;
    });

    currentPage = 1;
    displayClients();
    updatePagination();
}

// Réinitialiser la recherche
function resetSearch() {
    document.getElementById('searchInput').value = '';
    const ids = ['cityFilter','countryFilter','hasEmailFilter','hasPhoneFilter','createdFrom','createdTo'];
    ids.forEach(id => {
        const el = document.getElementById(id);
        if (!el) return;
        if (el.type === 'checkbox') el.checked = false; else el.value = '';
    });
    filteredClients = [...clients];
    currentPage = 1;
    displayClients();
    updatePagination();
}

// Pagination
function updatePagination() {
    const totalPages = Math.ceil(filteredClients.length / itemsPerPage);
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
    const totalPages = Math.ceil(filteredClients.length / itemsPerPage);
    if (page >= 1 && page <= totalPages) {
        currentPage = page;
        displayClients();
        updatePagination();
    }
}

// Ouvrir le modal pour nouveau client
function openClientModal() {
    document.getElementById('clientModalTitle').innerHTML = '<i class="bi bi-person-plus me-2"></i>Nouveau Client';
    document.getElementById('clientForm').reset();
    document.getElementById('clientId').value = '';
    document.getElementById('clientCountry').value = 'Sénégal';
}

// Modifier un client
async function editClient(clientId) {
    try {
        const { data: client } = await axios.get(`/api/clients/${clientId}`);
        
        // Remplir le formulaire
        document.getElementById('clientId').value = client.client_id;
        document.getElementById('clientName').value = client.name || '';
        document.getElementById('clientContact').value = client.contact_person || '';
        document.getElementById('clientEmail').value = client.email || '';
        document.getElementById('clientPhone').value = client.phone || '';
        document.getElementById('clientAddress').value = client.address || '';
        document.getElementById('clientCity').value = client.city || '';
        document.getElementById('clientPostalCode').value = client.postal_code || '';
        document.getElementById('clientCountry').value = client.country || 'Sénégal';
        document.getElementById('clientTaxNumber').value = client.tax_number || '';
        document.getElementById('clientNotes').value = client.notes || '';
        
        document.getElementById('clientModalTitle').innerHTML = '<i class="bi bi-pencil me-2"></i>Modifier Client';
        
        // Ouvrir le modal
        const modal = new bootstrap.Modal(document.getElementById('clientModal'));
        modal.show();
        
    } catch (error) {
        console.error('Erreur lors du chargement du client:', error);
        showError(error.response?.data?.detail || 'Erreur lors du chargement du client');
    }
}

// Sauvegarder un client
async function saveClient() {
    try {
        const clientId = document.getElementById('clientId').value;
        const clientData = {
            name: document.getElementById('clientName').value.trim(),
            contact: document.getElementById('clientContact').value.trim() || null,
            email: document.getElementById('clientEmail').value.trim() || null,
            phone: document.getElementById('clientPhone').value.trim() || null,
            address: document.getElementById('clientAddress').value.trim() || null,
            city: document.getElementById('clientCity').value.trim() || null,
            postal_code: document.getElementById('clientPostalCode').value.trim() || null,
            country: document.getElementById('clientCountry').value.trim() || null,
            tax_number: document.getElementById('clientTaxNumber').value.trim() || null,
            notes: document.getElementById('clientNotes').value.trim() || null
        };

        if (!clientData.name) {
            showError('Le nom du client est obligatoire');
            return;
        }

        const url = clientId ? `/api/clients/${clientId}` : '/api/clients/';
        const method = clientId ? 'PUT' : 'POST';

        if (method === 'POST') {
            await axios.post(url, clientData);
        } else {
            await axios.put(url, clientData);
        }

        // Fermer le modal
        const modal = bootstrap.Modal.getInstance(document.getElementById('clientModal'));
        modal.hide();

        // Recharger la liste
        await loadClients();
        
        showSuccess(clientId ? 'Client modifié avec succès' : 'Client créé avec succès');
        
    } catch (error) {
        console.error('Erreur lors de la sauvegarde:', error);
        showError(error.response?.data?.detail || error.message || 'Erreur lors de la sauvegarde du client');
    }
}

// Voir les détails d'un client
async function viewClient(clientId) {
    try {
        window.location.href = `/clients/detail?id=${clientId}`;
        
    } catch (error) {
        console.error('Erreur lors du chargement du client:', error);
        showError(error.response?.data?.detail || 'Erreur lors du chargement du client');
    }
}

// Supprimer un client
async function deleteClient(clientId) {
    if (!confirm('Êtes-vous sûr de vouloir supprimer ce client ?')) {
        return;
    }

    try {
        await axios.delete(`/api/clients/${clientId}`);

        await loadClients();
        showSuccess('Client supprimé avec succès');
        
    } catch (error) {
        console.error('Erreur lors de la suppression:', error);
        showError(error.response?.data?.detail || error.message || 'Erreur lors de la suppression du client');
    }
}

// Afficher le loading
function showLoading() {
    // Ne pas afficher d'indicateur de chargement pour une expérience instantanée
}
