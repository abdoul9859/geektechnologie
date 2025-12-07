// Variables globales
let currentInvoiceId = null;
let invoices = [];
let suppliers = [];
let products = [];
let currentPage = 1;
let itemsPerPage = 20;
let currentFilters = {
    search: '',
    supplier_id: null,
    status: null
};

// Initialisation
function initializeSupplierInvoices() {
    loadSuppliers();
    loadProducts();
    loadInvoices();
    loadSummaryStats();
    setupEventListeners();
    setupFormValidation();
    
    // Le formulaire simplifié n'a plus de champs de date
    // Les dates seront gérées automatiquement par le serveur
}

// Configuration des écouteurs d'événements
function setupEventListeners() {
    // Recherche en temps réel
    document.getElementById('searchInput').addEventListener('input', debounce(handleSearch, 300));
    
    // Filtres
    document.getElementById('supplierFilter').addEventListener('change', handleFilterChange);
    document.getElementById('statusFilter').addEventListener('change', handleFilterChange);
    
    // Formulaire de facture
    document.getElementById('invoiceForm').addEventListener('submit', handleInvoiceFormSubmit);
    document.getElementById('paymentForm').addEventListener('submit', handlePaymentFormSubmit);
    
    // Calculs automatiques
    // Modal de paiement
    document.getElementById('addPaymentBtn').addEventListener('click', () => {
        if (currentInvoiceId) {
            openPaymentModal(currentInvoiceId);
        }
    });
}

// Validation des formulaires
function setupFormValidation() {
    const invoiceForm = document.getElementById('invoiceForm');
    const paymentForm = document.getElementById('paymentForm');

    invoiceForm.addEventListener('input', function(e) {
        const field = e.target;
        if (field.validity.valid) {
            field.classList.remove('is-invalid');
            field.classList.add('is-valid');
        } else {
            field.classList.remove('is-valid');
            field.classList.add('is-invalid');
        }
    });

    paymentForm.addEventListener('input', function(e) {
        const field = e.target;
        if (field.validity.valid) {
            field.classList.remove('is-invalid');
            field.classList.add('is-valid');
        } else {
            field.classList.remove('is-valid');
            field.classList.add('is-invalid');
        }
    });
}

// Charger les fournisseurs
async function loadSuppliers() {
    try {
        const response = await axios.get('/api/suppliers');
        suppliers = response.data.suppliers || response.data;
        
        // Remplir uniquement le filtre (pas le select car on utilise maintenant la recherche)
        const supplierFilter = document.getElementById('supplierFilter');
        supplierFilter.innerHTML = '<option value="">Tous les fournisseurs</option>';
        
        suppliers.forEach(supplier => {
            const filterOption = new Option(supplier.name, supplier.supplier_id);
            supplierFilter.appendChild(filterOption);
        });
        
        // Initialiser la recherche de fournisseur
        setupSupplierSearch();
    } catch (error) {
        console.error('Erreur lors du chargement des fournisseurs:', error);
        showError('Erreur lors du chargement des fournisseurs');
    }
}

// Configurer la recherche de fournisseur
function setupSupplierSearch() {
    const searchInput = document.getElementById('supplierSearch');
    const searchResults = document.getElementById('supplierSearchResults');
    
    if (!searchInput) return;
    
    // Recherche en temps réel
    searchInput.addEventListener('input', debounce(function(e) {
        const inputVal = (e && e.target && typeof e.target.value === 'string') ? e.target.value : (searchInput.value || '');
        const searchTerm = inputVal.toLowerCase().trim();
        
        if (searchTerm.length < 2) {
            searchResults.style.display = 'none';
            return;
        }
        
        const filteredSuppliers = suppliers.filter(supplier => {
            const name = supplier.name || '';
            const contact = supplier.contact || '';
            return name.toLowerCase().includes(searchTerm) ||
                   contact.toLowerCase().includes(searchTerm);
        });
        
        if (filteredSuppliers.length === 0) {
            searchResults.innerHTML = `
                <button type="button" class="list-group-item list-group-item-action text-center text-muted">
                    <i class="bi bi-search"></i> Aucun fournisseur trouvé - Cliquez sur "Nouveau" pour créer
                </button>
            `;
        } else {
            searchResults.innerHTML = filteredSuppliers.slice(0, 5).map(supplier => `
                <button type="button" class="list-group-item list-group-item-action" 
                        onclick="selectSupplier(${supplier.supplier_id}, '${escapeHtml(supplier.name)}')"
                        data-supplier-id="${supplier.supplier_id}">
                    <div class="d-flex justify-content-between align-items-center">
                        <div>
                            <strong>${escapeHtml(supplier.name)}</strong>
                            ${supplier.contact ? `<small class="text-muted d-block">${escapeHtml(supplier.contact)}</small>` : ''}
                        </div>
                        ${supplier.phone ? `<small class="text-muted">${escapeHtml(supplier.phone)}</small>` : ''}
                    </div>
                </button>
            `).join('');
        }
        
        searchResults.style.display = 'block';
    }, 300));
    
    // Fermer les résultats quand on clique ailleurs
    document.addEventListener('click', function(e) {
        if (!searchInput.contains(e.target) && !searchResults.contains(e.target)) {
            searchResults.style.display = 'none';
        }
    });
    
    // Gérer la sélection avec le clavier
    searchInput.addEventListener('keydown', function(e) {
        const items = searchResults.querySelectorAll('.list-group-item');
        const activeItem = searchResults.querySelector('.list-group-item.active');
        let currentIndex = Array.from(items).indexOf(activeItem);
        
        if (e.key === 'ArrowDown') {
            e.preventDefault();
            if (currentIndex < items.length - 1) {
                if (activeItem) activeItem.classList.remove('active');
                items[currentIndex + 1].classList.add('active');
            } else if (currentIndex === -1 && items.length > 0) {
                items[0].classList.add('active');
            }
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            if (currentIndex > 0) {
                if (activeItem) activeItem.classList.remove('active');
                items[currentIndex - 1].classList.add('active');
            }
        } else if (e.key === 'Enter') {
            e.preventDefault();
            if (activeItem) {
                activeItem.click();
            }
        }
    });
}

// Sélectionner un fournisseur
function selectSupplier(supplierId, supplierName) {
    document.getElementById('supplierId').value = supplierId;
    document.getElementById('supplierSearch').value = supplierName;
    document.getElementById('supplierSearchResults').style.display = 'none';
}

// Ouvrir la modal de création rapide de fournisseur
function openQuickSupplierModal() {
    // Fermer temporairement la modal de facture
    const invoiceModal = bootstrap.Modal.getInstance(document.getElementById('invoiceModal'));
    if (invoiceModal) {
        invoiceModal.hide();
    }
    
    // Réinitialiser le formulaire
    document.getElementById('quickSupplierForm').reset();
    
    // Ouvrir la modal de création
    const modal = new bootstrap.Modal(document.getElementById('quickSupplierModal'));
    modal.show();
    
    // Gérer la soumission du formulaire
    document.getElementById('quickSupplierForm').onsubmit = async function(e) {
        e.preventDefault();
        await createQuickSupplier();
    };
}

// Créer un nouveau fournisseur rapidement
async function createQuickSupplier() {
    const supplierData = {
        name: document.getElementById('quickSupplierName').value,
        contact: document.getElementById('quickSupplierContact').value || null,
        phone: document.getElementById('quickSupplierPhone').value || null,
        email: document.getElementById('quickSupplierEmail').value || null,
        address: document.getElementById('quickSupplierAddress').value || null
    };
    
    try {
        const response = await axios.post('/api/suppliers', supplierData);
        const newSupplier = response.data;
        
        // Ajouter le nouveau fournisseur à la liste
        suppliers.push(newSupplier);
        
        // Sélectionner automatiquement le nouveau fournisseur
        selectSupplier(newSupplier.supplier_id, newSupplier.name);
        
        // Fermer la modal de création
        const modal = bootstrap.Modal.getInstance(document.getElementById('quickSupplierModal'));
        modal.hide();
        
        // Rouvrir la modal de facture
        const invoiceModal = new bootstrap.Modal(document.getElementById('invoiceModal'));
        invoiceModal.show();
        
        showSuccess('Fournisseur créé avec succès');
        
        // Recharger la liste des fournisseurs pour mettre à jour le filtre
        loadSuppliers();
        
    } catch (error) {
        console.error('Erreur lors de la création du fournisseur:', error);
        showError(error.response?.data?.detail || 'Erreur lors de la création du fournisseur');
    }
}

// Charger les produits
async function loadProducts() {
    try {
        const response = await axios.get('/api/products');
        products = response.data.products || response.data;
    } catch (error) {
        console.error('Erreur lors du chargement des produits:', error);
        products = [];
    }
}

// Charger les factures
async function loadInvoices() {
    try {
        showLoading();
        
        // Construire les paramètres de manière plus simple
        const queryParams = {
            skip: (currentPage - 1) * itemsPerPage,
            limit: itemsPerPage
        };
        
        // Ajouter les filtres seulement s'ils ont une valeur valide
        if (currentFilters.search && currentFilters.search.trim()) {
            queryParams.search = currentFilters.search.trim();
        }
        if (currentFilters.supplier_id && currentFilters.supplier_id !== 'null' && currentFilters.supplier_id !== null) {
            queryParams.supplier_id = currentFilters.supplier_id;
        }
        if (currentFilters.status && currentFilters.status !== 'null' && currentFilters.status !== null) {
            queryParams.status = currentFilters.status;
        }
        
        const response = await axios.get('/api/supplier-invoices/', { params: queryParams });
        invoices = response.data.invoices || [];
        
        displayInvoices();
        updatePagination(response.data.total || 0);
        hideLoading();
        
    } catch (error) {
        console.error('Erreur lors du chargement des factures:', error);
        hideLoading();
        if (error.response && error.response.status === 401) {
            showError('Vous devez être connecté pour accéder à cette page');
            // Redirection gérée automatiquement par http.js
        } else {
            showError('Erreur lors du chargement des factures: ' + (error.response?.data?.detail || error.message));
        }
    }
}

// Charger les statistiques
async function loadSummaryStats() {
    try {
        const response = await axios.get('/api/supplier-invoices/stats/summary');
        const stats = response.data;
        
        document.getElementById('totalInvoices').textContent = stats.total_invoices;
        document.getElementById('pendingInvoices').textContent = stats.pending_invoices;
        document.getElementById('totalAmount').textContent = formatCurrency(stats.total_amount);
        document.getElementById('remainingAmount').textContent = formatCurrency(stats.remaining_amount);
        
    } catch (error) {
        console.error('Erreur lors du chargement des statistiques:', error);
    }
}

// Afficher les factures
function displayInvoices() {
    const tbody = document.getElementById('invoicesTableBody');
    
    if (invoices.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="10" class="text-center py-4">
                    <i class="bi bi-inbox text-muted" style="font-size: 2rem;"></i>
                    <p class="text-muted mt-2">Aucune facture trouvée</p>
                </td>
            </tr>
        `;
        return;
    }
    
    tbody.innerHTML = invoices.map(invoice => `
        <tr>
            <td>
                <strong>${escapeHtml(invoice.invoice_number)}</strong>
            </td>
            <td>${escapeHtml(invoice.supplier_name || 'N/A')}</td>
            <td>${invoice.invoice_date ? formatDateTime(invoice.invoice_date) : '-'}</td>
            <td>${invoice.due_date ? formatDateTime(invoice.due_date) : '-'}</td>
            <td class="amount-display">${invoice.amount ? formatCurrency(invoice.amount) : '-'}</td>
            <td class="amount-display">${formatCurrency(invoice.paid_amount || 0)}</td>
            <td class="amount-display">${formatCurrency(invoice.remaining_amount || 0)}</td>
            <td>
                <span class="badge ${getStatusBadgeClass(invoice.status)}">
                    ${getStatusLabel(invoice.status)}
                </span>
            </td>
            <td>
                ${invoice.pdf_path ? `
                    <a href="/api/supplier-invoices/pdf/${invoice.invoice_id}" target="_blank" class="btn btn-sm btn-outline-primary" title="Voir PDF">
                        <i class="bi bi-file-pdf"></i>
                    </a>
                ` : '<span class="text-muted">-</span>'}
            </td>
            <td>
                <div class="btn-group btn-group-sm">
                    <button class="btn btn-outline-info" onclick="viewInvoice(${invoice.invoice_id})" title="Voir détails">
                        <i class="bi bi-eye"></i>
                    </button>
                    <button class="btn btn-outline-primary" onclick="editInvoice(${invoice.invoice_id})" title="Modifier">
                        <i class="bi bi-pencil"></i>
                    </button>
                    ${invoice.remaining_amount > 0 ? `
                        <button class="btn btn-outline-success" onclick="openPaymentModal(${invoice.invoice_id})" title="Ajouter paiement">
                            <i class="bi bi-credit-card"></i>
                        </button>
                    ` : ''}
                    <button class="btn btn-outline-danger" onclick="deleteInvoice(${invoice.invoice_id})" title="Supprimer">
                        <i class="bi bi-trash"></i>
                    </button>
                </div>
            </td>
        </tr>
    `).join('');
}

// Ouvrir la modal de facture
function openInvoiceModal(invoiceId = null) {
    currentInvoiceId = invoiceId;
    const modal = new bootstrap.Modal(document.getElementById('invoiceModal'));
    const title = document.getElementById('invoiceModalLabel');
    const saveBtn = document.getElementById('saveButtonText');
    
    if (invoiceId) {
        title.textContent = 'Modifier la facture';
        saveBtn.textContent = 'Mettre à jour';
        loadInvoiceForEdit(invoiceId);
    } else {
        title.textContent = 'Nouvelle facture fournisseur';
        saveBtn.textContent = 'Enregistrer';
        resetInvoiceForm();
        // plus d'items à ajouter dans le mode 'suivi dettes'
    }
    
    modal.show();
}

// Réinitialiser le formulaire de facture
function resetInvoiceForm() {
    document.getElementById('invoiceForm').reset();
    
    // Supprimer les classes de validation
    document.querySelectorAll('.form-control, .form-select').forEach(field => {
        field.classList.remove('is-valid', 'is-invalid');
    });
    
    // Réinitialiser les champs spécifiques du formulaire simplifié
    const supplierSearch = document.getElementById('supplierSearch');
    const supplierId = document.getElementById('supplierId');
    const pdfFile = document.getElementById('pdfFile');
    
    if (supplierSearch) supplierSearch.value = '';
    if (supplierId) supplierId.value = '';
    if (pdfFile) pdfFile.value = '';
}

// Charger une facture pour édition
async function loadInvoiceForEdit(invoiceId) {
    try {
        const response = await axios.get(`/api/supplier-invoices/${invoiceId}`);
        const invoice = response.data;

        // Remplir le fournisseur avec son nom
        document.getElementById('supplierId').value = invoice.supplier_id;
        document.getElementById('supplierSearch').value = invoice.supplier_name || '';
        
        // Le formulaire simplifié ne permet plus l'édition des détails
        // Seul le fournisseur et le PDF sont modifiables
        console.log('Mode édition non supporté avec le formulaire simplifié');
    } catch (error) {
        console.error('Erreur lors du chargement de la facture:', error);
        showError('Erreur lors du chargement de la facture');
    }
}

// Ajouter un élément de facture
// Fonction de gestion des items supprimée dans le mode dettes fournisseur. Rien à faire ici.

// Supprimer un élément de facture
function removeInvoiceItem(button) {
    button.closest('tr').remove();
    calculateTotals();
}

// Gérer le changement de produit
function handleProductChange(select, itemIndex) {
    const productId = parseInt(select.value);
    const product = products.find(p => p.product_id === productId);
    
    if (product) {
        const row = select.closest('tr');
        row.querySelector('input[name*="[product_name]"]').value = product.name;
        row.querySelector('input[name*="[unit_price]"]').value = product.purchase_price;
        calculateItemTotal(itemIndex);
    }
}

// Calculer le total d'un élément
function calculateItemTotal(itemIndex) {
    const container = document.getElementById('invoiceItems');
    const row = container.children[itemIndex];
    
    const quantity = parseFloat(row.querySelector('.quantity-input').value) || 0;
    const unitPrice = parseFloat(row.querySelector('.unit-price-input').value) || 0;
    const total = quantity * unitPrice;
    
    row.querySelector('.total-input').value = total.toFixed(2);
    
    calculateTotals();
}

// Calculer les totaux
function calculateTotals() {
    const container = document.getElementById('invoiceItems');
    const taxRate = parseFloat(document.getElementById('taxRate').value) || 0;
    
    let subtotal = 0;
    
    for (const row of container.children) {
        const total = parseFloat(row.querySelector('.total-input').value) || 0;
        subtotal += total;
    }
    
    const taxAmount = (subtotal * taxRate) / 100;
    const total = subtotal + taxAmount;
    
    document.getElementById('subtotalDisplay').textContent = formatCurrency(subtotal);
    document.getElementById('taxDisplay').textContent = formatCurrency(taxAmount);
    document.getElementById('totalDisplay').textContent = formatCurrency(total);
}

// Gérer la soumission du formulaire de facture
async function handleInvoiceFormSubmit(e) {
    e.preventDefault();
    
    if (!validateInvoiceForm()) {
        return;
    }
    
    try {
        const formData = collectInvoiceFormData();
        
        if (currentInvoiceId) {
            await axios.put(`/api/supplier-invoices/${currentInvoiceId}`, formData, {
                headers: { 'Content-Type': 'multipart/form-data' }
            });
            showSuccess('Facture mise à jour avec succès');
        } else {
            await axios.post('/api/supplier-invoices', formData, {
                headers: { 'Content-Type': 'multipart/form-data' }
            });
            showSuccess('Facture créée avec succès');
        }
        
        const modal = bootstrap.Modal.getInstance(document.getElementById('invoiceModal'));
        modal.hide();
        
        loadInvoices();
        loadSummaryStats();
        
    } catch (error) {
        console.error('Erreur lors de la sauvegarde:', error);
        showError(error.response?.data?.detail || 'Erreur lors de la sauvegarde');
    }
}

// Valider le formulaire de facture (version simplifiée)
function validateInvoiceForm() {
    const supplierId = document.getElementById('supplierId');
    const pdfFile = document.getElementById('pdfFile');
    
    let isValid = true;

    // Vérifier le fournisseur
    if (!supplierId.value) {
        supplierId.classList.add('is-invalid');
        isValid = false;
    } else {
        supplierId.classList.remove('is-invalid');
    }
    
    // Vérifier le fichier PDF
    if (!pdfFile.files || pdfFile.files.length === 0) {
        pdfFile.classList.add('is-invalid');
        isValid = false;
    } else {
        pdfFile.classList.remove('is-invalid');
        
        // Vérifier que c'est un PDF
        const file = pdfFile.files[0];
        if (file && file.type !== 'application/pdf') {
            pdfFile.classList.add('is-invalid');
            showError('Seuls les fichiers PDF sont acceptés');
            isValid = false;
        }
    }

    return isValid;
}

// Collecter les données du formulaire
function collectInvoiceFormData() {
    const formData = new FormData();
    
    // Seulement le fournisseur et le PDF
    formData.append('supplier_id', document.getElementById('supplierId').value);
    
    // Ajouter le fichier PDF (obligatoire)
    const pdfFile = document.getElementById('pdfFile').files[0];
    if (pdfFile) {
        formData.append('pdf_file', pdfFile);
    }
    
    return formData;
}

// Voir les détails d'une facture
async function viewInvoice(invoiceId) {
    try {
        const response = await axios.get(`/api/supplier-invoices/${invoiceId}`);
        const invoice = response.data;
        
        currentInvoiceId = invoiceId;
        
        const content = document.getElementById('invoiceDetailsContent');
        content.innerHTML = `
            <div class="row">
                <div class="col-md-6">
                    <h6>Informations générales</h6>
                    <p><strong>N° Facture :</strong> ${escapeHtml(invoice.invoice_number || 'Non défini')}</p>
                    <p><strong>Fournisseur :</strong> ${escapeHtml(invoice.supplier_name)}</p>
                    <p><strong>Date facture :</strong> ${invoice.invoice_date ? formatDateTime(invoice.invoice_date) : 'Non définie'}</p>
                    <p><strong>Date échéance :</strong> ${invoice.due_date ? formatDateTime(invoice.due_date) : 'Non définie'}</p>
                    <p><strong>Méthode de paiement :</strong> ${invoice.payment_method ? escapeHtml(invoice.payment_method) : 'Non spécifié'}</p>
                    <p><strong>Statut :</strong> 
                        <span class="badge ${getStatusBadgeClass(invoice.status)}">
                            ${getStatusLabel(invoice.status)}
                        </span>
                    </p>
                </div>
                <div class="col-md-6">
                    <h6>Montants</h6>
                    <p><strong>Montant total :</strong> ${invoice.amount ? formatCurrency(invoice.amount) : 'Non défini'}</p>
                    <p><strong>Montant payé :</strong> ${formatCurrency(invoice.paid_amount || 0)}</p>
                    <p><strong>Montant restant :</strong> ${formatCurrency(invoice.remaining_amount || 0)}</p>
                </div>
            </div>
            
            ${invoice.description ? `
                <div class="row mt-3">
                    <div class="col-12">
                        <h6>Description</h6>
                        <p>${escapeHtml(invoice.description)}</p>
                    </div>
                </div>
            ` : ''}
            
            ${invoice.pdf_path ? `
                <div class="row mt-3">
                    <div class="col-12">
                        <h6>Document PDF</h6>
                        <a href="/api/supplier-invoices/pdf/${invoice.invoice_id}" target="_blank" class="btn btn-primary">
                            <i class="bi bi-file-pdf me-2"></i>Voir le PDF de la facture
                        </a>
                        <small class="text-muted d-block mt-1">${escapeHtml(invoice.pdf_filename || 'Document PDF')}</small>
                    </div>
                </div>
            ` : ''}
            
            ${invoice.notes ? `
                <div class="row mt-3">
                    <div class="col-12">
                        <h6>Notes</h6>
                        <p>${escapeHtml(invoice.notes)}</p>
                    </div>
                </div>
            ` : ''}
        `;
        
        // Afficher/masquer le bouton de paiement
        const addPaymentBtn = document.getElementById('addPaymentBtn');
        addPaymentBtn.style.display = invoice.remaining_amount > 0 ? 'inline-block' : 'none';
        
        const modal = new bootstrap.Modal(document.getElementById('invoiceDetailsModal'));
        modal.show();
        
    } catch (error) {
        console.error('Erreur lors du chargement des détails:', error);
        showError('Erreur lors du chargement des détails');
    }
}

// Modifier une facture
function editInvoice(invoiceId) {
    openInvoiceModal(invoiceId);
}

// Supprimer une facture
function deleteInvoice(invoiceId) {
    currentInvoiceId = invoiceId;
    
    // Trouver la facture pour vérifier si elle a des paiements
    const invoice = invoices.find(inv => inv.invoice_id === invoiceId);
    if (invoice && invoice.paid_amount > 0) {
        // Personnaliser le message de confirmation pour les factures avec paiements
        const modalBody = document.querySelector('#deleteModal .modal-body');
        if (modalBody) {
            modalBody.innerHTML = `
                <p>Êtes-vous sûr de vouloir supprimer cette facture ?</p>
                <p class="text-warning">
                    <i class="bi bi-exclamation-triangle"></i>
                    <strong>Attention :</strong> Cette facture a un montant payé de ${formatCurrency(invoice.paid_amount)}.
                    Ce montant sera automatiquement rétabli dans votre chiffre d'affaires.
                </p>
            `;
        }
    } else {
        // Message standard pour les factures sans paiement
        const modalBody = document.querySelector('#deleteModal .modal-body');
        if (modalBody) {
            modalBody.innerHTML = '<p>Êtes-vous sûr de vouloir supprimer cette facture ?</p>';
        }
    }
    
    const modal = new bootstrap.Modal(document.getElementById('deleteModal'));
    modal.show();
}

// Confirmer la suppression
async function confirmDelete() {
    if (!currentInvoiceId) return;
    
    try {
        await axios.delete(`/api/supplier-invoices/${currentInvoiceId}`);
        
        const modal = bootstrap.Modal.getInstance(document.getElementById('deleteModal'));
        modal.hide();
        
        showSuccess('Facture supprimée avec succès');
        loadInvoices();
        loadSummaryStats();
        
        currentInvoiceId = null;
        
    } catch (error) {
        console.error('Erreur lors de la suppression:', error);
        showError(error.response?.data?.detail || 'Erreur lors de la suppression');
    }
}

// Ouvrir la modal de paiement
async function openPaymentModal(invoiceId) {
    try {
        const response = await axios.get(`/api/supplier-invoices/${invoiceId}`);
        const invoice = response.data;
        
        currentInvoiceId = invoiceId;
        
        // Remplir les informations de la facture
        const infoDiv = document.getElementById('paymentInvoiceInfo');
        infoDiv.innerHTML = `
            <div class="d-flex justify-content-between">
                <span><strong>Facture :</strong> ${escapeHtml(invoice.invoice_number)}</span>
                <span><strong>Fournisseur :</strong> ${escapeHtml(invoice.supplier_name)}</span>
            </div>
            <div class="d-flex justify-content-between mt-2">
                <span><strong>Total facture :</strong> ${formatCurrency(invoice.amount || invoice.total)}</span>
                <span><strong>Déjà payé :</strong> ${formatCurrency(invoice.paid_amount)}</span>
            </div>
            <div class="d-flex justify-content-between mt-2">
                <span><strong>Restant à payer :</strong></span>
                <span class="text-danger"><strong>${formatCurrency(invoice.remaining_amount)}</strong></span>
            </div>
        `;
        
        // Préremplir le montant avec le restant dû (entier)
        const remainingInt = Math.floor(invoice.remaining_amount || 0);
        const paymentAmountEl = document.getElementById('paymentAmount');
        paymentAmountEl.step = '1';
        paymentAmountEl.value = remainingInt;
        paymentAmountEl.max = remainingInt;
        paymentAmountEl.addEventListener('input', () => {
            const raw = String(paymentAmountEl.value).replace(',', '.');
            const n = Math.floor(Number(raw));
            paymentAmountEl.value = Number.isFinite(n) && n >= 0 ? String(n) : '';
        });
        
        // Réinitialiser le formulaire
        document.getElementById('paymentMethodSelect').value = '';
        document.getElementById('paymentReference').value = '';
        document.getElementById('paymentNotes').value = '';
        
        const modal = new bootstrap.Modal(document.getElementById('paymentModal'));
        modal.show();
        
    } catch (error) {
        console.error('Erreur lors de l\'ouverture de la modal de paiement:', error);
        showError('Erreur lors de l\'ouverture de la modal de paiement');
    }
}

// Gérer la soumission du formulaire de paiement
async function handlePaymentFormSubmit(e) {
    e.preventDefault();
    
    const amount = Math.round(parseFloat(document.getElementById('paymentAmount').value));
    const paymentDate = document.getElementById('paymentDate').value;
    const paymentMethod = document.getElementById('paymentMethodSelect').value;
    const reference = document.getElementById('paymentReference').value;
    const notes = document.getElementById('paymentNotes').value;
    
    if (!amount || amount <= 0) {
        showError('Veuillez saisir un montant valide');
        return;
    }
    
    if (!paymentDate) {
        showError('Veuillez saisir une date de paiement');
        return;
    }
    
    if (!paymentMethod) {
        showError('Veuillez sélectionner une méthode de paiement');
        return;
    }
    
    try {
        const paymentData = {
            amount: amount,
            payment_date: paymentDate,
            payment_method: paymentMethod,
            reference: reference || null,
            notes: notes || null
        };
        
        await axios.post(`/api/supplier-invoices/${currentInvoiceId}/payments`, paymentData);
        
        const modal = bootstrap.Modal.getInstance(document.getElementById('paymentModal'));
        modal.hide();
        
        showSuccess('Paiement enregistré avec succès');
        
        // Recharger les données
        loadInvoices();
        loadSummaryStats();
        
        // Si la modal de détails est ouverte, la rafraîchir
        const detailsModal = bootstrap.Modal.getInstance(document.getElementById('invoiceDetailsModal'));
        if (detailsModal) {
            viewInvoice(currentInvoiceId);
        }
        
    } catch (error) {
        console.error('Erreur lors de l\'enregistrement du paiement:', error);
        showError(error.response?.data?.detail || 'Erreur lors de l\'enregistrement du paiement');
    }
}

// Gestion des filtres et recherche
function handleSearch() {
    currentFilters.search = document.getElementById('searchInput').value;
    currentPage = 1;
    loadInvoices();
}

function handleFilterChange() {
    currentFilters.supplier_id = document.getElementById('supplierFilter').value || null;
    currentFilters.status = document.getElementById('statusFilter').value || null;
    currentPage = 1;
    loadInvoices();
}

function clearFilters() {
    document.getElementById('searchInput').value = '';
    document.getElementById('supplierFilter').value = '';
    document.getElementById('statusFilter').value = '';
    
    currentFilters = { search: '', supplier_id: null, status: null };
    currentPage = 1;
    loadInvoices();
}

// Pagination
function updatePagination(total) {
    const totalPages = Math.ceil(total / itemsPerPage);
    const paginationContainer = document.getElementById('pagination');
    
    paginationContainer.innerHTML = '';
    
    if (totalPages <= 1) return;
    
    // Bouton précédent
    const prevLi = document.createElement('li');
    prevLi.className = `page-item ${currentPage === 1 ? 'disabled' : ''}`;
    prevLi.innerHTML = `<a class="page-link" href="#" onclick="changePage(${currentPage - 1})">Précédent</a>`;
    paginationContainer.appendChild(prevLi);
    
    // Pages
    const startPage = Math.max(1, currentPage - 2);
    const endPage = Math.min(totalPages, currentPage + 2);
    
    for (let i = startPage; i <= endPage; i++) {
        const li = document.createElement('li');
        li.className = `page-item ${i === currentPage ? 'active' : ''}`;
        li.innerHTML = `<a class="page-link" href="#" onclick="changePage(${i})">${i}</a>`;
        paginationContainer.appendChild(li);
    }
    
    // Bouton suivant
    const nextLi = document.createElement('li');
    nextLi.className = `page-item ${currentPage === totalPages ? 'disabled' : ''}`;
    nextLi.innerHTML = `<a class="page-link" href="#" onclick="changePage(${currentPage + 1})">Suivant</a>`;
    paginationContainer.appendChild(nextLi);
}

function changePage(page) {
    if (page < 1) return;
    currentPage = page;
    loadInvoices();
}

// Fonctions utilitaires
function getStatusBadgeClass(status) {
    const classes = {
        pending: 'bg-warning text-dark',
        partial: 'bg-info',
        paid: 'bg-success',
        overdue: 'bg-danger'
    };
    return classes[status] || 'bg-secondary';
}

function getStatusLabel(status) {
    const labels = {
        pending: 'En attente',
        partial: 'Partiellement payé',
        paid: 'Payé',
        overdue: 'En retard'
    };
    return labels[status] || status;
}

function formatCurrency(amount) {
    return new Intl.NumberFormat('fr-FR', {
        style: 'currency',
        currency: 'XOF',
        minimumFractionDigits: 0,
        maximumFractionDigits: 0
    }).format(Math.round(amount || 0));
}

function formatDateTime(dateString) {
    if (!dateString) return '-';
    const date = new Date(dateString);
    return date.toLocaleDateString('fr-FR', {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
    });
}

function escapeHtml(text) {
    const map = {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#039;'
    };
    return String(text || '').replace(/[&<>"']/g, m => map[m]);
}

function showLoading() {
    document.getElementById('invoicesTableBody').innerHTML = `
        <tr>
            <td colspan="9" class="text-center py-4">
                <div class="spinner-border" role="status">
                    <span class="visually-hidden">Chargement...</span>
                </div>
            </td>
        </tr>
    `;
}

function hideLoading() {
    // Le loading sera masqué par displayInvoices()
}

function showError(message) {
    // Utiliser le système de notification existant
    if (typeof showNotification === 'function') {
        showNotification(message, 'error');
    } else {
        alert('Erreur: ' + message);
    }
}

function showSuccess(message) {
    // Utiliser le système de notification existant
    if (typeof showNotification === 'function') {
        showNotification(message, 'success');
    } else {
        alert('Succès: ' + message);
    }
}

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
