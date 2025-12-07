// Gestion des ventes quotidiennes
class DailySalesManager {
    constructor() {
        this.currentPage = 1;
        this.pageSize = 20;
        this.currentFilters = {};
        this.editingSaleId = null;
        this.deleteSaleId = null;
        this.selectedDate = new Date().toISOString().split('T')[0]; // Aujourd'hui par défaut
        this.selectedProducts = []; // Liste des produits sélectionnés
        this.currentProduct = null; // Produit actuellement sélectionné pour ajout
        
        this.init();
    }

    init() {
        // Masquer la carte "Vente Moyenne" pour les non-admin
        try {
            if (window.authManager && !window.authManager.isAdmin()) {
                const avgEl = document.getElementById('averageSale');
                const avgCardCol = avgEl ? avgEl.closest('.col-md-3') : null;
                if (avgCardCol) avgCardCol.style.display = 'none';
            }
        } catch (e) { /* ignore */ }

        this.setupEventListeners();
        this.setDefaultDate();
        this.updateDateDisplay();
        this.loadSales();
        this.loadStats();
    }

    setupEventListeners() {
        // Filtres
        document.getElementById('applyFilters').addEventListener('click', () => this.applyFilters());
        document.getElementById('clearFilters').addEventListener('click', () => this.clearFilters());
        
        // Navigation par jour
        document.getElementById('prevDayBtn').addEventListener('click', () => this.previousDay());
        document.getElementById('nextDayBtn').addEventListener('click', () => this.nextDay());
        document.getElementById('todayBtn').addEventListener('click', () => this.goToToday());
        document.getElementById('selectedDate').addEventListener('change', (e) => {
            this.selectedDate = e.target.value;
            this.updateDateDisplay();
            this.loadSales();
            this.loadStats();
        });
        
        // Modal d'ajout/modification
        document.getElementById('saveSaleBtn').addEventListener('click', () => this.saveSale());
        
        // Recherche de clients
        document.getElementById('searchClientBtn').addEventListener('click', () => this.searchClients());
        document.getElementById('clientSearch').addEventListener('input', (e) => {
            if (e.target.value.length >= 2) {
                this.searchClients();
            }
        });
        
        // Ajout de nouveau client
        document.getElementById('addNewClientBtn').addEventListener('click', () => this.showAddClientModal());
        document.getElementById('saveQuickClientBtn').addEventListener('click', () => this.saveQuickClient());
        
        // Recherche de produits
        document.getElementById('searchProductBtn').addEventListener('click', () => this.searchProducts());
        document.getElementById('productSearch').addEventListener('input', (e) => {
            if (e.target.value.length >= 2) {
                this.searchProducts();
            }
        });
        
        // Ajout de produit
        document.getElementById('addProductBtn').addEventListener('click', () => this.addCurrentProduct());
        
        // Sélection de variante
        document.getElementById('variantSelect').addEventListener('change', (e) => {
            if (e.target.value) {
                this.selectVariant(e.target.value);
            }
        });
        
        // Calcul automatique du total général
        document.addEventListener('input', (e) => {
            if (e.target.classList.contains('product-quantity') || e.target.classList.contains('product-price')) {
                this.updateProductTotal(e.target);
                this.calculateGrandTotal();
            }
        });
        
        // Sélection de client
        document.getElementById('clientDropdown').addEventListener('click', (e) => {
            const clientItem = e.target.closest('.client-item');
            if (clientItem) {
                const clientId = clientItem.dataset.clientId;
                const clientName = clientItem.dataset.clientName;
                
                // Remplir le champ de recherche avec le nom du client
                document.getElementById('clientSearch').value = clientName;
                document.querySelector('input[name="clientId"]')?.remove();
                
                const hiddenInput = document.createElement('input');
                hiddenInput.type = 'hidden';
                hiddenInput.name = 'clientId';
                hiddenInput.value = clientId;
                document.getElementById('saleForm').appendChild(hiddenInput);
                
                document.getElementById('clientDropdown').style.display = 'none';
            }
        });
        
        // Sélection de produit
        document.getElementById('productDropdown').addEventListener('click', (e) => {
            const productItem = e.target.closest('.product-item');
            if (productItem) {
                const productId = productItem.dataset.productId;
                const productName = productItem.dataset.productName;
                const productPrice = productItem.dataset.productPrice;
                const hasVariants = productItem.dataset.hasVariants === 'true';
                
                // Stocker le produit actuellement sélectionné
                this.currentProduct = {
                    id: productId,
                    name: productName,
                    price: parseFloat(productPrice),
                    hasVariants: hasVariants
                };
                
                // Remplir le champ de recherche avec le nom du produit
                document.getElementById('productSearch').value = productName;
                
                document.getElementById('productDropdown').style.display = 'none';
                
                // Si le produit a des variantes, charger les variantes disponibles
                if (hasVariants) {
                    this.loadProductVariants(productId);
                } else {
                    // Cacher le champ de sélection de variante
                    document.getElementById('variantSelectionField').style.display = 'none';
                }
            }
        });
        
        // Modal de suppression
        document.getElementById('confirmDeleteBtn').addEventListener('click', () => this.confirmDelete());
        
        // Fermeture des dropdowns
        document.addEventListener('click', (e) => {
            if (!e.target.closest('.input-group')) {
                document.getElementById('clientDropdown').style.display = 'none';
                document.getElementById('productDropdown').style.display = 'none';
            }
        });
    }

    getInvoiceStatusBadge(status) {
        const s = String(status || '').toLowerCase();
        if (!s) return '-';
        const map = {
            'payée': ['bg-success','Payée'],
            'paid': ['bg-success','Payée'],
            'partiellement payée': ['bg-warning text-dark','Partielle'],
            'overdue': ['bg-danger','En retard'],
            'en attente': ['bg-secondary','En attente'],
            'sent': ['bg-secondary','Envoyée'],
            'draft': ['bg-secondary','Brouillon']
        };
        const conf = map[s] || ['bg-secondary', status];
        return `<span class="badge ${conf[0]}">${conf[1]}</span>`;
    }

    setDefaultDate() {
        const today = new Date().toISOString().split('T')[0];
        document.getElementById('saleDate').value = today;
        document.getElementById('selectedDate').value = this.selectedDate;
    }

    async loadProductVariants(productId) {
        try {
            const response = await axios.get(`/api/products/id/${productId}/variants/available`);
            const data = response.data;
            
            if (data.available_variants && data.available_variants.length > 0) {
                this.populateVariantSelect(data.available_variants);
            } else {
                this.showAlert('Aucune variante disponible pour ce produit', 'warning');
                document.getElementById('variantSelectionField').style.display = 'none';
            }
        } catch (error) {
            console.error('Error loading variants:', error);
            this.showAlert('Erreur lors du chargement des variantes', 'danger');
            document.getElementById('variantSelectionField').style.display = 'none';
        }
    }

    populateVariantSelect(variants) {
        const select = document.getElementById('variantSelect');
        select.innerHTML = '<option value="">Sélectionner une variante...</option>';
        
        variants.forEach((variant, index) => {
            const option = document.createElement('option');
            option.value = variant.variant_id;
            option.dataset.imei = variant.imei_serial;
            option.dataset.barcode = variant.barcode || '';
            option.dataset.condition = variant.condition || '';
            
            // Créer le texte d'affichage
            let displayText = `Variante ${index + 1}`;
            if (variant.imei_serial) {
                displayText += ` - IMEI: ${variant.imei_serial}`;
            }
            if (variant.barcode) {
                displayText += ` - Code: ${variant.barcode}`;
            }
            if (variant.condition) {
                displayText += ` - ${variant.condition}`;
            }
            if (variant.attributes && variant.attributes.length > 0) {
                const attrs = variant.attributes.map(attr => `${attr.attribute_name}: ${attr.attribute_value}`).join(', ');
                displayText += ` (${attrs})`;
            }
            
            option.textContent = displayText;
            select.appendChild(option);
        });
        
        // Afficher le champ de sélection
        document.getElementById('variantSelectionField').style.display = 'block';
    }

    selectVariant(variantId) {
        const select = document.getElementById('variantSelect');
        const selectedOption = select.options[select.selectedIndex];
        
        if (selectedOption && selectedOption.value) {
            // Mettre à jour le produit actuel avec les infos de la variante
            this.currentProduct.variantId = parseInt(variantId);
            this.currentProduct.imei = selectedOption.dataset.imei;
            this.currentProduct.barcode = selectedOption.dataset.barcode;
            this.currentProduct.condition = selectedOption.dataset.condition;
            
            // Ajouter automatiquement le produit à la liste
            this.addCurrentProduct();
        }
    }

    showVariantSelectionModal(variants) {
        // Créer le modal de sélection de variantes
        const modalHtml = `
            <div class="modal fade" id="variantSelectionModal" tabindex="-1">
                <div class="modal-dialog modal-lg">
                    <div class="modal-content">
                        <div class="modal-header">
                            <h5 class="modal-title">Sélectionner une Variante</h5>
                            <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                        </div>
                        <div class="modal-body">
                            <p class="text-muted">Ce produit a plusieurs variantes. Veuillez sélectionner celle que vous souhaitez vendre :</p>
                            <div class="list-group" id="variantsList">
                                ${variants.map((variant, index) => `
                                    <div class="list-group-item list-group-item-action variant-item" 
                                         data-variant-id="${variant.variant_id}" 
                                         data-imei="${variant.imei_serial}"
                                         data-barcode="${variant.barcode || ''}"
                                         data-condition="${variant.condition || ''}"
                                         style="cursor: pointer;">
                                        <div class="d-flex w-100 justify-content-between">
                                            <h6 class="mb-1">Variante ${index + 1}</h6>
                                            <small>IMEI: ${variant.imei_serial}</small>
                                        </div>
                                        <p class="mb-1">
                                            ${variant.barcode ? `<strong>Code-barres:</strong> ${variant.barcode}<br>` : ''}
                                            ${variant.condition ? `<strong>Condition:</strong> ${variant.condition}<br>` : ''}
                                            ${variant.attributes.length > 0 ? '<strong>Attributs:</strong><br>' : ''}
                                            ${variant.attributes.map(attr => `• ${attr.attribute_name}: ${attr.attribute_value}`).join('<br>')}
                                        </p>
                                    </div>
                                `).join('')}
                            </div>
                        </div>
                        <div class="modal-footer">
                            <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Annuler</button>
                        </div>
                    </div>
                </div>
            </div>
        `;
        
        // Supprimer l'ancien modal s'il existe
        const existingModal = document.getElementById('variantSelectionModal');
        if (existingModal) {
            existingModal.remove();
        }
        
        // Ajouter le nouveau modal
        document.body.insertAdjacentHTML('beforeend', modalHtml);
        
        // Ajouter les événements
        document.querySelectorAll('.variant-item').forEach(item => {
            item.addEventListener('click', () => {
                const variantId = item.dataset.variantId;
                const imei = item.dataset.imei;
                const barcode = item.dataset.barcode;
                const condition = item.dataset.condition;
                
                // Mettre à jour le produit actuel avec les infos de la variante
                this.currentProduct.variantId = variantId;
                this.currentProduct.imei = imei;
                this.currentProduct.barcode = barcode;
                this.currentProduct.condition = condition;
                
                // Fermer le modal
                bootstrap.Modal.getInstance(document.getElementById('variantSelectionModal')).hide();
                
                // Ajouter le produit à la liste
                this.addCurrentProduct();
            });
        });
        
        // Ouvrir le modal
        new bootstrap.Modal(document.getElementById('variantSelectionModal')).show();
    }

    addCurrentProduct() {
        if (!this.currentProduct) {
            this.showAlert('Veuillez d\'abord sélectionner un produit', 'warning');
            return;
        }

        // Vérifier si le produit n'est pas déjà ajouté (avec la même variante si applicable)
        const existingProduct = this.selectedProducts.find(p => {
            if (p.id === this.currentProduct.id) {
                if (p.variantId && this.currentProduct.variantId) {
                    return p.variantId === this.currentProduct.variantId;
                } else if (!p.variantId && !this.currentProduct.variantId) {
                    return true;
                }
            }
            return false;
        });
        
        if (existingProduct) {
            this.showAlert('Cette variante est déjà dans la liste', 'warning');
            return;
        }

        // Ajouter le produit à la liste
        const productToAdd = {
            ...this.currentProduct,
            quantity: 1,
            total: this.currentProduct.price
        };
        
        this.selectedProducts.push(productToAdd);
        this.displaySelectedProducts();
        this.calculateGrandTotal();
        
        // Vider la sélection actuelle
        this.currentProduct = null;
        document.getElementById('productSearch').value = '';
    }

    displaySelectedProducts() {
        const tbody = document.getElementById('selectedProductsBody');
        const noProductsMessage = document.getElementById('noProductsMessage');
        
        if (this.selectedProducts.length === 0) {
            tbody.innerHTML = '';
            noProductsMessage.style.display = 'block';
            return;
        }
        
        noProductsMessage.style.display = 'none';
        tbody.innerHTML = '';
        
        this.selectedProducts.forEach((product, index) => {
            const row = document.createElement('tr');
            
            // Informations du produit avec variante si applicable
            let productInfo = this.escapeHtml(product.name);
            if (product.variantId) {
                productInfo += `<br><small class="text-muted">`;
                if (product.imei) productInfo += `IMEI: ${this.escapeHtml(product.imei)}<br>`;
                if (product.barcode) productInfo += `Code-barres: ${this.escapeHtml(product.barcode)}<br>`;
                if (product.condition) productInfo += `Condition: ${this.escapeHtml(product.condition)}`;
                productInfo += `</small>`;
            }
            
            row.innerHTML = `
                <td>${productInfo}</td>
                <td>
                    <input type="number" class="form-control form-control-sm product-quantity" 
                           value="${product.quantity}" min="1" data-index="${index}">
                </td>
                <td>
                    <input type="number" class="form-control form-control-sm product-price" 
                           value="${product.price}" step="0.01" min="0" data-index="${index}">
                </td>
                <td class="product-total">${this.formatCurrency(product.total)}</td>
                <td>
                    <button class="btn btn-sm btn-outline-danger" onclick="dailySalesManager.removeProduct(${index})">
                        <i class="bi bi-trash"></i>
                    </button>
                </td>
            `;
            tbody.appendChild(row);
        });
    }

    updateProductTotal(input) {
        const index = parseInt(input.dataset.index);
        const product = this.selectedProducts[index];
        
        if (input.classList.contains('product-quantity')) {
            product.quantity = parseInt(input.value) || 1;
        } else if (input.classList.contains('product-price')) {
            product.price = parseFloat(input.value) || 0;
        }
        
        product.total = product.quantity * product.price;
        
        // Mettre à jour l'affichage du total de cette ligne
        const row = input.closest('tr');
        const totalCell = row.querySelector('.product-total');
        totalCell.textContent = this.formatCurrency(product.total);
    }

    calculateGrandTotal() {
        const grandTotal = this.selectedProducts.reduce((sum, product) => sum + product.total, 0);
        document.getElementById('totalAmount').value = grandTotal.toFixed(2);
    }

    removeProduct(index) {
        this.selectedProducts.splice(index, 1);
        this.displaySelectedProducts();
        this.calculateGrandTotal();
    }

    async loadSales() {
        try {
            const params = new URLSearchParams({
                skip: (this.currentPage - 1) * this.pageSize,
                limit: this.pageSize,
                start_date: this.selectedDate,
                end_date: this.selectedDate,
                ...this.currentFilters
            });

            const response = await axios.get(`/api/daily-sales/?${params}`);
            this.displaySales(response.data);
        } catch (error) {
            this.showAlert('Erreur lors du chargement des ventes', 'danger');
            console.error('Error loading sales:', error);
        }
    }

    displaySales(sales) {
        const tbody = document.getElementById('salesTableBody');
        tbody.innerHTML = '';

        if (sales.length === 0) {
            const today = new Date().toISOString().split('T')[0];
            const isToday = this.selectedDate === today;
            const message = isToday ? 
                'Aucune vente aujourd\'hui' : 
                `Aucune vente le ${new Date(this.selectedDate).toLocaleDateString('fr-FR')}`;
            
            tbody.innerHTML = `
                <tr>
                    <td colspan="10" class="text-center text-muted py-4">
                        <i class="bi bi-inbox me-2"></i>${message}
                    </td>
                </tr>
            `;
            return;
        }

        // Grouper par invoice_id pour éviter les doublons d'affichage de la même facture
        const grouped = [];
        const byInvoice = new Map();
        sales.forEach(sale => {
            if (sale.invoice_id) {
                const key = String(sale.invoice_id);
                if (!byInvoice.has(key)) {
                    byInvoice.set(key, { ...sale });
                } else {
                    const agg = byInvoice.get(key);
                    agg.quantity = (Number(agg.quantity)||0) + (Number(sale.quantity)||0);
                    agg.total_amount = (Number(agg.total_amount)||0) + (Number(sale.total_amount)||0);
                    byInvoice.set(key, agg);
                }
            } else {
                grouped.push(sale);
            }
        });
        byInvoice.forEach(v => grouped.push(v));
        const rows = grouped;

        rows.forEach(sale => {
            const row = document.createElement('tr');
            row.innerHTML = `
                <td>${this.formatDate(sale.sale_date)}</td>
                <td>${this.escapeHtml(sale.client_name)}</td>
                <td>${this.escapeHtml(sale.product_name)}</td>
                <td>${sale.quantity}</td>
                <td>${this.formatCurrency(sale.unit_price)}</td>
                <td>${this.formatCurrency(sale.total_amount)}</td>
                <td>${this.getPaymentMethodBadge(sale.payment_method)}</td>
                <td>${this.getInvoiceStatusBadge(sale.invoice_status)}</td>
                <td>${sale.invoice_id ? `<a href="/invoices/print/${sale.invoice_id}" target="_blank" class="btn btn-sm btn-outline-primary">#${sale.invoice_id}</a>` : '-'}</td>
                <td>
                    <div class="btn-group btn-group-sm">
                        <button class="btn btn-outline-primary" onclick="dailySalesManager.editSale(${sale.sale_id})" title="Modifier">
                            <i class="bi bi-pencil"></i>
                        </button>
                        <button class="btn btn-outline-danger" onclick="dailySalesManager.deleteSale(${sale.sale_id})" title="Supprimer">
                            <i class="bi bi-trash"></i>
                        </button>
                    </div>
                </td>
            `;
            tbody.appendChild(row);
        });
    }

    getPaymentMethodBadge(method) {
        const badges = {
            'espece': '<span class="badge bg-success">Espèces</span>',
            'mobile': '<span class="badge bg-info">Mobile Money</span>',
            'virement': '<span class="badge bg-primary">Virement</span>',
            'cheque': '<span class="badge bg-warning">Chèque</span>'
        };
        return badges[method] || method;
    }

    async loadStats() {
        try {
            const params = new URLSearchParams({
                start_date: this.selectedDate,
                end_date: this.selectedDate,
                ...this.currentFilters
            });
            const response = await axios.get(`/api/daily-sales/stats/summary?${params}`);
            const stats = response.data;
            
            document.getElementById('totalSales').textContent = stats.total_sales;
            document.getElementById('totalAmount').textContent = this.formatCurrency(stats.total_amount);
            document.getElementById('averageSale').textContent = this.formatCurrency(stats.average_sale);
            document.getElementById('directSales').textContent = stats.direct_sales;
        } catch (error) {
            console.error('Error loading stats:', error);
        }
    }

    applyFilters() {
        this.currentFilters = {
            search: document.getElementById('searchInput').value || undefined,
            payment_method: document.getElementById('paymentMethodFilter').value || undefined
        };
        
        // Supprimer les valeurs undefined
        Object.keys(this.currentFilters).forEach(key => {
            if (this.currentFilters[key] === undefined) {
                delete this.currentFilters[key];
            }
        });
        
        this.currentPage = 1;
        this.loadSales();
        this.loadStats();
    }

    clearFilters() {
        document.getElementById('searchInput').value = '';
        document.getElementById('paymentMethodFilter').value = '';
        
        this.currentFilters = {};
        this.currentPage = 1;
        this.loadSales();
        this.loadStats();
    }

    // Navigation par jour
    previousDay() {
        const date = new Date(this.selectedDate);
        date.setDate(date.getDate() - 1);
        this.selectedDate = date.toISOString().split('T')[0];
        document.getElementById('selectedDate').value = this.selectedDate;
        this.updateDateDisplay();
        this.loadSales();
        this.loadStats();
    }

    nextDay() {
        const date = new Date(this.selectedDate);
        date.setDate(date.getDate() + 1);
        this.selectedDate = date.toISOString().split('T')[0];
        document.getElementById('selectedDate').value = this.selectedDate;
        this.updateDateDisplay();
        this.loadSales();
        this.loadStats();
    }

    goToToday() {
        this.selectedDate = new Date().toISOString().split('T')[0];
        document.getElementById('selectedDate').value = this.selectedDate;
        this.updateDateDisplay();
        this.loadSales();
        this.loadStats();
    }

    updateDateDisplay() {
        const dateDisplay = document.getElementById('selectedDateDisplay');
        const today = new Date().toISOString().split('T')[0];
        const yesterday = new Date();
        yesterday.setDate(yesterday.getDate() - 1);
        const yesterdayStr = yesterday.toISOString().split('T')[0];
        
        if (this.selectedDate === today) {
            dateDisplay.textContent = 'Aujourd\'hui';
        } else if (this.selectedDate === yesterdayStr) {
            dateDisplay.textContent = 'Hier';
        } else {
            const date = new Date(this.selectedDate);
            const options = { 
                weekday: 'long', 
                year: 'numeric', 
                month: 'long', 
                day: 'numeric' 
            };
            dateDisplay.textContent = date.toLocaleDateString('fr-FR', options);
        }
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

    showAddClientModal() {
        // Fermer le dropdown de recherche client
        document.getElementById('clientDropdown').style.display = 'none';
        
        // Vider le formulaire
        document.getElementById('quickClientForm').reset();
        
        // Ouvrir le modal
        new bootstrap.Modal(document.getElementById('addClientModal')).show();
    }

    async saveQuickClient() {
        try {
            const clientData = {
                name: document.getElementById('quickClientName').value,
                phone: document.getElementById('quickClientPhone').value || null,
                email: null,
                address: null
            };

            if (!clientData.name.trim()) {
                this.showAlert('Le nom du client est obligatoire', 'warning');
                return;
            }

            const response = await axios.post('/api/clients/', clientData);
            const newClient = response.data;
            
            // Fermer le modal d'ajout de client
            bootstrap.Modal.getInstance(document.getElementById('addClientModal')).hide();
            
            // Remplir automatiquement le champ de recherche client
            document.getElementById('clientSearch').value = newClient.name;
            document.querySelector('input[name="clientId"]')?.remove();
            
            const hiddenInput = document.createElement('input');
            hiddenInput.type = 'hidden';
            hiddenInput.name = 'clientId';
            hiddenInput.value = newClient.client_id;
            document.getElementById('saleForm').appendChild(hiddenInput);
            
            this.showAlert('Client ajouté avec succès', 'success');
        } catch (error) {
            this.showAlert('Erreur lors de l\'ajout du client', 'danger');
            console.error('Error creating client:', error);
        }
    }

    async searchProducts() {
        const query = document.getElementById('productSearch').value;
        if (query.length < 2) return;

        try {
            const response = await axios.get(`/api/products/?search=${encodeURIComponent(query)}&limit=10`);
            const products = response.data || [];
            
            const dropdown = document.getElementById('productDropdown');
            dropdown.innerHTML = '';
            
            if (products.length === 0) {
                dropdown.innerHTML = '<div class="dropdown-item text-muted">Aucun produit trouvé</div>';
            } else {
                products.forEach(product => {
                    const item = document.createElement('div');
                    item.className = 'dropdown-item product-item';
                    item.dataset.productId = product.product_id;
                    item.dataset.productName = product.name;
                    item.dataset.productPrice = product.price;
                    item.dataset.hasVariants = product.has_variants || false;
                    
                    let stockInfo = `Stock: ${product.quantity}`;
                    if (product.has_variants && product.variants_available) {
                        stockInfo += ` • Variantes: ${product.variants_available}`;
                    }
                    
                    item.innerHTML = `
                        <div class="fw-semibold">${this.escapeHtml(product.name)}</div>
                        <small class="text-muted">${stockInfo} • Prix: ${this.formatCurrency(product.price)}</small>
                        ${product.has_variants ? '<small class="text-info d-block"><i class="bi bi-info-circle me-1"></i>Ce produit a des variantes</small>' : ''}
                    `;
                    dropdown.appendChild(item);
                });
            }
            
            dropdown.style.display = 'block';
        } catch (error) {
            console.error('Error searching products:', error);
        }
    }

    newSale() {
        this.editingSaleId = null;
        this.selectedProducts = [];
        this.currentProduct = null;
        document.getElementById('modalTitle').textContent = 'Nouvelle Vente';
        document.getElementById('saleForm').reset();
        this.setDefaultDate();
        document.getElementById('paymentMethod').value = 'espece';
        document.getElementById('variantSelectionField').style.display = 'none';
        document.getElementById('variantSelect').innerHTML = '<option value="">Chargement des variantes...</option>';
        this.displaySelectedProducts();
        this.calculateGrandTotal();
    }

    async editSale(saleId) {
        try {
            const response = await axios.get(`/api/daily-sales/${saleId}`);
            const sale = response.data;
            
            this.editingSaleId = saleId;
            document.getElementById('modalTitle').textContent = 'Modifier la Vente';
            
            document.getElementById('saleId').value = sale.sale_id;
            document.getElementById('clientSearch').value = sale.client_name;
            document.getElementById('productSearch').value = sale.product_name;
            document.getElementById('quantity').value = sale.quantity;
            document.getElementById('unitPrice').value = sale.unit_price;
            document.getElementById('totalAmount').value = sale.total_amount;
            document.getElementById('saleDate').value = sale.sale_date;
            document.getElementById('paymentMethod').value = sale.payment_method;
            document.getElementById('notes').value = sale.notes || '';
            
            // Ajouter les IDs si disponibles
            if (sale.client_id) {
                document.querySelector('input[name="clientId"]')?.remove();
                const hiddenInput = document.createElement('input');
                hiddenInput.type = 'hidden';
                hiddenInput.name = 'clientId';
                hiddenInput.value = sale.client_id;
                document.getElementById('saleForm').appendChild(hiddenInput);
            }
            
            if (sale.product_id) {
                document.querySelector('input[name="productId"]')?.remove();
                const hiddenInput = document.createElement('input');
                hiddenInput.type = 'hidden';
                hiddenInput.name = 'productId';
                hiddenInput.value = sale.product_id;
                document.getElementById('saleForm').appendChild(hiddenInput);
            }
            
            new bootstrap.Modal(document.getElementById('addSaleModal')).show();
        } catch (error) {
            this.showAlert('Erreur lors du chargement de la vente', 'danger');
            console.error('Error loading sale:', error);
        }
    }

    async saveSale() {
        try {
            // Vérifier qu'il y a au moins un produit
            if (this.selectedProducts.length === 0) {
                this.showAlert('Veuillez ajouter au moins un produit', 'warning');
                return;
            }

            const clientId = document.querySelector('input[name="clientId"]')?.value || null;
            const clientName = document.getElementById('clientSearch').value;
            const saleDate = document.getElementById('saleDate').value;
            const paymentMethod = document.getElementById('paymentMethod').value;
            const notes = document.getElementById('notes').value;

            // Créer une vente pour chaque produit
            const salesToCreate = this.selectedProducts.map(product => ({
                client_id: clientId,
                client_name: clientName,
                product_id: product.id,
                product_name: product.name,
                variant_id: product.variantId || null,
                variant_imei: product.imei || null,
                variant_barcode: product.barcode || null,
                variant_condition: product.condition || null,
                quantity: product.quantity,
                unit_price: product.price,
                total_amount: product.total,
                sale_date: saleDate,
                payment_method: paymentMethod,
                notes: notes
            }));

            if (this.editingSaleId) {
                // Pour l'édition, on supprime l'ancienne vente et on recrée
                await axios.delete(`/api/daily-sales/${this.editingSaleId}`);
            }

            // Créer toutes les ventes
            for (const saleData of salesToCreate) {
                await axios.post('/api/daily-sales/', saleData);
            }

            this.showAlert('Vente(s) créée(s) avec succès', 'success');
            bootstrap.Modal.getInstance(document.getElementById('addSaleModal')).hide();
            this.loadSales();
            this.loadStats();
        } catch (error) {
            this.showAlert('Erreur lors de la sauvegarde', 'danger');
            console.error('Error saving sale:', error);
        }
    }

    deleteSale(saleId) {
        this.deleteSaleId = saleId;
        new bootstrap.Modal(document.getElementById('deleteModal')).show();
    }

    async confirmDelete() {
        try {
            await axios.delete(`/api/daily-sales/${this.deleteSaleId}`);
            this.showAlert('Vente supprimée avec succès', 'success');
            bootstrap.Modal.getInstance(document.getElementById('deleteModal')).hide();
            this.loadSales();
            this.loadStats();
        } catch (error) {
            this.showAlert('Erreur lors de la suppression', 'danger');
            console.error('Error deleting sale:', error);
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

    formatCurrency(amount) {
        return new Intl.NumberFormat('fr-FR', {
            style: 'currency',
            currency: 'XOF',
            minimumFractionDigits: 0,
            maximumFractionDigits: 0
        }).format(amount);
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
}

// Initialiser le gestionnaire
let dailySalesManager;

document.addEventListener('DOMContentLoaded', function() {
    dailySalesManager = new DailySalesManager();
    
    // Gérer l'ouverture du modal pour nouvelle vente
    document.getElementById('addSaleModal').addEventListener('show.bs.modal', function() {
        dailySalesManager.newSale();
    });
});
