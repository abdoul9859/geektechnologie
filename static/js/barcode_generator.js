// Générateur de codes-barres
let products = [];
let selectedProducts = [];
let filteredProducts = [];
// ID du produit actuellement en aperçu (pour impression depuis la modal)
let currentPreviewProductId = null;
// Paramètres d'impression d'étiquettes (compatibles petites étiqueteuses)
const LABEL_PRESETS = {
    '58x40': { width: '58mm', height: '40mm' },
    '62x29': { width: '62mm', height: '29mm' },
    '50x30': { width: '50mm', height: '30mm' },
    '40x30': { width: '40mm', height: '30mm' },
    '30x20': { width: '30mm', height: '20mm' },
};
let currentLabelKey = '58x40';

// Initialisation
document.addEventListener('DOMContentLoaded', function() {
    const ready = () => {
        const hasAuthManager = !!window.authManager;
        const hasUser = !!(hasAuthManager && window.authManager.userData && Object.keys(window.authManager.userData).length);
        return hasAuthManager && (window.authManager.isAuthenticatedSync?.() || hasUser);
    };

    const init = () => {
        setupEventListeners();
        loadProducts();
    };

    // Initialiser immédiatement sans délai pour un chargement instantané
    init();
});

// Configuration des écouteurs d'événements
function setupEventListeners() {
    // Recherche et filtres (côté serveur)
    const reload = () => loadProducts();
    const debouncedReload = debounce(reload, 300);
    document.getElementById('searchInput').addEventListener('input', debouncedReload);
    document.getElementById('categoryFilter').addEventListener('change', reload);
    document.getElementById('barcodeFilter').addEventListener('change', reload);

    // Sélection
    document.getElementById('selectAllCheck').addEventListener('change', handleSelectAll);
    document.getElementById('selectAllTableCheck').addEventListener('change', handleSelectAll);

    // Code-barres personnalisé
    document.getElementById('customText').addEventListener('input', updateCustomBarcodePreview);
    document.getElementById('barcodeFormat').addEventListener('change', updateCustomBarcodePreview);

    // Config étiquettes
    const sizeSelect = document.getElementById('labelSize');
    const copiesInput = document.getElementById('copiesPerProduct');
    if (sizeSelect) {
        sizeSelect.addEventListener('change', () => {
            currentLabelKey = sizeSelect.value || '58x40';
        });
    }
    if (copiesInput) {
        copiesInput.addEventListener('input', () => {
            const val = Math.max(1, Math.min(100, parseInt(copiesInput.value || '1')));
            copiesInput.value = String(val);
        });
    }
}

// Charger les produits (avec recherche/filtre côté serveur pour inclure IMEI/séries et nouveaux produits)
async function loadProducts() {
    try {
        showLoading();

        const searchTerm = (document.getElementById('searchInput')?.value || '').trim();
        const category = document.getElementById('categoryFilter')?.value || '';
        const barcodeFilter = document.getElementById('barcodeFilter')?.value || '';

        // Map du filtre code-barres
        let has_barcode;
        if (barcodeFilter === 'with') has_barcode = true;
        else if (barcodeFilter === 'without') has_barcode = false;

        const params = {
            page: 1,
            page_size: 200, // API max per page (avoid 422)
        };
        if (searchTerm) params.search = searchTerm;
        if (category) params.category = category;
        if (has_barcode !== undefined) params.has_barcode = has_barcode;

        const { data } = await axios.get('/api/products/paginated', { params });
        const items = Array.isArray(data) ? data : (data.items || data.products || []);
        // Normaliser: assurer un champ 'id' pour le front
        products = items.map(p => ({ ...p, id: p.id ?? p.product_id }));

        // Générer des codes-barres pour les produits qui n'en ont pas
        products = products.map(product => {
            if (!product.barcode && product.id) {
                product.barcode = generateProductBarcode(product.id);
            }
            return product;
        });

        filteredProducts = [...products];
        populateCategoryFilter();
        displayProducts();
        updateStatistics();
        hideLoading();
    } catch (error) {
        console.error('Erreur:', error);
        showError(error.response?.data?.detail || 'Erreur lors du chargement des produits');
        hideLoading();
    }
}

// Générer un code-barres pour un produit
function generateProductBarcode(productId) {
    // Générer un code-barres EAN13 basé sur l'ID du produit
    const baseCode = String(productId).padStart(12, '0');
    return baseCode;
}

// Remplir le filtre des catégories
function populateCategoryFilter() {
    const categoryFilter = document.getElementById('categoryFilter');
    const categories = [...new Set(products.map(p => p.category).filter(Boolean))];
    
    categoryFilter.innerHTML = '<option value="">Toutes les catégories</option>';
    categories.forEach(category => {
        const option = new Option(category, category);
        categoryFilter.appendChild(option);
    });
}

// Afficher les produits
function displayProducts() {
    const tbody = document.getElementById('productsTableBody');
    tbody.innerHTML = '';

    if (filteredProducts.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="7" class="text-center py-4">
                    <i class="bi bi-box display-4 text-muted"></i>
                    <p class="text-muted mt-2 mb-0">Aucun produit trouvé</p>
                </td>
            </tr>
        `;
        return;
    }

    filteredProducts.forEach(product => {
        const row = createProductRow(product);
        tbody.appendChild(row);
    });

    updateResultsCount();
    updateSelectAllState();
}

// Créer une ligne de produit
function createProductRow(product) {
    const row = document.createElement('tr');
    const isSelected = selectedProducts.includes(product.id);
    const hasBarcode = product.barcode && product.barcode.trim() !== '';
    
    row.innerHTML = `
        <td>
            <input type="checkbox" class="form-check-input product-checkbox" 
                   data-product-id="${product.id}" ${isSelected ? 'checked' : ''}>
        </td>
        <td>
            <div>
                <strong>${escapeHtml(product.name)}</strong>
                ${product.description ? `<br><small class="text-muted">${escapeHtml(product.description)}</small>` : ''}
            </div>
        </td>
        <td>
            ${product.category ? `<span class="badge bg-light text-dark">${escapeHtml(product.category)}</span>` : '-'}
        </td>
        <td>
            <strong>${formatCurrency(product.price || 0)}</strong>
        </td>
        <td>
            ${hasBarcode ? 
                `<code class="text-primary">${escapeHtml(product.barcode)}</code>` : 
                '<span class="text-muted">Généré automatiquement</span>'
            }
        </td>
        <td>
            <div id="barcode-preview-${product.id}" class="barcode-preview">
                ${hasBarcode || product.id ? 
                    `<svg id="barcode-${product.id}"></svg>` : 
                    '<span class="text-muted">N/A</span>'
                }
            </div>
        </td>
        <td>
            <div class="btn-group btn-group-sm">
                <button class="btn btn-outline-primary" onclick="previewBarcode(${product.id})" title="Aperçu">
                    <i class="bi bi-eye"></i>
                </button>
                <button class="btn btn-outline-success" onclick="printSingleBarcode(${product.id})" title="Imprimer">
                    <i class="bi bi-printer"></i>
                </button>
            </div>
        </td>
    `;

    // Ajouter l'écouteur pour la checkbox
    const checkbox = row.querySelector('.product-checkbox');
    checkbox.addEventListener('change', function() {
        handleProductSelection(product.id, this.checked);
    });

    // Générer le code-barres après l'insertion dans le DOM
    setTimeout(() => {
        generateBarcodePreview(product);
    }, 100);

    return row;
}

// Générer l'aperçu du code-barres
function generateBarcodePreview(product) {
    const barcodeElement = document.getElementById(`barcode-${product.id}`);
    if (!barcodeElement) return;

    const barcodeValue = product.barcode || generateProductBarcode(product.id);
    
    try {
        JsBarcode(barcodeElement, barcodeValue, {
            format: "CODE128",
            width: 1.2,
            height: 28,
            displayValue: false,
            margin: 2
        });
    } catch (error) {
        console.error('Erreur génération code-barres:', error);
        barcodeElement.innerHTML = '<span class="text-danger">Erreur</span>';
    }
}

// Filtrer les produits
function filterProducts() {
    // Passage au filtrage/recherche côté serveur pour inclure codes-barres variantes et IMEI/séries
    // On recharge simplement depuis l'API avec les paramètres courants
    loadProducts();
}

// Effacer les filtres
function clearFilters() {
    document.getElementById('searchInput').value = '';
    document.getElementById('categoryFilter').value = '';
    document.getElementById('barcodeFilter').value = '';
    loadProducts();
}

// Gérer la sélection de tous les produits
function handleSelectAll() {
    const selectAllCheck = document.getElementById('selectAllCheck');
    const selectAllTableCheck = document.getElementById('selectAllTableCheck');
    const isChecked = selectAllCheck.checked || selectAllTableCheck.checked;

    // Synchroniser les deux checkboxes
    selectAllCheck.checked = isChecked;
    selectAllTableCheck.checked = isChecked;

    if (isChecked) {
        selectedProducts = filteredProducts.map(p => p.id);
    } else {
        selectedProducts = [];
    }

    // Mettre à jour toutes les checkboxes des produits
    document.querySelectorAll('.product-checkbox').forEach(checkbox => {
        checkbox.checked = isChecked;
    });

    updateStatistics();
    updatePrintButton();
}

// Gérer la sélection d'un produit
function handleProductSelection(productId, isSelected) {
    if (isSelected) {
        if (!selectedProducts.includes(productId)) {
            selectedProducts.push(productId);
        }
    } else {
        selectedProducts = selectedProducts.filter(id => id !== productId);
    }

    updateSelectAllState();
    updateStatistics();
    updatePrintButton();
}

// Mettre à jour l'état de "Tout sélectionner"
function updateSelectAllState() {
    const selectAllCheck = document.getElementById('selectAllCheck');
    const selectAllTableCheck = document.getElementById('selectAllTableCheck');
    const allSelected = filteredProducts.length > 0 && 
                       filteredProducts.every(p => selectedProducts.includes(p.id));

    selectAllCheck.checked = allSelected;
    selectAllTableCheck.checked = allSelected;
}

// Mettre à jour les statistiques
function updateStatistics() {
    const totalProducts = products.length;
    const selectedCount = selectedProducts.length;
    const withBarcodes = products.filter(p => p.barcode && p.barcode.trim() !== '').length;
    const withoutBarcodes = totalProducts - withBarcodes;

    document.getElementById('totalProducts').textContent = totalProducts;
    document.getElementById('selectedCount').textContent = selectedCount;
    document.getElementById('withBarcodes').textContent = withBarcodes;
    document.getElementById('withoutBarcodes').textContent = withoutBarcodes;
}

// Mettre à jour le compteur de résultats
function updateResultsCount() {
    const resultsCount = document.getElementById('resultsCount');
    if (resultsCount) {
        resultsCount.textContent = `${filteredProducts.length} produit${filteredProducts.length !== 1 ? 's' : ''}`;
    }
}

// Mettre à jour le bouton d'impression
function updatePrintButton() {
    const printBtn = document.getElementById('printBtn');
    printBtn.disabled = selectedProducts.length === 0;
    printBtn.textContent = `Imprimer sélectionnés (${selectedProducts.length})`;
}

// Aperçu d'un code-barres
function previewBarcode(productId) {
    const product = products.find(p => p.id === productId);
    if (!product) return;
    // Mémoriser l'ID pour l'impression depuis l'aperçu
    currentPreviewProductId = productId;

    const barcodeValue = product.barcode || generateProductBarcode(product.id);
    
    // Créer le contenu de l'aperçu
    const previewContent = document.getElementById('previewContent');
    previewContent.innerHTML = `
        <div class="barcode-container">
            <div class="barcode-label">${escapeHtml(product.name)}</div>
            <svg id="preview-barcode"></svg>
            <div class="barcode-price">${formatCurrency(product.price || 0)}</div>
        </div>
    `;

    // Générer le code-barres
    setTimeout(() => {
        try {
            JsBarcode("#preview-barcode", barcodeValue, {
                format: "CODE128",
                width: 2,
                height: 60,
                displayValue: true,
                fontSize: 12,
                margin: 10
            });
        } catch (error) {
            console.error('Erreur génération aperçu:', error);
        }
    }, 100);

    // Afficher la modal
    const modal = new bootstrap.Modal(document.getElementById('printPreviewModal'));
    modal.show();
}

// Imprimer un seul code-barres
function printSingleBarcode(productId) {
    selectedProducts = [productId];
    printSelected();
}

// Imprimer les codes-barres sélectionnés
function printSelected() {
    if (selectedProducts.length === 0) {
        showError('Veuillez sélectionner au moins un produit');
        return;
    }

    const selectedProductsData = products.filter(p => selectedProducts.includes(p.id));
    generatePrintContent(selectedProductsData);
}

// Générer le contenu d'impression
function generatePrintContent(productsData) {
    const printContent = document.getElementById('printContent');
    printContent.innerHTML = '';

    // Déterminer la taille d'étiquette
    const size = LABEL_PRESETS[currentLabelKey] || LABEL_PRESETS['58x40'];
    const copies = Math.max(1, Math.min(100, parseInt((document.getElementById('copiesPerProduct')?.value) || '1')));

    // Injecter un style @page spécifique (meilleure compatibilité thermiques)
    injectPageSizeStyle(size.width, size.height);

    let labelIndex = 0;
    productsData.forEach((product) => {
        for (let c = 0; c < copies; c++) {
            const barcodeValue = product.barcode || generateProductBarcode(product.id);
            const container = document.createElement('div');
            container.className = 'barcode-container';
            container.style.width = size.width;
            container.style.height = size.height;
            container.style.pageBreakAfter = 'always';
            container.innerHTML = `
                <div class="barcode-label">${escapeHtml(product.name)}</div>
                <svg id="print-barcode-${labelIndex}"></svg>
                <div class="barcode-price">${formatCurrency(product.price || 0)}</div>
            `;
            printContent.appendChild(container);

            // Générer après insertion
            setTimeout(((idx, value) => () => {
                try {
                    JsBarcode(`#print-barcode-${idx}`, value, {
                        format: 'CODE128',
                        width: 1.6,
                        height: mmToPx(size.height) * 0.45, // ~45% de la hauteur
                        displayValue: true,
                        fontSize: 10,
                        margin: 4
                    });
                } catch (e) { console.error('Erreur génération code-barres', e); }
            })(labelIndex, barcodeValue), 0);

            labelIndex++;
        }
    });

    // Afficher la zone d'impression et lancer l'impression
    const printArea = document.getElementById('printArea');
    const previousDisplay = printArea.style.display;
    printArea.style.display = 'block';

    setTimeout(() => {
        window.print();
        // Rétablir l'état après impression
        const restore = () => {
            printArea.style.display = previousDisplay || 'none';
            window.onafterprint = null;
        };
        window.onafterprint = restore;
        // Fallback si onafterprint ne se déclenche pas
        setTimeout(restore, 2000);
    }, 300);
}

// Générer un code-barres personnalisé
function generateCustomBarcode() {
    document.getElementById('customText').value = '';
    document.getElementById('customLabel').value = '';
    document.getElementById('customPrice').value = '';
    document.getElementById('barcodeFormat').value = 'CODE128';
    document.getElementById('customBarcodePreview').innerHTML = '<p class="text-muted">Saisissez un texte pour voir l\'aperçu</p>';
    
    const modal = new bootstrap.Modal(document.getElementById('customBarcodeModal'));
    modal.show();
}

// Mettre à jour l'aperçu du code-barres personnalisé
function updateCustomBarcodePreview() {
    const text = document.getElementById('customText').value;
    const format = document.getElementById('barcodeFormat').value;
    const preview = document.getElementById('customBarcodePreview');

    if (!text.trim()) {
        preview.innerHTML = '<p class="text-muted">Saisissez un texte pour voir l\'aperçu</p>';
        return;
    }

    preview.innerHTML = '<svg id="custom-preview-barcode"></svg>';

    setTimeout(() => {
        try {
            JsBarcode("#custom-preview-barcode", text, {
                format: format,
                width: 2,
                height: 50,
                displayValue: true,
                fontSize: 12,
                margin: 10
            });
        } catch (error) {
            preview.innerHTML = '<p class="text-danger">Erreur: Format ou texte invalide</p>';
        }
    }, 100);
}

// Imprimer le code-barres personnalisé
function printCustomBarcode() {
    const text = document.getElementById('customText').value;
    const label = document.getElementById('customLabel').value;
    const price = document.getElementById('customPrice').value;
    const format = document.getElementById('barcodeFormat').value;

    if (!text.trim()) {
        showError('Veuillez saisir un texte pour le code-barres');
        return;
    }

    // Créer le contenu d'impression
    const printContent = document.getElementById('printContent');
    printContent.innerHTML = `
        <div class="barcode-container">
            <div class="barcode-label">${escapeHtml(label || 'Code-barres personnalisé')}</div>
            <svg id="custom-print-barcode"></svg>
            ${price ? `<div class="barcode-price">${formatCurrency(parseFloat(price))}</div>` : ''}
        </div>
    `;

    // Appliquer la taille d'étiquette choisie
    const size = LABEL_PRESETS[currentLabelKey] || LABEL_PRESETS['58x40'];
    injectPageSizeStyle(size.width, size.height);
    const container = document.querySelector('#printContent .barcode-container');
    if (container) {
        container.style.width = size.width;
        container.style.height = size.height;
        container.style.pageBreakAfter = 'always';
    }

    // Rendre visible la zone d'impression
    const printArea = document.getElementById('printArea');
    const previousDisplay = printArea.style.display;
    printArea.style.display = 'block';

    // Générer le code-barres
    setTimeout(() => {
        try {
            JsBarcode("#custom-print-barcode", text, {
                format: format,
                width: 1.6,
                height: mmToPx(size.height) * 0.45,
                displayValue: true,
                fontSize: 10,
                margin: 5
            });

            // Fermer la modal et lancer l'impression
            const modal = bootstrap.Modal.getInstance(document.getElementById('customBarcodeModal'));
            modal.hide();

            setTimeout(() => {
                window.print();
                const restore = () => {
                    printArea.style.display = previousDisplay || 'none';
                    window.onafterprint = null;
                };
                window.onafterprint = restore;
                setTimeout(restore, 2000);
            }, 500);
        } catch (error) {
            showError('Erreur lors de la génération du code-barres');
        }
    }, 100);
}

// Confirmer l'impression depuis l'aperçu
function confirmPrint() {
    const modal = bootstrap.Modal.getInstance(document.getElementById('printPreviewModal'));
    modal.hide();

    // Utiliser le même pipeline que l'impression depuis la liste pour éviter les pages blanches
    if (currentPreviewProductId != null) {
        selectedProducts = [currentPreviewProductId];
        // Déclenche la génération de #printContent et l'impression
        printSelected();
        // Reset l'état
        currentPreviewProductId = null;
    }
}

// Utilitaires
function formatCurrency(amount) {
    return new Intl.NumberFormat('fr-FR', { 
        style: 'currency', 
        currency: 'XOF',
        minimumFractionDigits: 0,
        maximumFractionDigits: 0
    }).format(Math.round(amount || 0));
}

function showLoading() {
    // No-op to avoid showing a loading spinner
}

function hideLoading() {
    // Le loading sera masqué par displayProducts()
}

// === Utilitaires impression étiquettes ===
function injectPageSizeStyle(width, height) {
    let style = document.getElementById('page-size-style');
    if (!style) {
        style = document.createElement('style');
        style.id = 'page-size-style';
        document.head.appendChild(style);
    }
    style.innerHTML = `@media print { @page { size: ${width} ${height}; margin: 0; } }`;
}

function mmToPx(mmStr) {
    const mm = parseFloat(String(mmStr).replace('mm', '')) || 0;
    // 96 dpi: 1in = 25.4mm -> 1mm = 96/25.4 px
    return Math.round(mm * (96 / 25.4));
}
