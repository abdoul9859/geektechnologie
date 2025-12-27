// Gestion des maintenances
let currentPage = 1;
let perPage = 20;
let clients = [];
let users = [];
let currentMaintenanceId = null;

// Initialisation
document.addEventListener('DOMContentLoaded', () => {
    loadMaintenances();
    loadStats();
    loadClients();
    loadUsers();
    
    // Event listeners pour les filtres
    document.getElementById('searchInput')?.addEventListener('input', debounce(() => { currentPage = 1; loadMaintenances(); }, 300));
    document.getElementById('statusFilter')?.addEventListener('change', () => { currentPage = 1; loadMaintenances(); });
    document.getElementById('priorityFilter')?.addEventListener('change', () => { currentPage = 1; loadMaintenances(); });
    document.getElementById('overdueFilter')?.addEventListener('change', () => { currentPage = 1; loadMaintenances(); });
});

function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => { clearTimeout(timeout); func(...args); };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

// Charger les maintenances
async function loadMaintenances() {
    try {
        const search = document.getElementById('searchInput')?.value || '';
        const status = document.getElementById('statusFilter')?.value || '';
        const priority = document.getElementById('priorityFilter')?.value || '';
        const overdue = document.getElementById('overdueFilter')?.checked || false;
        
        let url = `/api/maintenances?page=${currentPage}&per_page=${perPage}`;
        if (search) url += `&search=${encodeURIComponent(search)}`;
        if (status) url += `&status=${status}`;
        if (priority) url += `&priority=${priority}`;
        if (overdue) url += `&overdue=true`;
        
        const response = await axios.get(url);
        const data = response.data;
        
        renderMaintenances(data.items);
        renderPagination(data.total, data.pages);
    } catch (error) {
        console.error('Erreur chargement maintenances:', error);
        showError('Erreur lors du chargement des maintenances');
    }
}

// Charger les statistiques
async function loadStats() {
    try {
        const response = await axios.get('/api/maintenances/stats');
        const stats = response.data;
        
        document.getElementById('statReceived').textContent = stats.received || 0;
        document.getElementById('statInProgress').textContent = stats.in_progress || 0;
        document.getElementById('statReady').textContent = stats.ready || 0;
        document.getElementById('statOverdue').textContent = stats.overdue || 0;
        document.getElementById('statPickedUp').textContent = stats.picked_up || 0;
        document.getElementById('statUrgent').textContent = stats.urgent || 0;
    } catch (error) {
        console.error('Erreur chargement stats:', error);
    }
}

// Charger les clients
async function loadClients() {
    try {
        const response = await axios.get('/api/clients?per_page=1000');
        clients = response.data.items || response.data || [];
        populateClientSelect();
    } catch (error) {
        console.error('Erreur chargement clients:', error);
    }
}

// Charger les utilisateurs (techniciens)
async function loadUsers() {
    try {
        const response = await axios.get('/api/auth/users');
        users = response.data || [];
        populateTechnicianSelect();
    } catch (error) {
        console.error('Erreur chargement users:', error);
    }
}

function populateClientSelect() {
    // Ne plus utiliser le select, on utilise maintenant la recherche
    setupClientSearch();
}

function setupClientSearch() {
    const searchInput = document.getElementById('clientSearchInput');
    const resultsDiv = document.getElementById('clientSearchResults');
    const hiddenSelect = document.getElementById('clientSelect');
    
    if (!searchInput || !resultsDiv) return;

    // IMPORTANT: Bootstrap modal utilise des transforms pendant l'animation.
    // Un élément position:fixed à l'intérieur d'un parent transformé se comporte mal.
    // On "porte" donc le dropdown dans document.body.
    try {
        if (resultsDiv.parentElement !== document.body) {
            document.body.appendChild(resultsDiv);
        }
        resultsDiv.style.position = 'fixed';
    } catch (e) {}

    const closeResults = () => { resultsDiv.style.display = 'none'; };
    let latestResults = [];

    const positionResults = () => {
        const rect = searchInput.getBoundingClientRect();
        resultsDiv.style.top = `${rect.bottom}px`;
        resultsDiv.style.left = `${rect.left}px`;
        resultsDiv.style.width = `${rect.width}px`;
        resultsDiv.style.zIndex = '2000';
    };

    const positionResultsDeferred = () => {
        requestAnimationFrame(() => {
            positionResults();
            requestAnimationFrame(() => {
                positionResults();
            });
        });
    };

    const openResults = () => {
        positionResultsDeferred();
        resultsDiv.style.display = 'block';
    };

    const renderList = async (term) => {
        const t = String(term || '').trim();
        try {
            const { data } = await axios.get('/api/clients/', { params: { search: t || undefined, limit: 15 } });
            const list = Array.isArray(data) ? data : (data.items || data || []);
            latestResults = list || [];
            if (!latestResults.length) {
                resultsDiv.innerHTML = '<div class="list-group list-group-flush"><div class="list-group-item text-muted small">Aucun client</div></div>';
            } else {
                resultsDiv.innerHTML = `
                    <div class="list-group list-group-flush">
                        ${latestResults.map(c => `
                            <button type="button" class="list-group-item list-group-item-action" data-client-id="${c.client_id}">
                                <div class="fw-semibold">${escapeHtml(c.name || '')}</div>
                                ${c.phone ? `<div class="small text-muted"><i class="bi bi-telephone"></i> ${escapeHtml(c.phone)}</div>` : ''}
                                ${c.email ? `<div class="small text-muted"><i class="bi bi-envelope"></i> ${escapeHtml(c.email)}</div>` : ''}
                            </button>
                        `).join('')}
                    </div>
                `;
            }
            openResults();
        } catch (e) {
            resultsDiv.innerHTML = '<div class="list-group list-group-flush"><div class="list-group-item text-muted small">Erreur de chargement</div></div>';
            openResults();
        }
    };

    searchInput.addEventListener('focus', () => {
        renderList(searchInput.value);
    });

    // Corriger le décalage lié à l'animation/positionnement de la modal
    try {
        const modalEl = searchInput.closest('.modal');
        if (modalEl && !modalEl.__clientSearchBound) {
            modalEl.__clientSearchBound = true;
            modalEl.addEventListener('shown.bs.modal', () => {
                if (resultsDiv.style.display === 'block') {
                    positionResultsDeferred();
                }
            });
            modalEl.addEventListener('hidden.bs.modal', () => {
                closeResults();
            });
        }
    } catch (e) {}

    searchInput.addEventListener('input', debounce((e) => {
        const v = (e && e.target && typeof e.target.value === 'string') ? e.target.value : (searchInput.value || '');
        if (!String(v || '').trim()) {
            closeResults();
            hiddenSelect.value = '';
            return;
        }
        renderList(v);
    }, 200));

    resultsDiv.addEventListener('click', (e) => {
        const btn = e.target.closest('[data-client-id]');
        if (!btn) return;
        const id = Number(btn.getAttribute('data-client-id'));
        const c = (latestResults || []).find(x => Number(x.client_id) === id) || (clients || []).find(x => Number(x.client_id) === id);
        if (c) {
            selectClient({
                client_id: c.client_id,
                name: c.name,
                phone: c.phone,
                email: c.email
            });
        } else {
            document.getElementById('clientSelect').value = String(id || '');
            closeResults();
        }
    });

    searchInput.addEventListener('keydown', (e) => {
        const items = resultsDiv.querySelectorAll('.list-group-item');
        const active = resultsDiv.querySelector('.list-group-item.active');
        let idx = Array.from(items).indexOf(active);

        if (e.key === 'ArrowDown') {
            e.preventDefault();
            if (idx < items.length - 1) {
                if (active) active.classList.remove('active');
                items[idx + 1]?.classList.add('active');
            } else if (idx === -1 && items.length) {
                items[0].classList.add('active');
            }
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            if (idx > 0) {
                if (active) active.classList.remove('active');
                items[idx - 1]?.classList.add('active');
            }
        } else if (e.key === 'Enter') {
            e.preventDefault();
            if (active) active.click();
            else if (items.length) items[0].click();
        } else if (e.key === 'Escape') {
            closeResults();
        }
    });

    document.addEventListener('click', (e) => {
        if (!searchInput.contains(e.target) && !resultsDiv.contains(e.target)) {
            closeResults();
        }
    });

    window.addEventListener('resize', () => {
        if (resultsDiv.style.display === 'block') positionResults();
    });

    window.addEventListener('scroll', () => {
        if (resultsDiv.style.display === 'block') positionResults();
    }, true);
}

function selectClient(client) {
    const clientId = client?.client_id;
    const name = client?.name || '';
    const phone = client?.phone || '';
    const email = client?.email || '';
    
    document.getElementById('clientSelect').value = clientId;
    document.getElementById('clientSearchInput').value = name;
    document.getElementById('clientName').value = name;
    document.getElementById('clientPhone').value = phone;
    document.getElementById('clientEmail').value = email;
    document.getElementById('clientSearchResults').style.display = 'none';
}

function populateTechnicianSelect() {
    const select = document.getElementById('technicianId');
    if (!select) return;
    select.innerHTML = '<option value="">Non assigné</option>';
    users.forEach(u => {
        select.innerHTML += `<option value="${u.user_id}">${escapeHtml(u.full_name || u.username)}</option>`;
    });
}

function fillClientInfo() {
    // Cette fonction n'est plus utilisée avec la nouvelle recherche
    // Gardée pour compatibilité
    const hiddenSelect = document.getElementById('clientSelect');
    const clientId = hiddenSelect?.value;
    
    if (!clientId) {
        document.getElementById('clientName').value = '';
        document.getElementById('clientPhone').value = '';
        document.getElementById('clientEmail').value = '';
        return;
    }
    
    const client = clients.find(c => c.client_id == clientId);
    if (client) {
        document.getElementById('clientName').value = client.name || '';
        document.getElementById('clientPhone').value = client.phone || '';
        document.getElementById('clientEmail').value = client.email || '';
    }
}

// Rendu du tableau
function renderMaintenances(items) {
    const tbody = document.getElementById('maintenancesTableBody');
    if (!tbody) return;
    
    if (!items || items.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="9" class="text-center text-muted py-4">
                    <i class="bi bi-inbox me-2"></i>Aucune maintenance trouvée
                </td>
            </tr>
        `;
        return;
    }
    
    tbody.innerHTML = items.map(m => {
        const statusBadge = getStatusBadge(m.status);
        const priorityBadge = getPriorityBadge(m.priority);
        const isOverdue = m.pickup_deadline && new Date(m.pickup_deadline) < new Date() && !m.pickup_date && ['completed', 'ready'].includes(m.status);
        
        return `
        <tr class="${isOverdue ? 'table-danger' : ''}">
            <td><strong>${escapeHtml(m.maintenance_number)}</strong></td>
            <td>
                <div>${escapeHtml(m.client_name)}</div>
                ${m.client_phone ? `<small class="text-muted">${escapeHtml(m.client_phone)}</small>` : ''}
            </td>
            <td>
                <div>${escapeHtml(m.device_type)}</div>
                <small class="text-muted">${escapeHtml((m.device_brand || '') + ' ' + (m.device_model || ''))}</small>
            </td>
            <td><small>${escapeHtml((m.problem_description || '').substring(0, 50))}${(m.problem_description || '').length > 50 ? '...' : ''}</small></td>
            <td>${formatDate(m.reception_date)}</td>
            <td>
                ${m.pickup_deadline ? formatDate(m.pickup_deadline) : '-'}
                ${isOverdue ? '<br><span class="badge bg-danger">En retard</span>' : ''}
            </td>
            <td>${statusBadge}</td>
            <td>${priorityBadge}</td>
            <td>
                <div class="btn-group btn-group-sm">
                    <button class="btn btn-outline-info" onclick="viewMaintenance(${m.maintenance_id})" title="Voir">
                        <i class="bi bi-eye"></i>
                    </button>
                    <button class="btn btn-outline-primary" onclick="editMaintenance(${m.maintenance_id})" title="Modifier">
                        <i class="bi bi-pencil"></i>
                    </button>
                    <button class="btn btn-outline-success" onclick="printMaintenanceSheetById(${m.maintenance_id})" title="Imprimer fiche">
                        <i class="bi bi-printer"></i>
                    </button>
                    ${m.status === 'ready' && !m.pickup_date ? `
                    <button class="btn btn-outline-warning" onclick="markPickedUp(${m.maintenance_id})" title="Marquer récupéré">
                        <i class="bi bi-box-arrow-up-right"></i>
                    </button>
                    ` : ''}
                    ${isOverdue ? `
                    <button class="btn btn-outline-danger" onclick="waiveLiability(${m.maintenance_id})" title="Dégager responsabilité">
                        <i class="bi bi-shield-x"></i>
                    </button>
                    ` : ''}
                </div>
            </td>
        </tr>
        `;
    }).join('');
}

function getStatusBadge(status) {
    const badges = {
        'received': '<span class="badge bg-info">Reçue</span>',
        'in_progress': '<span class="badge bg-warning text-dark">En cours</span>',
        'completed': '<span class="badge bg-primary">Terminée</span>',
        'ready': '<span class="badge bg-success">Prête</span>',
        'picked_up': '<span class="badge bg-secondary">Récupérée</span>',
        'abandoned': '<span class="badge bg-dark">Abandonnée</span>'
    };
    return badges[status] || `<span class="badge bg-light text-dark">${status}</span>`;
}

function getPriorityBadge(priority) {
    const badges = {
        'low': '<span class="badge bg-light text-dark">Basse</span>',
        'normal': '<span class="badge bg-secondary">Normale</span>',
        'high': '<span class="badge bg-warning text-dark">Haute</span>',
        'urgent': '<span class="badge bg-danger">Urgente</span>'
    };
    return badges[priority] || '';
}

function renderPagination(total, pages) {
    const pagination = document.getElementById('pagination');
    if (!pagination) return;
    
    if (pages <= 1) {
        pagination.innerHTML = '';
        return;
    }
    
    let html = '';
    
    // Précédent
    html += `<li class="page-item ${currentPage === 1 ? 'disabled' : ''}">
        <a class="page-link" href="#" onclick="goToPage(${currentPage - 1}); return false;">Précédent</a>
    </li>`;
    
    // Pages
    for (let i = 1; i <= pages; i++) {
        if (i === 1 || i === pages || (i >= currentPage - 2 && i <= currentPage + 2)) {
            html += `<li class="page-item ${i === currentPage ? 'active' : ''}">
                <a class="page-link" href="#" onclick="goToPage(${i}); return false;">${i}</a>
            </li>`;
        } else if (i === currentPage - 3 || i === currentPage + 3) {
            html += `<li class="page-item disabled"><span class="page-link">...</span></li>`;
        }
    }
    
    // Suivant
    html += `<li class="page-item ${currentPage === pages ? 'disabled' : ''}">
        <a class="page-link" href="#" onclick="goToPage(${currentPage + 1}); return false;">Suivant</a>
    </li>`;
    
    pagination.innerHTML = html;
}

function goToPage(page) {
    currentPage = page;
    loadMaintenances();
}

function resetFilters() {
    document.getElementById('searchInput').value = '';
    document.getElementById('statusFilter').value = '';
    document.getElementById('priorityFilter').value = '';
    document.getElementById('overdueFilter').checked = false;
    currentPage = 1;
    loadMaintenances();
}

// Ouvrir le modal de création
function openMaintenanceModal() {
    currentMaintenanceId = null;
    document.getElementById('maintenanceModalTitle').innerHTML = '<i class="bi bi-plus-circle me-2"></i>Nouvelle Maintenance';
    document.getElementById('maintenanceForm').reset();
    document.getElementById('maintenanceId').value = '';
    
    // Réinitialiser la recherche client
    document.getElementById('clientSearchInput').value = '';
    document.getElementById('clientSelect').value = '';
    document.getElementById('clientSearchResults').style.display = 'none';
    
    // Date de réception par défaut
    const now = new Date();
    document.getElementById('receptionDate').value = now.toISOString().slice(0, 16);
    
    // Date limite par défaut (30 jours)
    const deadline = new Date(now.getTime() + 30 * 24 * 60 * 60 * 1000);
    document.getElementById('pickupDeadline').value = deadline.toISOString().slice(0, 10);
    
    // Valeurs par défaut
    document.getElementById('status').value = 'received';
    document.getElementById('priority').value = 'normal';
    document.getElementById('warrantyDays').value = '30';
    document.getElementById('advancePaid').value = '0';
    
    const modal = new bootstrap.Modal(document.getElementById('maintenanceModal'));
    modal.show();
}

// Modifier une maintenance
async function editMaintenance(id) {
    try {
        const response = await axios.get(`/api/maintenances/${id}`);
        const m = response.data;
        
        currentMaintenanceId = id;
        document.getElementById('maintenanceModalTitle').innerHTML = '<i class="bi bi-pencil me-2"></i>Modifier Maintenance';
        document.getElementById('maintenanceId').value = id;
        
        // Remplir les champs client
        document.getElementById('clientSelect').value = m.client_id || '';
        document.getElementById('clientSearchInput').value = m.client_name || '';
        document.getElementById('clientName').value = m.client_name || '';
        document.getElementById('clientPhone').value = m.client_phone || '';
        document.getElementById('clientEmail').value = m.client_email || '';
        
        document.getElementById('deviceType').value = m.device_type || '';
        document.getElementById('deviceBrand').value = m.device_brand || '';
        document.getElementById('deviceModel').value = m.device_model || '';
        document.getElementById('deviceSerial').value = m.device_serial || '';
        document.getElementById('deviceDescription').value = m.device_description || '';
        document.getElementById('deviceAccessories').value = m.device_accessories || '';
        document.getElementById('deviceCondition').value = m.device_condition || '';
        
        document.getElementById('problemDescription').value = m.problem_description || '';
        document.getElementById('diagnosis').value = m.diagnosis || '';
        document.getElementById('workDone').value = m.work_done || '';
        
        if (m.reception_date) {
            document.getElementById('receptionDate').value = m.reception_date.slice(0, 16);
        }
        if (m.estimated_completion_date) {
            document.getElementById('estimatedCompletionDate').value = m.estimated_completion_date;
        }
        if (m.pickup_deadline) {
            document.getElementById('pickupDeadline').value = m.pickup_deadline;
        }
        
        document.getElementById('status').value = m.status || 'received';
        document.getElementById('priority').value = m.priority || 'normal';
        document.getElementById('technicianId').value = m.technician_id || '';
        document.getElementById('warrantyDays').value = m.warranty_days || 30;
        
        document.getElementById('estimatedCost').value = m.estimated_cost || '';
        document.getElementById('finalCost').value = m.final_cost || '';
        document.getElementById('advancePaid').value = m.advance_paid || 0;
        
        document.getElementById('notes').value = m.notes || '';
        document.getElementById('internalNotes').value = m.internal_notes || '';
        
        const modal = new bootstrap.Modal(document.getElementById('maintenanceModal'));
        modal.show();
    } catch (error) {
        console.error('Erreur chargement maintenance:', error);
        showError('Erreur lors du chargement de la maintenance');
    }
}

// Sauvegarder une maintenance
async function saveMaintenance() {
    try {
        const id = document.getElementById('maintenanceId').value;
        
        const data = {
            client_id: document.getElementById('clientSelect').value || null,
            client_name: document.getElementById('clientName').value,
            client_phone: document.getElementById('clientPhone').value || null,
            client_email: document.getElementById('clientEmail').value || null,
            
            device_type: document.getElementById('deviceType').value,
            device_brand: document.getElementById('deviceBrand').value || null,
            device_model: document.getElementById('deviceModel').value || null,
            device_serial: document.getElementById('deviceSerial').value || null,
            device_description: document.getElementById('deviceDescription').value || null,
            device_accessories: document.getElementById('deviceAccessories').value || null,
            device_condition: document.getElementById('deviceCondition').value || null,
            
            problem_description: document.getElementById('problemDescription').value,
            diagnosis: document.getElementById('diagnosis').value || null,
            work_done: document.getElementById('workDone').value || null,
            
            reception_date: document.getElementById('receptionDate').value || null,
            estimated_completion_date: document.getElementById('estimatedCompletionDate').value || null,
            pickup_deadline: document.getElementById('pickupDeadline').value || null,
            
            status: document.getElementById('status').value,
            priority: document.getElementById('priority').value,
            technician_id: document.getElementById('technicianId').value || null,
            warranty_days: parseInt(document.getElementById('warrantyDays').value) || 30,
            
            estimated_cost: parseFloat(document.getElementById('estimatedCost').value) || null,
            final_cost: parseFloat(document.getElementById('finalCost').value) || null,
            advance_paid: parseFloat(document.getElementById('advancePaid').value) || 0,
            
            notes: document.getElementById('notes').value || null,
            internal_notes: document.getElementById('internalNotes').value || null
        };
        
        // Validation
        if (!data.client_name) {
            showError('Le nom du client est requis');
            return;
        }
        if (!data.device_type) {
            showError('Le type d\'appareil est requis');
            return;
        }
        if (!data.problem_description) {
            showError('La description du problème est requise');
            return;
        }
        
        let response;
        if (id) {
            response = await axios.put(`/api/maintenances/${id}`, data);
            showSuccess('Maintenance mise à jour avec succès');
        } else {
            response = await axios.post('/api/maintenances', data);
            showSuccess('Maintenance créée avec succès');
        }
        
        // Fermer le modal
        const modal = bootstrap.Modal.getInstance(document.getElementById('maintenanceModal'));
        modal.hide();
        
        // Recharger
        loadMaintenances();
        loadStats();
    } catch (error) {
        console.error('Erreur sauvegarde maintenance:', error);
        showError(error.response?.data?.detail || 'Erreur lors de la sauvegarde');
    }
}

// Voir le détail d'une maintenance
async function viewMaintenance(id) {
    try {
        const response = await axios.get(`/api/maintenances/${id}`);
        const m = response.data;
        
        currentMaintenanceId = id;
        
        const isOverdue = m.pickup_deadline && new Date(m.pickup_deadline) < new Date() && !m.pickup_date && ['completed', 'ready'].includes(m.status);
        
        document.getElementById('maintenanceDetailTitle').innerHTML = `<i class="bi bi-info-circle me-2"></i>${m.maintenance_number}`;
        
        document.getElementById('maintenanceDetailContent').innerHTML = `
            <div class="row">
                <div class="col-md-6">
                    <h6 class="text-primary"><i class="bi bi-person me-2"></i>Client</h6>
                    <p><strong>${escapeHtml(m.client_name)}</strong></p>
                    ${m.client_phone ? `<p><i class="bi bi-telephone me-2"></i>${escapeHtml(m.client_phone)}</p>` : ''}
                    ${m.client_email ? `<p><i class="bi bi-envelope me-2"></i>${escapeHtml(m.client_email)}</p>` : ''}
                </div>
                <div class="col-md-6">
                    <h6 class="text-primary"><i class="bi bi-laptop me-2"></i>Appareil</h6>
                    <p><strong>${escapeHtml(m.device_type)}</strong> ${m.device_brand || ''} ${m.device_model || ''}</p>
                    ${m.device_serial ? `<p><small>S/N: ${escapeHtml(m.device_serial)}</small></p>` : ''}
                    ${m.device_accessories ? `<p><small>Accessoires: ${escapeHtml(m.device_accessories)}</small></p>` : ''}
                </div>
            </div>
            <hr>
            <div class="row">
                <div class="col-12">
                    <h6 class="text-primary"><i class="bi bi-bug me-2"></i>Problème</h6>
                    <p>${escapeHtml(m.problem_description || '-')}</p>
                </div>
                ${m.diagnosis ? `
                <div class="col-12">
                    <h6 class="text-primary"><i class="bi bi-search me-2"></i>Diagnostic</h6>
                    <p>${escapeHtml(m.diagnosis)}</p>
                </div>
                ` : ''}
                ${m.work_done ? `
                <div class="col-12">
                    <h6 class="text-primary"><i class="bi bi-check-circle me-2"></i>Travaux effectués</h6>
                    <p>${escapeHtml(m.work_done)}</p>
                </div>
                ` : ''}
            </div>
            <hr>
            <div class="row">
                <div class="col-md-4">
                    <p><strong>Réception:</strong> ${formatDate(m.reception_date)}</p>
                </div>
                <div class="col-md-4">
                    <p><strong>Limite récup.:</strong> ${m.pickup_deadline ? formatDate(m.pickup_deadline) : '-'}
                    ${isOverdue ? '<span class="badge bg-danger ms-2">EN RETARD</span>' : ''}</p>
                </div>
                <div class="col-md-4">
                    <p><strong>Statut:</strong> ${getStatusBadge(m.status)} ${getPriorityBadge(m.priority)}</p>
                </div>
            </div>
            <div class="row">
                <div class="col-md-4">
                    <p><strong>Coût estimé:</strong> ${m.estimated_cost ? formatCurrency(m.estimated_cost) : '-'}</p>
                </div>
                <div class="col-md-4">
                    <p><strong>Coût final:</strong> ${m.final_cost ? formatCurrency(m.final_cost) : '-'}</p>
                </div>
                <div class="col-md-4">
                    <p><strong>Avance:</strong> ${formatCurrency(m.advance_paid || 0)}</p>
                </div>
            </div>
            ${m.liability_waived ? `
            <div class="alert alert-danger mt-3">
                <i class="bi bi-shield-x me-2"></i>
                <strong>Responsabilité dégagée</strong> le ${formatDate(m.liability_waived_date)}
            </div>
            ` : ''}
            ${m.notes ? `
            <div class="alert alert-info mt-3">
                <strong>Notes:</strong> ${escapeHtml(m.notes)}
            </div>
            ` : ''}
        `;
        
        // Configurer le bouton modifier
        document.getElementById('btnEditMaintenance').onclick = () => {
            bootstrap.Modal.getInstance(document.getElementById('maintenanceDetailModal')).hide();
            editMaintenance(id);
        };
        
        // Afficher/masquer le bouton rappel
        const btnReminder = document.getElementById('btnSendReminder');
        if (btnReminder) {
            btnReminder.style.display = (m.status === 'ready' && m.client_phone) ? 'inline-block' : 'none';
        }
        
        const modal = new bootstrap.Modal(document.getElementById('maintenanceDetailModal'));
        modal.show();
    } catch (error) {
        console.error('Erreur chargement détail:', error);
        showError('Erreur lors du chargement du détail');
    }
}

// Marquer comme récupéré
async function markPickedUp(id) {
    if (!confirm('Confirmer que l\'appareil a été récupéré par le client ?')) return;
    
    try {
        await axios.post(`/api/maintenances/${id}/pickup`);
        showSuccess('Maintenance marquée comme récupérée');
        loadMaintenances();
        loadStats();
    } catch (error) {
        console.error('Erreur:', error);
        showError('Erreur lors de la mise à jour');
    }
}

// Dégager la responsabilité
async function waiveLiability(id) {
    if (!confirm('Êtes-vous sûr de vouloir dégager la responsabilité sur cette machine ?\n\nCette action est irréversible et marque la maintenance comme abandonnée.')) return;
    
    try {
        await axios.post(`/api/maintenances/${id}/waive-liability`);
        showSuccess('Responsabilité dégagée - Maintenance marquée comme abandonnée');
        loadMaintenances();
        loadStats();
    } catch (error) {
        console.error('Erreur:', error);
        showError('Erreur lors de la mise à jour');
    }
}

// Envoyer un rappel
async function sendReminder() {
    if (!currentMaintenanceId) return;
    
    try {
        const response = await axios.post(`/api/maintenances/${currentMaintenanceId}/send-reminder`);
        const data = response.data;
        
        if (data.maintenance?.client_phone) {
            // Ouvrir WhatsApp avec le message
            const phone = data.maintenance.client_phone.replace(/[\s\-\.]/g, '').trim();
            const phoneFormatted = phone.startsWith('+') ? phone : '+221' + phone.replace(/^0/, '');
            const message = encodeURIComponent(data.message);
            window.open(`https://wa.me/${phoneFormatted.replace('+', '')}?text=${message}`, '_blank');
            showSuccess('Rappel préparé - WhatsApp ouvert');
        } else {
            showError('Pas de numéro de téléphone pour ce client');
        }
    } catch (error) {
        console.error('Erreur envoi rappel:', error);
        showError(error.response?.data?.detail || 'Erreur lors de l\'envoi du rappel');
    }
}

// Imprimer la fiche de maintenance
function printMaintenanceSheet() {
    if (!currentMaintenanceId) return;
    printMaintenanceSheetById(currentMaintenanceId);
}

function printMaintenanceSheetById(id) {
    window.open(`/api/maintenances/${id}/print?kind=technician`, '_blank');
}

function printMaintenanceDocById(id, kind) {
    if (!id) return;
    const k = (kind || 'technician');
    window.open(`/api/maintenances/${id}/print?kind=${encodeURIComponent(k)}`, '_blank');
}

function printMaintenanceClientReceipt() {
    if (!currentMaintenanceId) return;
    printMaintenanceDocById(currentMaintenanceId, 'client');
}

function printMaintenanceLabel() {
    if (!currentMaintenanceId) return;
    printMaintenanceDocById(currentMaintenanceId, 'label');
}

function printMaintenanceTicket() {
    if (!currentMaintenanceId) return;
    printMaintenanceDocById(currentMaintenanceId, 'ticket');
}

// Utilitaires
function formatDate(dateStr) {
    if (!dateStr) return '-';
    const d = new Date(dateStr);
    return d.toLocaleDateString('fr-FR');
}

function formatCurrency(amount) {
    return new Intl.NumberFormat('fr-FR', { style: 'decimal' }).format(amount || 0) + ' FCFA';
}

function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function showSuccess(message) {
    // Utiliser le système de notification existant ou créer un toast
    if (typeof Swal !== 'undefined') {
        Swal.fire({ icon: 'success', title: 'Succès', text: message, timer: 2000, showConfirmButton: false });
    } else {
        alert(message);
    }
}

function showError(message) {
    if (typeof Swal !== 'undefined') {
        Swal.fire({ icon: 'error', title: 'Erreur', text: message });
    } else {
        alert('Erreur: ' + message);
    }
}
