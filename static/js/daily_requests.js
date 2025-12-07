// Gestion des demandes quotidiennes des clients
class DailyRequestsManager {
    constructor() {
        this.currentPage = 1;
        this.pageSize = 20;
        this.currentFilters = {};
        this.editingRequestId = null;
        this.deleteRequestId = null;
        
        this.init();
    }

    init() {
        this.setupEventListeners();
        this.setDefaultDate();
        this.loadRequests();
        this.loadStats();
    }

    setupEventListeners() {
        // Filtres
        document.getElementById('applyFilters').addEventListener('click', () => this.applyFilters());
        document.getElementById('clearFilters').addEventListener('click', () => this.clearFilters());
        
        // Modal d'ajout/modification
        document.getElementById('saveRequestBtn').addEventListener('click', () => this.saveRequest());
        
        // Recherche de clients
        document.getElementById('searchClientBtn').addEventListener('click', () => this.searchClients());
        document.getElementById('clientSearch').addEventListener('input', (e) => {
            if (e.target.value.length >= 2) {
                this.searchClients();
            }
        });
        
        // Sélection de client
        document.getElementById('clientDropdown').addEventListener('click', (e) => {
            const clientItem = e.target.closest('.client-item');
            if (clientItem) {
                const clientId = clientItem.dataset.clientId;
                const clientName = clientItem.dataset.clientName;
                const clientPhone = clientItem.dataset.clientPhone;
                
                document.getElementById('clientName').value = clientName;
                document.getElementById('clientPhone').value = clientPhone || '';
                document.querySelector('input[name="clientId"]')?.remove();
                
                const hiddenInput = document.createElement('input');
                hiddenInput.type = 'hidden';
                hiddenInput.name = 'clientId';
                hiddenInput.value = clientId;
                document.getElementById('requestForm').appendChild(hiddenInput);
                
                document.getElementById('clientDropdown').style.display = 'none';
                document.getElementById('clientSearch').value = '';
            }
        });
        
        // Modal de suppression
        document.getElementById('confirmDeleteBtn').addEventListener('click', () => this.confirmDelete());
        
        // Fermeture des dropdowns
        document.addEventListener('click', (e) => {
            if (!e.target.closest('.input-group')) {
                document.getElementById('clientDropdown').style.display = 'none';
            }
        });
    }

    setDefaultDate() {
        const today = new Date().toISOString().split('T')[0];
        document.getElementById('requestDate').value = today;
    }

    async loadRequests() {
        try {
            const params = new URLSearchParams({
                skip: (this.currentPage - 1) * this.pageSize,
                limit: this.pageSize,
                ...this.currentFilters
            });

            const response = await axios.get(`/api/daily-requests/?${params}`);
            this.displayRequests(response.data);
        } catch (error) {
            this.showAlert('Erreur lors du chargement des demandes', 'danger');
            console.error('Error loading requests:', error);
        }
    }

    displayRequests(requests) {
        const tbody = document.getElementById('requestsTableBody');
        tbody.innerHTML = '';

        if (requests.length === 0) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="7" class="text-center text-muted py-4">
                        <i class="bi bi-inbox me-2"></i>Aucune demande trouvée
                    </td>
                </tr>
            `;
            return;
        }

        requests.forEach(request => {
            const row = document.createElement('tr');
            row.innerHTML = `
                <td>${this.formatDate(request.request_date)}</td>
                <td>${this.escapeHtml(request.client_name)}</td>
                <td>${this.escapeHtml(request.client_phone || '')}</td>
                <td>${this.escapeHtml(request.product_description)}</td>
                <td>${this.getStatusBadge(request.status)}</td>
                <td>${this.escapeHtml(request.notes || '')}</td>
                <td>
                    <div class="btn-group btn-group-sm">
                        <button class="btn btn-outline-primary" onclick="dailyRequestsManager.editRequest(${request.request_id})" title="Modifier">
                            <i class="bi bi-pencil"></i>
                        </button>
                        ${request.status === 'pending' ? `
                            <button class="btn btn-outline-success" onclick="dailyRequestsManager.fulfillRequest(${request.request_id})" title="Marquer comme satisfaite">
                                <i class="bi bi-check-circle"></i>
                            </button>
                        ` : ''}
                        <button class="btn btn-outline-danger" onclick="dailyRequestsManager.deleteRequest(${request.request_id})" title="Supprimer">
                            <i class="bi bi-trash"></i>
                        </button>
                    </div>
                </td>
            `;
            tbody.appendChild(row);
        });
    }

    getStatusBadge(status) {
        const badges = {
            'pending': '<span class="badge bg-warning">En attente</span>',
            'fulfilled': '<span class="badge bg-success">Satisfaite</span>',
            'cancelled': '<span class="badge bg-danger">Annulée</span>'
        };
        return badges[status] || status;
    }

    async loadStats() {
        try {
            const params = new URLSearchParams(this.currentFilters);
            const response = await axios.get(`/api/daily-requests/stats/summary?${params}`);
            const stats = response.data;
            
            document.getElementById('totalRequests').textContent = stats.total_requests;
            document.getElementById('pendingRequests').textContent = stats.pending_requests;
            document.getElementById('fulfilledRequests').textContent = stats.fulfilled_requests;
            document.getElementById('fulfillmentRate').textContent = `${stats.fulfillment_rate}%`;
        } catch (error) {
            console.error('Error loading stats:', error);
        }
    }

    applyFilters() {
        this.currentFilters = {
            search: document.getElementById('searchInput').value || undefined,
            status: document.getElementById('statusFilter').value || undefined,
            start_date: document.getElementById('startDate').value || undefined,
            end_date: document.getElementById('endDate').value || undefined
        };
        
        // Supprimer les valeurs undefined
        Object.keys(this.currentFilters).forEach(key => {
            if (this.currentFilters[key] === undefined) {
                delete this.currentFilters[key];
            }
        });
        
        this.currentPage = 1;
        this.loadRequests();
        this.loadStats();
    }

    clearFilters() {
        document.getElementById('searchInput').value = '';
        document.getElementById('statusFilter').value = '';
        document.getElementById('startDate').value = '';
        document.getElementById('endDate').value = '';
        
        this.currentFilters = {};
        this.currentPage = 1;
        this.loadRequests();
        this.loadStats();
    }

    async searchClients() {
        const query = document.getElementById('clientSearch').value;
        if (query.length < 2) return;

        try {
            const response = await axios.get(`/api/clients/?search=${encodeURIComponent(query)}&limit=10`);
            const clients = response.data.items || response.data || [];
            
            const dropdown = document.getElementById('clientDropdown');
            dropdown.innerHTML = '';
            
            if (clients.length === 0) {
                dropdown.innerHTML = '<div class="dropdown-item text-muted">Aucun client trouvé</div>';
            } else {
                clients.forEach(client => {
                    const item = document.createElement('div');
                    item.className = 'dropdown-item client-item';
                    item.dataset.clientId = client.client_id;
                    item.dataset.clientName = client.name;
                    item.dataset.clientPhone = client.phone || '';
                    item.innerHTML = `
                        <div class="fw-semibold">${this.escapeHtml(client.name)}</div>
                        <small class="text-muted">${this.escapeHtml(client.phone || '')} ${client.email ? '• ' + this.escapeHtml(client.email) : ''}</small>
                    `;
                    dropdown.appendChild(item);
                });
            }
            
            dropdown.style.display = 'block';
        } catch (error) {
            console.error('Error searching clients:', error);
        }
    }

    newRequest() {
        this.editingRequestId = null;
        document.getElementById('modalTitle').textContent = 'Nouvelle Demande';
        document.getElementById('requestForm').reset();
        this.setDefaultDate();
        document.getElementById('status').value = 'pending';
    }

    async editRequest(requestId) {
        try {
            const response = await axios.get(`/api/daily-requests/${requestId}`);
            const request = response.data;
            
            this.editingRequestId = requestId;
            document.getElementById('modalTitle').textContent = 'Modifier la Demande';
            
            document.getElementById('requestId').value = request.request_id;
            document.getElementById('clientName').value = request.client_name;
            document.getElementById('clientPhone').value = request.client_phone || '';
            document.getElementById('productDescription').value = request.product_description;
            document.getElementById('requestDate').value = request.request_date;
            document.getElementById('status').value = request.status;
            document.getElementById('notes').value = request.notes || '';
            
            // Ajouter l'ID du client si disponible
            if (request.client_id) {
                document.querySelector('input[name="clientId"]')?.remove();
                const hiddenInput = document.createElement('input');
                hiddenInput.type = 'hidden';
                hiddenInput.name = 'clientId';
                hiddenInput.value = request.client_id;
                document.getElementById('requestForm').appendChild(hiddenInput);
            }
            
            new bootstrap.Modal(document.getElementById('addRequestModal')).show();
        } catch (error) {
            this.showAlert('Erreur lors du chargement de la demande', 'danger');
            console.error('Error loading request:', error);
        }
    }

    async saveRequest() {
        try {
            const formData = {
                client_id: document.querySelector('input[name="clientId"]')?.value || null,
                client_name: document.getElementById('clientName').value,
                client_phone: document.getElementById('clientPhone').value,
                product_description: document.getElementById('productDescription').value,
                request_date: document.getElementById('requestDate').value,
                status: document.getElementById('status').value,
                notes: document.getElementById('notes').value
            };

            if (this.editingRequestId) {
                await axios.put(`/api/daily-requests/${this.editingRequestId}`, formData);
                this.showAlert('Demande modifiée avec succès', 'success');
            } else {
                await axios.post('/api/daily-requests/', formData);
                this.showAlert('Demande créée avec succès', 'success');
            }

            bootstrap.Modal.getInstance(document.getElementById('addRequestModal')).hide();
            this.loadRequests();
            this.loadStats();
        } catch (error) {
            this.showAlert('Erreur lors de la sauvegarde', 'danger');
            console.error('Error saving request:', error);
        }
    }

    async fulfillRequest(requestId) {
        try {
            await axios.post(`/api/daily-requests/${requestId}/fulfill`);
            this.showAlert('Demande marquée comme satisfaite', 'success');
            this.loadRequests();
            this.loadStats();
        } catch (error) {
            this.showAlert('Erreur lors de la mise à jour', 'danger');
            console.error('Error fulfilling request:', error);
        }
    }

    deleteRequest(requestId) {
        this.deleteRequestId = requestId;
        new bootstrap.Modal(document.getElementById('deleteModal')).show();
    }

    async confirmDelete() {
        try {
            await axios.delete(`/api/daily-requests/${this.deleteRequestId}`);
            this.showAlert('Demande supprimée avec succès', 'success');
            bootstrap.Modal.getInstance(document.getElementById('deleteModal')).hide();
            this.loadRequests();
            this.loadStats();
        } catch (error) {
            this.showAlert('Erreur lors de la suppression', 'danger');
            console.error('Error deleting request:', error);
        }
    }

    showAlert(message, type) {
        const alertContainer = document.getElementById('alerts-container');
        const alertId = 'alert-' + Date.now();
        
        const alert = document.createElement('div');
        alert.id = alertId;
        alert.className = `alert alert-${type} alert-dismissible fade show`;
        alert.innerHTML = `
            ${message}
            <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
        `;
        
        alertContainer.appendChild(alert);
        
        // Auto-remove after 5 seconds
        setTimeout(() => {
            const alertElement = document.getElementById(alertId);
            if (alertElement) {
                alertElement.remove();
            }
        }, 5000);
    }

    formatDate(dateString) {
        return new Date(dateString).toLocaleDateString('fr-FR');
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
}

// Initialiser le gestionnaire
let dailyRequestsManager;

document.addEventListener('DOMContentLoaded', function() {
    dailyRequestsManager = new DailyRequestsManager();
    
    // Gérer l'ouverture du modal pour nouvelle demande
    document.getElementById('addRequestModal').addEventListener('show.bs.modal', function() {
        dailyRequestsManager.newRequest();
    });
});
