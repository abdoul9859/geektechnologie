// Gestion des fournisseurs - Version 2.3 - Fixed empty state display
console.log('Suppliers.js loaded - Version 2.3');

let suppliers = [];
let currentSupplierId = null;
let isEditMode = false;

// Initialisation
document.addEventListener('DOMContentLoaded', function() {
    console.log('DOM loaded, checking elements...');
    
    // Vérifier et créer les éléments manquants si nécessaire
    ensureRequiredElements();
    
    console.log('After ensuring elements:');
    console.log('suppliersContainer:', document.getElementById('suppliersContainer'));
    console.log('emptyState:', document.getElementById('emptyState'));
    console.log('suppliersList:', document.getElementById('suppliersList'));
    
    // Même logique que products.js pour attendre l'auth prête (cookies HttpOnly)
    const ready = () => {
        const hasAuthManager = !!window.authManager;
        const hasUser = !!(hasAuthManager && window.authManager.userData && Object.keys(window.authManager.userData).length);
        return hasAuthManager && (window.authManager.isAuthenticatedSync() || hasUser);
    };

    // Initialiser immédiatement sans délai pour un chargement instantané
    loadSuppliers();
    setupEventListeners();
});

// S'assurer que les éléments requis existent
function ensureRequiredElements() {
    let container = document.getElementById('suppliersContainer');
    
    // Si le container principal n'existe pas, le créer
    if (!container) {
        console.log('Creating suppliersContainer...');
        const contentDiv = document.querySelector('.container-fluid') || document.querySelector('main') || document.body;
        container = document.createElement('div');
        container.id = 'suppliersContainer';
        container.className = 'mt-4';
        contentDiv.appendChild(container);
    }
    
    // S'assurer que emptyState existe
    let emptyState = document.getElementById('emptyState');
    if (!emptyState) {
        console.log('Creating emptyState...');
        emptyState = document.createElement('div');
        emptyState.id = 'emptyState';
        emptyState.className = 'text-center py-5';
        emptyState.innerHTML = `
            <i class="bi bi-truck display-1 text-muted"></i>
            <h4 class="text-muted mt-3">Aucun fournisseur</h4>
            <p class="text-muted">Commencez par ajouter votre premier fournisseur</p>
        `;
        container.appendChild(emptyState);
    }
    
    // S'assurer que suppliersList existe
    let suppliersList = document.getElementById('suppliersList');
    if (!suppliersList) {
        console.log('Creating suppliersList...');
        suppliersList = document.createElement('div');
        suppliersList.id = 'suppliersList';
        suppliersList.className = 'row';
        suppliersList.style.display = 'none';
        container.appendChild(suppliersList);
    }
    
    // S'assurer que resultsCount existe
    let resultsCount = document.getElementById('resultsCount');
    if (!resultsCount) {
        console.log('Creating resultsCount...');
        const searchSection = document.querySelector('.row.mb-4');
        if (searchSection) {
            const countDiv = document.createElement('div');
            countDiv.className = 'col-md-6 text-end';
            countDiv.innerHTML = '<small class="text-muted" id="resultsCount">0 fournisseur trouvé</small>';
            searchSection.appendChild(countDiv);
        }
    }
}

// Configuration des écouteurs d'événements
function setupEventListeners() {
    // Recherche en temps réel
    const searchInput = document.getElementById('searchInput');
    if (searchInput) {
        searchInput.addEventListener('input', debounce(filterSuppliers, 300));
    }

    // Formulaire de soumission
    const form = document.getElementById('supplierFormElement');
    if (form) {
        form.addEventListener('submit', handleFormSubmit);
    }

    // Modal de suppression
    const confirmDeleteBtn = document.getElementById('confirmDeleteBtn');
    if (confirmDeleteBtn) {
        confirmDeleteBtn.addEventListener('click', confirmDelete);
    }
}

// Charger les fournisseurs
async function loadSuppliers() {
    try {
        showLoading();
        
        // Appel API via utilitaire avec timeout pour éviter les chargements infinis
        const response = await safeLoadData(
            () => axios.get('/api/suppliers/'),
            {
                timeout: 8000,
                fallbackData: [],
                errorMessage: 'Erreur lors du chargement des fournisseurs'
            }
        );
        
        // L'API retourne un objet { suppliers, total, page, pages }
        const payload = response?.data ?? {};
        suppliers = Array.isArray(payload) ? payload : (payload.suppliers || []);
        console.log('Loaded suppliers:', payload);
        // Masquer le spinner avant l'affichage
        hideLoading();
        displaySuppliers();
        updateStatistics();
    } catch (error) {
        console.error('Erreur lors du chargement des fournisseurs:', error);
        hideLoading();
        showError(error.response?.data?.detail || 'Erreur lors du chargement des fournisseurs');
        hideLoading();
    }
}

// Afficher les fournisseurs
function displaySuppliers(suppliersToShow) {
    console.log('displaySuppliers called with:', suppliersToShow);
    console.log('Type of suppliersToShow:', typeof suppliersToShow);
    console.log('Is array:', Array.isArray(suppliersToShow));
    
    // S'assurer que les éléments existent avant de les utiliser
    ensureRequiredElements();
    
    const container = document.getElementById('suppliersContainer');
    const emptyState = document.getElementById('emptyState');
    const suppliersList = document.getElementById('suppliersList');
    
    console.log('Elements found:', { 
        container: !!container, 
        emptyState: !!emptyState, 
        suppliersList: !!suppliersList 
    });
    
    // Les éléments devraient maintenant exister grâce à ensureRequiredElements
    if (!container || !emptyState || !suppliersList) {
        console.error('Critical error: Required elements still missing after ensureRequiredElements');
        return;
    }
    
    // Par défaut, utiliser le tableau global si aucun paramètre n'est fourni
    if (!Array.isArray(suppliersToShow)) {
        suppliersToShow = Array.isArray(suppliers) ? suppliers : [];
    }
    
    if (suppliersToShow.length === 0) {
        emptyState.style.display = 'block';
        suppliersList.style.display = 'none';
        updateResultsCount(0);
        return;
    }

    emptyState.style.display = 'none';
    suppliersList.style.display = 'flex';
    suppliersList.innerHTML = '';

    suppliersToShow.forEach(supplier => {
        const supplierCard = createSupplierCard(supplier);
        suppliersList.appendChild(supplierCard);
    });

    updateResultsCount(suppliersToShow.length);
}

// Créer une carte fournisseur
function createSupplierCard(supplier) {
    const col = document.createElement('div');
    col.className = 'col-lg-6 col-xl-4 mb-4';

    col.innerHTML = `
        <div class="card h-100 shadow-sm border-0 supplier-card">
            <div class="card-body">
                <div class="d-flex justify-content-between align-items-start mb-3">
                    <div class="flex-grow-1">
                        <h5 class="card-title mb-1 text-primary">
                            <i class="bi bi-building me-2"></i>
                            ${escapeHtml(supplier.name)}
                        </h5>
                        <small class="text-muted">ID: ${supplier.id}</small>
                    </div>
                    <div class="dropdown">
                        <button class="btn btn-outline-secondary btn-sm dropdown-toggle" type="button" data-bs-toggle="dropdown">
                            <i class="bi bi-three-dots"></i>
                        </button>
                        <ul class="dropdown-menu">
                            <li>
                                <button class="dropdown-item" onclick="editSupplier(${supplier.id})">
                                    <i class="bi bi-pencil me-2"></i>
                                    Modifier
                                </button>
                            </li>
                            <li><hr class="dropdown-divider" /></li>
                            <li>
                                <button class="dropdown-item text-danger" onclick="deleteSupplier(${supplier.id})">
                                    <i class="bi bi-trash me-2"></i>
                                    Supprimer
                                </button>
                            </li>
                        </ul>
                    </div>
                </div>
                
                <div class="supplier-info">
                    ${supplier.contact_person ? `
                        <div class="mb-2">
                            <i class="bi bi-person text-muted me-2"></i>
                            <span class="text-muted small">Contact:</span>
                            <div class="ms-4">${escapeHtml(supplier.contact_person)}</div>
                        </div>
                    ` : ''}
                    
                    ${supplier.email ? `
                        <div class="mb-2">
                            <i class="bi bi-envelope text-muted me-2"></i>
                            <span class="text-muted small">Email:</span>
                            <div class="ms-4">
                                <a href="mailto:${supplier.email}" class="text-decoration-none">
                                    ${escapeHtml(supplier.email)}
                                </a>
                            </div>
                        </div>
                    ` : ''}
                    
                    ${supplier.phone ? `
                        <div class="mb-2">
                            <i class="bi bi-telephone text-muted me-2"></i>
                            <span class="text-muted small">Téléphone:</span>
                            <div class="ms-4">
                                <a href="tel:${supplier.phone}" class="text-decoration-none">
                                    ${escapeHtml(supplier.phone)}
                                </a>
                            </div>
                        </div>
                    ` : ''}
                    
                    ${supplier.address ? `
                        <div class="mb-2">
                            <i class="bi bi-geo-alt text-muted me-2"></i>
                            <span class="text-muted small">Adresse:</span>
                            <div class="ms-4 text-break">${escapeHtml(supplier.address)}</div>
                        </div>
                    ` : ''}
                </div>
            </div>
            
            <div class="card-footer bg-transparent border-top-0">
                <div class="d-flex justify-content-end gap-2">
                    <button 
                        onclick="editSupplier(${supplier.id})" 
                        class="btn btn-primary btn-sm"
                        title="Modifier"
                    >
                        <i class="bi bi-pencil"></i>
                    </button>
                    <button 
                        onclick="deleteSupplier(${supplier.id})" 
                        class="btn btn-outline-danger btn-sm"
                        title="Supprimer"
                    >
                        <i class="bi bi-trash"></i>
                    </button>
                </div>
            </div>
        </div>
    `;

    return col;
}

// Filtrer les fournisseurs
function filterSuppliers() {
    const searchTerm = document.getElementById('searchInput').value.toLowerCase();
    
    if (!searchTerm) {
        displaySuppliers(suppliers);
        return;
    }

    const filtered = suppliers.filter(supplier => 
        supplier.name.toLowerCase().includes(searchTerm) ||
        (supplier.contact_person && supplier.contact_person.toLowerCase().includes(searchTerm)) ||
        (supplier.email && supplier.email.toLowerCase().includes(searchTerm)) ||
        (supplier.phone && supplier.phone.includes(searchTerm)) ||
        (supplier.address && supplier.address.toLowerCase().includes(searchTerm))
    );

    displaySuppliers(filtered);
}

// Mettre à jour les statistiques
function updateStatistics() {
    const totalSuppliers = suppliers.length;
    const activeSuppliers = suppliers.filter(s => s.status === 'active' || !s.status).length;
    const suppliersWithEmail = suppliers.filter(s => s.email).length;
    const suppliersWithPhone = suppliers.filter(s => s.phone).length;

    // Mettre à jour les éléments avec vérification d'existence
    const totalElement = document.getElementById('totalSuppliers');
    const activeElement = document.getElementById('activeSuppliers');
    const emailElement = document.getElementById('suppliersWithEmail');
    const phoneElement = document.getElementById('suppliersWithPhone');

    if (totalElement) totalElement.textContent = totalSuppliers;
    if (activeElement) activeElement.textContent = activeSuppliers;
    if (emailElement) emailElement.textContent = suppliersWithEmail;
    if (phoneElement) phoneElement.textContent = suppliersWithPhone;
}

// Mettre à jour le compteur de résultats
function updateResultsCount(count) {
    const resultsCount = document.getElementById('resultsCount');
    if (resultsCount) {
        resultsCount.textContent = `${count} fournisseur${count !== 1 ? 's' : ''} trouvé${count !== 1 ? 's' : ''}`;
    }
}

// Basculer l'affichage du formulaire
function toggleForm() {
    const form = document.getElementById('supplierForm');
    const btn = document.getElementById('toggleFormBtn');
    
    if (form.style.display === 'none') {
        form.style.display = 'block';
        btn.innerHTML = '<i class="bi bi-x-circle me-2"></i>Annuler';
        btn.className = 'btn btn-outline-secondary btn-lg shadow-sm';
    } else {
        resetForm();
    }
}

// Réinitialiser le formulaire
function resetForm() {
    const form = document.getElementById('supplierFormElement');
    const formContainer = document.getElementById('supplierForm');
    const btn = document.getElementById('toggleFormBtn');
    const formTitle = document.getElementById('formTitle');
    const submitBtnText = document.getElementById('submitBtnText');

    form.reset();
    formContainer.style.display = 'none';
    btn.innerHTML = '<i class="bi bi-plus-circle me-2"></i>Ajouter un Fournisseur';
    btn.className = 'btn btn-success btn-lg shadow-sm';
    
    formTitle.innerHTML = '<i class="bi bi-plus-circle me-2"></i>Ajouter un Fournisseur';
    submitBtnText.textContent = 'Ajouter';
    
    isEditMode = false;
    currentSupplierId = null;
    document.getElementById('supplierId').value = '';
}

// Modifier un fournisseur
function editSupplier(id) {
    const supplier = suppliers.find(s => s.id === id);
    if (!supplier) return;

    // Remplir le formulaire
    document.getElementById('supplierId').value = supplier.id;
    document.getElementById('name').value = supplier.name || '';
    document.getElementById('contact_person').value = supplier.contact_person || '';
    document.getElementById('email').value = supplier.email || '';
    document.getElementById('phone').value = supplier.phone || '';
    document.getElementById('address').value = supplier.address || '';

    // Mettre à jour l'interface
    const formContainer = document.getElementById('supplierForm');
    const btn = document.getElementById('toggleFormBtn');
    const formTitle = document.getElementById('formTitle');
    const submitBtnText = document.getElementById('submitBtnText');

    formContainer.style.display = 'block';
    btn.innerHTML = '<i class="bi bi-x-circle me-2"></i>Annuler';
    btn.className = 'btn btn-outline-secondary btn-lg shadow-sm';
    
    formTitle.innerHTML = '<i class="bi bi-pencil me-2"></i>Modifier le Fournisseur';
    submitBtnText.textContent = 'Mettre à jour';
    
    isEditMode = true;
    currentSupplierId = id;

    // Scroll vers le formulaire
    formContainer.scrollIntoView({ behavior: 'smooth' });
}

// Gérer la soumission du formulaire
async function handleFormSubmit(e) {
    e.preventDefault();
    
    const supplierData = {
        name: document.getElementById('name').value,
        contact_person: document.getElementById('contact_person').value,
        email: document.getElementById('email').value,
        phone: document.getElementById('phone').value,
        address: document.getElementById('address').value
    };

    // Validation
    if (!supplierData.name.trim()) {
        showError('Le nom du fournisseur est obligatoire');
        return;
    }

    try {
        let response;
        
        if (isEditMode && currentSupplierId) {
            response = await axios.put(`/api/suppliers/${currentSupplierId}`, supplierData);
        } else {
            response = await axios.post('/api/suppliers', supplierData);
        }

        if (response.status && response.status >= 400) {
            throw new Error('Erreur lors de l\'enregistrement du fournisseur');
        }

        showSuccess(isEditMode ? 'Fournisseur modifié avec succès' : 'Fournisseur ajouté avec succès');
        resetForm();
        loadSuppliers();
    } catch (error) {
        console.error('Erreur:', error);
        showError(error.response?.data?.detail || 'Erreur lors de l\'enregistrement du fournisseur');
    }
}

// Supprimer un fournisseur
function deleteSupplier(id) {
    currentSupplierId = id;
    const modal = new bootstrap.Modal(document.getElementById('deleteModal'));
    modal.show();
}

// Confirmer la suppression
async function confirmDelete() {
    if (!currentSupplierId) return;

    try {
        await axios.delete(`/api/suppliers/${currentSupplierId}`);

        showSuccess('Fournisseur supprimé avec succès');
        
        // Fermer la modal
        const modal = bootstrap.Modal.getInstance(document.getElementById('deleteModal'));
        modal.hide();
        
        // Recharger les données
        loadSuppliers();
        currentSupplierId = null;
    } catch (error) {
        console.error('Erreur:', error);
        showError(error.response?.data?.detail || 'Erreur lors de la suppression du fournisseur');
    }
}

// Afficher le loading
function showLoading() {
    // Ne pas afficher d'indicateur de chargement pour une expérience instantanée
}

// Masquer le loading
function hideLoading() {
    // Récupérer le conteneur
    const container = document.getElementById('suppliersContainer');
    
    // Vérifier si le conteneur contient l'indicateur de chargement
    const loadingSpinner = container.querySelector('.spinner-border');
    
    if (loadingSpinner) {
        // Vider le conteneur de chargement
        container.innerHTML = '';
        
        // Recréer l'état vide dans le conteneur
        const emptyState = document.createElement('div');
        emptyState.id = 'emptyState';
        emptyState.className = 'text-center py-5';
        emptyState.innerHTML = `
            <i class="bi bi-truck display-1 text-muted"></i>
            <h4 class="text-muted mt-3">Aucun fournisseur</h4>
            <p class="text-muted">Commencez par ajouter votre premier fournisseur</p>
        `;
        container.appendChild(emptyState);
    }
    
    // S'assurer que suppliersList existe en dehors du conteneur
    let suppliersList = document.getElementById('suppliersList');
    if (!suppliersList) {
        // Créer la liste des fournisseurs après le conteneur
        const parentElement = container.parentElement;
        suppliersList = document.createElement('div');
        suppliersList.id = 'suppliersList';
        suppliersList.className = 'row';
        suppliersList.style.display = 'none';
        parentElement.insertBefore(suppliersList, container.nextSibling);
    }
}
