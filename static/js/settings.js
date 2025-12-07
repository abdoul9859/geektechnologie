// Gestion des paramètres de l'application
let users = [];
let editingUserId = null;
let categories = [];
let selectedCategoryId = null;
let selectedCategoryName = '';

// Initialisation
document.addEventListener('DOMContentLoaded', function() {
    console.log('[settings.js] DOMContentLoaded - script loaded');
    // console.log('Settings - DOMContentLoaded, authManager:', window.authManager);

    // Même logique que products.js pour attendre l'auth prête (cookies HttpOnly)
    const ready = () => {
        const hasAuthManager = !!window.authManager;
        const hasUser = !!(hasAuthManager && window.authManager.userData && Object.keys(window.authManager.userData).length);
        return hasAuthManager && (window.authManager.isAuthenticatedSync() || hasUser);
    };

    const init = () => {
        // console.log('Settings - Chargement des données...');
        // loadUsers(); // Chargement à la demande seulement
        loadSettings();
        // Ne pas charger les catégories ici, elles seront chargées à la demande
        
        // Feature flag côté client pour activer/désactiver le fallback catégories/list (désactivé par défaut)
        if (typeof window.ENABLE_CATEGORY_LIST_FALLBACK === 'undefined') {
            window.ENABLE_CATEGORY_LIST_FALLBACK = false;
        }
        
        // Ajouter des gestionnaires d'événements pour les onglets Bootstrap
        setupTabEventHandlers();

        // Si l'onglet Catégories est déjà actif au chargement, charger immédiatement
        const categoriesTab = document.querySelector('a[href="#categories"]');
        const categoriesPane = document.getElementById('categories');
        if ((categoriesTab && categoriesTab.classList.contains('active')) || (categoriesPane && categoriesPane.classList.contains('show') && categoriesPane.classList.contains('active'))) {
            console.log('[settings] Categories tab already active on load -> loadCategories()');
            loadCategories();
        }

// Exposer globalement pour compatibilité avec onclick inline du template
if (typeof window !== 'undefined') {
    window.loadProductConditions = loadProductConditions;
    window.saveProductConditions = saveProductConditions;
}
    };

    // Initialiser immédiatement sans délai pour un chargement instantané
    init();
});

// Configurer les gestionnaires d'événements pour les onglets
function setupTabEventHandlers() {
    // console.log('Configuration des gestionnaires d\'événements pour les onglets...');
    
    // Gestionnaire pour l'onglet Catégories
    const categoriesTab = document.querySelector('a[href="#categories"]');
    // console.log('Élément catégories trouvé:', categoriesTab);
    
    if (categoriesTab) {
        // Utiliser 'shown.bs.tab' pour les pills Bootstrap
        categoriesTab.addEventListener('shown.bs.tab', function (e) {
            // console.log('Chargement des catégories...');
            loadCategories();
        });
    } else {
        console.error('Élément onglet Catégories non trouvé !');
    }
    
    // Gestionnaire pour l'onglet Utilisateurs
    const usersTab = document.querySelector('a[href="#users"]');
    // console.log('Élément utilisateurs trouvé:', usersTab);
    
    if (usersTab) {
        usersTab.addEventListener('shown.bs.tab', function (e) {
            // console.log('Chargement des utilisateurs...');
            loadUsers();
        });
    }

    // Gestionnaire pour l'onglet États des produits
    const prodCondsTab = document.querySelector('a[href="#product-conditions"]');
    if (prodCondsTab) {
        prodCondsTab.addEventListener('shown.bs.tab', function () {
            loadProductConditions();
        });
    }
}

// Charger les paramètres existants
async function loadSettings() {
    try {
        // Charger les paramètres depuis SQLite via API
        const settings = await apiStorage.getAppSettings();
        
        // Appliquer les paramètres aux formulaires
        if (settings.general) {
            Object.keys(settings.general).forEach(key => {
                const element = document.getElementById(key);
                if (element) {
                    element.value = settings.general[key];
                }
            });
        }
        
        if (settings.company) {
            Object.keys(settings.company).forEach(key => {
                const element = document.getElementById(key);
                if (element) {
                    element.value = settings.company[key];
                }
            });
        }
        
        if (settings.invoice) {
            Object.keys(settings.invoice).forEach(key => {
                const element = document.getElementById(key);
                if (element) {
                    if (element.type === 'checkbox') {
                        element.checked = settings.invoice[key];
                    } else {
                        // Champ multi-lignes des méthodes de paiement
                        if (key === 'invoicePaymentMethods' && Array.isArray(settings.invoice[key])) {
                            element.value = settings.invoice[key].join('\n');
                        } else {
                            element.value = settings.invoice[key];
                        }
                    }
                }
            });
        }

        // Charger les méthodes de paiement via l'endpoint dédié et remplir la textarea
        try {
            const methods = await apiStorage.getInvoicePaymentMethods();
            const el = document.getElementById('invoicePaymentMethods');
            if (el && Array.isArray(methods)) {
                el.value = methods.join('\n');
            }
        } catch (e) {
            // silencieux
        }

        // Favicon preview from settings
        try {
            const faviconUrl = settings?.general?.faviconUrl || settings?.favicon || null;
            const urlInput = document.getElementById('faviconUrl');
            const preview = document.getElementById('faviconPreview');
            if (urlInput) urlInput.value = faviconUrl || '';
            if (preview) {
                if (faviconUrl) {
                    preview.src = faviconUrl;
                    preview.style.display = '';
                } else {
                    preview.removeAttribute('src');
                    preview.style.display = 'none';
                }
            }
        } catch(e) { /* ignore */ }

        if (settings.stock) {
            Object.keys(settings.stock).forEach(key => {
                const element = document.getElementById(key);
                if (element) {
                    if (element.type === 'checkbox') {
                        element.checked = settings.stock[key];
                    } else {
                        element.value = settings.stock[key];
                    }
                }
            });
        }
        
    } catch (error) {
        console.error('Erreur lors du chargement des paramètres:', error);
    }
}

// ===== ÉTATS DES PRODUITS =====
async function loadProductConditions() {
    const textarea = document.getElementById('productConditionsOptions');
    const select = document.getElementById('productConditionsDefault');
    if (!textarea || !select) return;

    // Spinner léger
    textarea.disabled = true;
    select.disabled = true;

    try {
        const resp = await axios.get('/api/products/settings/conditions');
        const data = resp.data || {};
        const options = Array.isArray(data.options) ? data.options : [];
        const def = data.default || '';

        // Remplir textarea (une valeur par ligne)
        textarea.value = options.join('\n');

        // Remplir select
        while (select.options.length > 1) select.remove(1);
        options.forEach(opt => {
            const o = document.createElement('option');
            o.value = String(opt);
            o.textContent = String(opt);
            select.appendChild(o);
        });
        select.value = def && options.includes(def) ? def : '';
    } catch (e) {
        showError(e?.response?.data?.detail || 'Erreur lors du chargement des états produits');
    } finally {
        textarea.disabled = false;
        select.disabled = false;
    }
}

async function saveProductConditions() {
    const textarea = document.getElementById('productConditionsOptions');
    const select = document.getElementById('productConditionsDefault');
    if (!textarea || !select) return;

    // Lire et nettoyer
    let options = (textarea.value || '')
        .split(/\r?\n/)
        .map(s => s.trim())
        .filter(Boolean);

    // Dédupliquer en conservant l'ordre
    const seen = new Set();
    options = options.filter(v => {
        const key = v.toLowerCase();
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
    });

    const def = (select.value || '').trim();
    if (!options.length) {
        showError('Veuillez saisir au moins un état autorisé');
        return;
    }
    if (def && !options.includes(def)) {
        showError('L\'état par défaut doit faire partie des états autorisés');
        return;
    }

    try {
        await axios.put('/api/products/settings/conditions', {
            options: options,
            default: def || null
        });
        showSuccess('États produits enregistrés');
        // Recharger pour refléter l\'ordre/normalisation renvoyée par le backend, si besoin
        await loadProductConditions();
    } catch (e) {
        showError(e?.response?.data?.detail || 'Erreur lors de l\'enregistrement des états produits');
    }
}

// Sauvegarder les paramètres généraux
async function saveGeneralSettings() {
    try {
        const settings = {
            appName: document.getElementById('appName').value,
            appVersion: document.getElementById('appVersion').value,
            defaultCurrency: document.getElementById('defaultCurrency').value,
            defaultLanguage: document.getElementById('defaultLanguage').value,
            timezone: document.getElementById('timezone').value,
            dateFormat: document.getElementById('dateFormat').value
        };

        // Sauvegarder dans SQLite via API
        await apiStorage.updateAppSetting('general', settings);

        showSuccess('Paramètres généraux sauvegardés avec succès');
    } catch (error) {
        console.error('Erreur lors de la sauvegarde:', error);
        showError('Erreur lors de la sauvegarde des paramètres généraux');
    }
}

// Sauvegarder les paramètres de l'entreprise
async function saveCompanySettings() {
    try {
        const settings = {
            companyName: document.getElementById('companyName').value,
            companyPhone: document.getElementById('companyPhone').value,
            companyAddress: document.getElementById('companyAddress').value,
            companyEmail: document.getElementById('companyEmail').value,
            companyWebsite: document.getElementById('companyWebsite').value,
            companyTaxNumber: document.getElementById('companyTaxNumber').value,
            companyRegistration: document.getElementById('companyRegistration').value,
        };

        if (!settings.companyName.trim()) {
            showError('Le nom de l\'entreprise est obligatoire');
            return;
        }

        // Inclure le logo en DataURL si un fichier est sélectionné
        const fileInput = document.getElementById('companyLogo');
        let logoDataUrl = null;
        if (fileInput && fileInput.files && fileInput.files[0]) {
            logoDataUrl = await new Promise((resolve) => {
                const reader = new FileReader();
                reader.onload = () => resolve(String(reader.result || ''));
                reader.readAsDataURL(fileInput.files[0]);
            });
        } else {
            // Préserver un éventuel logo déjà enregistré
            const current = await apiStorage.getAppSettings();
            logoDataUrl = current?.company?.logo || null;
        }
        if (logoDataUrl) settings.logo = logoDataUrl;

        // 1) Sauvegarder dans SQLite via API (appSettings.company)
        await apiStorage.updateAppSetting('company', settings);

        // 2) Aligner la clef utilisée par l'impression pour éviter tout écart
        const printable = {
            name: settings.companyName,
            address: settings.companyAddress,
            email: settings.companyEmail,
            phone: settings.companyPhone,
            website: settings.companyWebsite,
            logo: logoDataUrl || null,
        };
        await apiStorage.setItem('INVOICE_COMPANY', printable);

        showSuccess('Informations de l\'entreprise sauvegardées avec succès');
    } catch (error) {
        console.error('Erreur lors de la sauvegarde:', error);
        showError('Erreur lors de la sauvegarde des informations de l\'entreprise');
    }
}

// Sauvegarder les paramètres de facturation
async function saveInvoiceSettings() {
    try {
        const settings = {
            invoicePrefix: document.getElementById('invoicePrefix').value,
            quotationPrefix: document.getElementById('quotationPrefix').value,
            paymentTerms: parseInt(document.getElementById('paymentTerms').value),
            invoiceFooter: document.getElementById('invoiceFooter').value,
            autoInvoiceNumber: document.getElementById('autoInvoiceNumber').checked
        };

        // Sauvegarder les méthodes de paiement via endpoint dédié (format JSON canonique)
        const methodsText = document.getElementById('invoicePaymentMethods').value || '';
await apiRequest('/api/user-settings/invoice/payment-methods', {
            method: 'POST',
            data: { methods: methodsText }
        });

        // Sauvegarder dans SQLite via API (autres champs)
        await apiStorage.updateAppSetting('invoice', settings);

        showSuccess('Paramètres de facturation sauvegardés avec succès');
    } catch (error) {
        console.error('Erreur lors de la sauvegarde:', error);
        showError('Erreur lors de la sauvegarde des paramètres de facturation');
    }
}

// Sauvegarder les paramètres de stock
async function saveStockSettings() {
    try {
        const settings = {
            lowStockThreshold: parseInt(document.getElementById('lowStockThreshold').value),
            stockMethod: document.getElementById('stockMethod').value,
            trackSerialNumbers: document.getElementById('trackSerialNumbers').checked,
            autoStockMovements: document.getElementById('autoStockMovements').checked,
            stockAlerts: document.getElementById('stockAlerts').checked
        };

        // Sauvegarder dans SQLite via API
        await apiStorage.updateAppSetting('stock', settings);

        showSuccess('Paramètres de stock sauvegardés avec succès');
    } catch (error) {
        console.error('Erreur lors de la sauvegarde:', error);
        showError('Erreur lors de la sauvegarde des paramètres de stock');
    }
}

// Charger la liste des utilisateurs
async function loadUsers() {
    try {
        // Vérifier d'abord si l'utilisateur est admin
        if (!window.authManager || !window.authManager.isAdmin()) {
            // Afficher un message d'information au lieu d'une erreur
            const tableBody = document.getElementById('usersTableBody');
            if (tableBody) {
                tableBody.innerHTML = `
                    <tr>
                        <td colspan="5" class="text-center">
                            <div class="alert alert-info mb-0">
                                <i class="bi bi-info-circle me-2"></i>
                                Accès restreint. Droits administrateur requis pour gérer les utilisateurs.
                            </div>
                        </td>
                    </tr>
                `;
            }
            return;
        }

        // Appel API via utilitaire avec timeout pour éviter les chargements infinis
        const response = await safeLoadData(
            () => axios.get('/api/auth/users'),
            {
                timeout: 8000,
                fallbackData: [],
                errorMessage: 'Erreur lors du chargement des utilisateurs'
            }
        );
        const data = response.data;
        
        // Validation des données
        if (Array.isArray(data)) {
            users = data;
        } else {
            users = [];
        }
        
        displayUsers();
        
    } catch (error) {
        console.error('Erreur lors du chargement des utilisateurs:', error);
        
        // Afficher un message d'erreur approprié
        const tableBody = document.getElementById('usersTableBody');
        if (tableBody) {
            if (error.response?.status === 403) {
                tableBody.innerHTML = `
                    <tr>
                        <td colspan="5" class="text-center">
                            <div class="alert alert-warning mb-0">
                                <i class="bi bi-exclamation-triangle me-2"></i>
                                Accès refusé. Droits administrateur requis.
                            </div>
                        </td>
                    </tr>
                `;
            } else {
                tableBody.innerHTML = `
                    <tr>
                        <td colspan="5" class="text-center text-danger">
                            <i class="bi bi-exclamation-triangle me-2"></i>
                            Erreur lors du chargement des utilisateurs
                        </td>
                    </tr>
                `;
            }
        }
    }
}

// Afficher les utilisateurs
function displayUsers() {
    const tbody = document.getElementById('usersTableBody');
    if (!tbody) return;

    if (users.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="5" class="text-center text-muted py-4">
                    <i class="bi bi-inbox fs-1 d-block mb-2"></i>
                    Aucun utilisateur trouvé
                </td>
            </tr>
        `;
        return;
    }

    tbody.innerHTML = users.map(user => `
        <tr>
            <td>
                <div class="d-flex align-items-center">
                    <div class="avatar-sm bg-primary rounded-circle d-flex align-items-center justify-content-center me-3">
                        <i class="bi bi-person text-white"></i>
                    </div>
                    <div>
                        <h6 class="mb-0">${escapeHtml(user.username)}</h6>
                        <small class="text-muted">ID: ${user.user_id}</small>
                    </div>
                </div>
            </td>
            <td>${escapeHtml(user.email || '-')}</td>
            <td>
                <span class="badge bg-${getRoleBadgeColor(user.role)}">
                    ${getRoleLabel(user.role)}
                </span>
            </td>
            <td>
                <span class="badge bg-${user.is_active ? 'success' : 'danger'}">
                    ${user.is_active ? 'Actif' : 'Inactif'}
                </span>
            </td>
            <td>
                <div class="btn-group" role="group">
                    <button class="btn btn-sm btn-outline-primary" onclick="editUser(${user.user_id})" title="Modifier">
                        <i class="bi bi-pencil"></i>
                    </button>
                    <button class="btn btn-sm btn-outline-${user.is_active ? 'warning' : 'success'}" 
                            onclick="toggleUserStatus(${user.user_id})" 
                            title="${user.is_active ? 'Désactiver' : 'Activer'}">
                        <i class="bi bi-${user.is_active ? 'pause' : 'play'}"></i>
                    </button>
                    ${user.role !== 'admin' ? `
                        <button class="btn btn-sm btn-outline-danger" onclick="deleteUser(${user.user_id})" title="Supprimer">
                            <i class="bi bi-trash"></i>
                        </button>
                    ` : ''}
                </div>
            </td>
        </tr>
    `).join('');
}

// Utilitaires pour les rôles
function getRoleBadgeColor(role) {
    switch (role) {
        case 'admin': return 'danger';
        case 'manager': return 'warning';
        case 'user': return 'info';
        default: return 'secondary';
    }
}

function getRoleLabel(role) {
    switch (role) {
        case 'admin': return 'Administrateur';
        case 'manager': return 'Manager';
        case 'user': return 'Utilisateur';
        default: return role;
    }
}

// Ouvrir le modal utilisateur
function openUserModal() {
    const form = document.getElementById('userForm');
    if (form) form.reset();
    editingUserId = null;
    // Adapter l'UI du modal pour création
    const title = document.querySelector('#userModal .modal-title');
    if (title) title.innerHTML = '<i class="bi bi-person-plus me-2"></i>Nouvel Utilisateur';
    const btn = document.querySelector('#userModal .btn.btn-primary');
    if (btn) { btn.innerHTML = '<i class="bi bi-check-circle me-2"></i>Créer'; }
    const pwd = document.getElementById('userPassword');
    if (pwd) { pwd.required = true; pwd.value = ''; }
    const modal = new bootstrap.Modal(document.getElementById('userModal'));
    modal.show();
}

// Sauvegarder un utilisateur
async function saveUser() {
    try {
        const username = document.getElementById('userName').value.trim();
        const email = (document.getElementById('userEmail').value || '').trim() || null;
        const password = document.getElementById('userPassword').value;
        const role = document.getElementById('userRole').value;

        if (!username || !role) {
            showError('Tous les champs obligatoires doivent être remplis');
            return;
        }

        if (editingUserId == null) {
            // Création
            if (!password) {
                showError('Le mot de passe est obligatoire pour la création');
                return;
            }
            const payload = { username, email, password, role };
            const resp = await axios.post('/api/auth/register', payload, { withCredentials: true });
            if (!(resp && resp.status >= 200 && resp.status < 300)) throw new Error('Erreur création utilisateur');
            showSuccess('Utilisateur créé avec succès');
        } else {
            // Édition (partielle)
            const payload = { username, email, role };
            if (password) payload.password = password;
            const resp = await axios.put(`/api/auth/users/${editingUserId}`, payload, { withCredentials: true });
            if (!(resp && resp.status >= 200 && resp.status < 300)) throw new Error('Erreur mise à jour utilisateur');
            showSuccess('Utilisateur mis à jour');
        }

        const modal = bootstrap.Modal.getInstance(document.getElementById('userModal'));
        if (modal) modal.hide();
        await loadUsers();
    } catch (error) {
        console.error('Erreur lors de la sauvegarde utilisateur:', error);
        const msg = error?.response?.data?.detail || error.message || 'Erreur lors de la sauvegarde';
        showError(msg);
    }
}

// Modifier un utilisateur
async function editUser(userId) {
    try {
        const u = users.find(u => String(u.user_id) === String(userId));
        if (!u) { showError('Utilisateur introuvable'); return; }
        editingUserId = u.user_id;
        // Pré-remplir le formulaire
        const userName = document.getElementById('userName');
        const userEmail = document.getElementById('userEmail');
        const userPassword = document.getElementById('userPassword');
        const userRole = document.getElementById('userRole');
        if (userName) userName.value = u.username || '';
        if (userEmail) userEmail.value = u.email || '';
        if (userRole) userRole.value = u.role || '';
        if (userPassword) { userPassword.value = ''; userPassword.required = false; }

        // Adapter l'UI du modal pour édition
        const title = document.querySelector('#userModal .modal-title');
        if (title) title.innerHTML = '<i class="bi bi-pencil me-2"></i>Modifier l\'utilisateur';
        const btn = document.querySelector('#userModal .btn.btn-primary');
        if (btn) { btn.innerHTML = '<i class="bi bi-check-circle me-2"></i>Enregistrer'; }

        const modal = new bootstrap.Modal(document.getElementById('userModal'));
        modal.show();
    } catch (e) {
        showError('Impossible d\'ouvrir le formulaire d\'édition');
    }
}

// Activer/désactiver un utilisateur
async function toggleUserStatus(userId) {
    try {
        const u = users.find(x => String(x.user_id) === String(userId));
        if (!u) return;
        const target = !u.is_active;
        await axios.put(`/api/auth/users/${userId}/status`, { is_active: target }, { withCredentials: true });
        await loadUsers();
        showSuccess(`Utilisateur ${target ? 'activé' : 'désactivé'} avec succès`);
    } catch (error) {
        console.error('Erreur lors du changement de statut:', error);
        showError(error?.response?.data?.detail || 'Erreur lors du changement de statut de l\'utilisateur');
    }
}

// Supprimer un utilisateur
async function deleteUser(userId) {
    if (!confirm('Êtes-vous sûr de vouloir supprimer cet utilisateur ?')) return;
    try {
        await axios.delete(`/api/auth/users/${userId}`, { withCredentials: true });
        await loadUsers();
        showSuccess('Utilisateur supprimé avec succès');
    } catch (error) {
        console.error('Erreur lors de la suppression:', error);
        showError(error?.response?.data?.detail || 'Erreur lors de la suppression de l\'utilisateur');
    }
}

// Créer une sauvegarde
async function createBackup() {
    try {
        const btn = document.getElementById('createBackupBtn');
        const oldHtml = btn ? btn.innerHTML : '';
        if (btn) {
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>Création...';
        }
        showInfo('Création de la sauvegarde en cours...');
        
        // Appeler l'API pour créer une sauvegarde
        const response = await axios.get('/api/backup/create', {
            responseType: 'blob'
        });
        
        // Créer un lien de téléchargement
        const date = new Date().toISOString().split('T')[0];
        const filename = `geek-technologie-backup-${date}.db`;
        
        const url = window.URL.createObjectURL(new Blob([response.data]));
        const link = document.createElement('a');
        link.href = url;
        link.setAttribute('download', filename);
        document.body.appendChild(link);
        link.click();
        link.remove();
        window.URL.revokeObjectURL(url);
        
        showSuccess(`Sauvegarde créée: ${filename}`);
        
    } catch (error) {
        console.error('Erreur lors de la sauvegarde:', error);
        showError('Erreur lors de la création de la sauvegarde');
    } finally {
        const btn = document.getElementById('createBackupBtn');
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = '<i class="bi bi-download me-2"></i>Créer une sauvegarde';
        }
    }
}

// Restaurer une sauvegarde
async function restoreBackup() {
    const fileInput = document.getElementById('backupFile');
    if (!fileInput.files.length) {
        showError('Veuillez sélectionner un fichier de sauvegarde');
        return;
    }

    if (!confirm('Êtes-vous sûr de vouloir restaurer cette sauvegarde ? Toutes les données actuelles seront remplacées.')) {
        return;
    }

    try {
        const btn = document.getElementById('restoreBackupBtn');
        const oldHtml = btn ? btn.innerHTML : '';
        if (btn) {
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>Restauration...';
        }
        showInfo('Restauration en cours...');
        
        const formData = new FormData();
        formData.append('file', fileInput.files[0]);
        
        // Ne pas définir Content-Type manuellement, axios le gère automatiquement avec le boundary
        const response = await axios.post('/api/backup/restore', formData);
        
        showSuccess('Sauvegarde restaurée avec succès. Rechargement de la page...');
        
        // Recharger la page après 2 secondes
        setTimeout(() => {
            window.location.reload();
        }, 2000);
        
    } catch (error) {
        console.error('Erreur lors de la restauration:', error);
        showError(error?.response?.data?.detail || 'Erreur lors de la restauration de la sauvegarde');
    } finally {
        const btn = document.getElementById('restoreBackupBtn');
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = '<i class="bi bi-upload me-2"></i>Restaurer';
        }
    }
}

// Sauvegarder les paramètres de sauvegarde
async function saveBackupSettings() {
    try {
        const settings = {
            autoBackup: document.getElementById('autoBackup').checked,
            backupFrequency: document.getElementById('backupFrequency').value,
            backupRetention: parseInt(document.getElementById('backupRetention').value)
        };

        // Sauvegarder dans SQLite via API
        await apiStorage.updateAppSetting('backup', settings);

        showSuccess('Paramètres de sauvegarde sauvegardés avec succès');
    } catch (error) {
        console.error('Erreur lors de la sauvegarde:', error);
        showError('Erreur lors de la sauvegarde des paramètres de sauvegarde');
    }
}

// ===== GESTION DES CATÉGORIES =====

// Charger les catégories
async function loadCategories() {
    const tableBody = document.getElementById('categoriesTableBody');
    if (!tableBody) {
        console.error('Table body des catégories non trouvé');
        return;
    }

    try {
        // Afficher le spinner
        tableBody.innerHTML = `
            <tr>
                <td colspan="3" class="text-center">
                    <div class="spinner-border" role="status">
                        <span class="visually-hidden">Chargement...</span>
                    </div>
                </td>
            </tr>
        `;
        
        // Appel API via utilitaire avec timeout pour éviter les chargements infinis
        const response = await safeLoadData(
            () => axios.get('/api/products/categories'),
            {
                timeout: 8000,
                fallbackData: [],
                errorMessage: 'Erreur lors du chargement des catégories'
            }
        );
        const data = response.data;
        
        // Validation et normalisation des données (catégories déclarées)
        let declared = [];
        if (Array.isArray(data)) {
            declared = data.map(c => {
                if (typeof c === 'string') {
                    return { name: c, category_id: null, requires_variants: false, product_count: 0 };
                }
                const rawId = c.category_id ?? c.id ?? c.categoryId ?? null;
                const parsedId = (rawId === null || rawId === undefined || rawId === '') ? null : Number(rawId);
                const normalizedId = Number.isFinite(parsedId) ? parsedId : null;
                const normalizedName = c.name ?? c.label ?? '';
                const requiresVariants = typeof c.requires_variants === 'boolean' ? c.requires_variants : !!c.requiresVariants;
                const productCount = typeof c.product_count === 'number' ? c.product_count : (c.count ?? 0);
                return { ...c, category_id: normalizedId, name: normalizedName, requires_variants: requiresVariants, product_count: productCount };
            });
        } else {
            declared = [];
        }

        // Fallback: compléter avec les catégories détectées côté Produits (optionnel)
        let merged = [...declared];
        if (window.ENABLE_CATEGORY_LIST_FALLBACK) {
            try {
                // Fallback silencieux: si l'endpoint n'existe pas (404), ignorer sans bruit
                const resList = await axios.get('/api/products/categories/list').catch(() => ({ data: [] }));
                const names = Array.isArray(resList.data) ? resList.data : [];
                const known = new Set(merged.map(c => String(c.name).toLowerCase()));
                names.forEach(n => {
                    const nm = String(n || '').trim();
                    if (!nm) return;
                    if (!known.has(nm.toLowerCase())) {
                        merged.push({ category_id: null, name: nm, requires_variants: false, product_count: 0, _ghost: true });
                        known.add(nm.toLowerCase());
                    }
                });
            } catch (e) { /* ignore fallback errors */ }
        }

        categories = merged;

        console.log('[settings] categories loaded:', Array.isArray(categories) ? categories.length : 0, categories.slice ? categories.slice(0, 3) : categories);
        
        // Vider le tableau et afficher les catégories
        tableBody.innerHTML = '';
        
        if (categories.length === 0) {
            tableBody.innerHTML = `
                <tr>
                    <td colspan="3" class="text-center">
                        <div class="alert alert-light mb-0">
                            <i class="bi bi-info-circle me-2"></i>Aucune catégorie trouvée
                        </div>
                    </td>
                </tr>
            `;
        } else {
            // Ajouter chaque catégorie
            categories.forEach(category => {
                const row = document.createElement('tr');
                if (category.category_id != null) row.dataset.categoryId = String(category.category_id);
                row.dataset.categoryName = category.name || '';
                row.style.cursor = 'pointer';
                // Debug
                // console.debug('[settings] render row for category', category.category_id, category.name);
                row.innerHTML = `
                    <td>
                        <strong>${escapeHtml(category.name || '')}</strong>
                        ${category.requires_variants ? '<span class="badge bg-info ms-2">Variantes requises</span>' : ''}
                        ${category._ghost ? '<span class="badge bg-light text-dark ms-2">détectée</span>' : ''}
                    </td>
                    <td>${category.product_count || '0'}</td>
                    <td>
                        <div class="btn-group btn-group-sm">
                            <button class="btn btn-outline-secondary ${category._ghost ? 'disabled' : ''}" title="Gérer les attributs" data-action="select-attributes" ${category._ghost ? 'disabled' : ''}>
                                <i class="bi bi-sliders"></i>
                            </button>
                            ${category.category_id != null ? `
                                <button class="btn btn-outline-primary" onclick="editCategory(${category.category_id})">
                                    <i class="bi bi-pencil"></i>
                                </button>
                                <button class="btn btn-outline-danger" onclick="deleteCategory(${category.category_id}, '${escapeHtml(category.name || '')}')">
                                    <i class="bi bi-trash"></i>
                                </button>
                            ` : `
                                <button class="btn btn-outline-primary" onclick="startCreateCategoryFromName('${escapeHtml(category.name || '')}')" title="Créer cette catégorie">
                                    <i class="bi bi-plus-circle"></i>
                                </button>
                            `}
                        </div>
                    </td>
                `;
                // Rendre toute la ligne cliquable pour sélectionner la catégorie
                row.addEventListener('click', (e) => {
                    // Éviter le déclenchement si clic sur les boutons d'action autres que sliders
                    const target = e.target;
                    if (target.closest('.btn-outline-primary') || target.closest('.btn-outline-danger')) return;
                    const id = row.dataset.categoryId ? Number(row.dataset.categoryId) : null;
                    const name = row.dataset.categoryName || '';
                    console.log('[settings] row click - select category', id, name);
                    selectCategoryForAttributes(id, name);
                });
                tableBody.appendChild(row);
            });

            // Delegate clicks on the sliders button to ensure selection works even if inline handler fails
            if (!window._catTableBound) {
                tableBody.addEventListener('click', (e) => {
                    const btn = e.target.closest('button[data-action="select-attributes"]');
                    if (btn) {
                        const tr = btn.closest('tr');
                        if (!tr) return;
                        const id = tr.dataset.categoryId ? Number(tr.dataset.categoryId) : null;
                        const name = tr.dataset.categoryName || '';
                        console.log('[settings] sliders button click - select category', id, name);
                        selectCategoryForAttributes(id, name);
                    }
                });
                window._catTableBound = true;
            }
        }
        
    } catch (error) {
        console.error('Erreur lors du chargement des catégories:', error);
        tableBody.innerHTML = `
            <tr>
                <td colspan="3" class="text-center text-danger">
                    <i class="bi bi-exclamation-triangle me-2"></i>
                    Erreur lors du chargement des catégories
                </td>
            </tr>
        `;
        showError(error.response?.data?.detail || 'Erreur lors du chargement des catégories');
    }
}



// Sauvegarder une catégorie (création ou modification)
async function saveCategory() {
    try {
        const categoryId = document.getElementById('categoryId').value;
        const categoryName = document.getElementById('categoryName').value;
        const requiresVariants = document.getElementById('categoryRequiresVariants').checked;
        
        if (!categoryName.trim()) {
            showError('Le nom de la catégorie est obligatoire');
            return;
        }
        
        // Préparer les données
        const categoryData = {
            name: categoryName,
            requires_variants: !!requiresVariants
        };
        
        const url = categoryId ? `/api/products/categories/${categoryId}` : '/api/products/categories';
        if (categoryId) {
            await axios.put(url, categoryData);
        } else {
            await axios.post(url, categoryData);
        }

        // Purger le cache produits pour refléter immédiatement les changements
        try {
            await fetch('/api/products/cache', { method: 'DELETE', credentials: 'include' });
        } catch (e) { /* silencieux */ }
        
        // Réinitialiser le formulaire
        document.getElementById('categoryId').value = '';
        document.getElementById('categoryName').value = '';
        document.getElementById('saveCategoryBtn').innerHTML = '<i class="bi bi-plus-circle me-2"></i>Ajouter';
        document.getElementById('categoryRequiresVariants').checked = false;
        
        // Si la catégorie supprimée était sélectionnée pour les attributs, réinitialiser
        if (selectedCategoryId && Number(selectedCategoryId) === Number(categoryId)) {
            selectedCategoryId = null;
            selectedCategoryName = '';
            updateSelectedCategoryLabel();
            clearAttributesTable();
        }
        // Recharger les catégories
        await loadCategories();
        
        showSuccess(`Catégorie ${categoryId ? 'modifiée' : 'ajoutée'} avec succès`);
        
    } catch (error) {
        console.error('Erreur lors de la sauvegarde de la catégorie:', error);
        showError(error.response?.data?.detail || error.message || 'Erreur lors de la sauvegarde de la catégorie');
    }
}

// Préremplir le formulaire pour créer une catégorie à partir d'un nom détecté
function startCreateCategoryFromName(name) {
    try {
        document.getElementById('categoryId').value = '';
        document.getElementById('categoryName').value = name || '';
        document.getElementById('categoryRequiresVariants').checked = false;
        document.getElementById('saveCategoryBtn').innerHTML = '<i class="bi bi-plus-circle me-2"></i>Créer';
        document.getElementById('categoryName').focus();
    } catch (e) {}
}

// Éditer une catégorie
function editCategory(categoryId) {
    // Valider et normaliser l'ID
    const targetId = Number(categoryId);
    if (!Number.isFinite(targetId)) {
        showError('Catégorie non trouvée');
        return;
    }
    // Trouver la catégorie (comparaison numérique robuste)
    const category = categories.find(c => Number(c.category_id) === targetId);
    
    if (!category) {
        showError('Catégorie non trouvée');
        return;
    }
    
    // Remplir le formulaire
    document.getElementById('categoryId').value = categoryId;
    document.getElementById('categoryName').value = category.name || '';
    document.getElementById('categoryRequiresVariants').checked = !!category.requires_variants;
    document.getElementById('saveCategoryBtn').innerHTML = '<i class="bi bi-check-circle me-2"></i>Modifier';
    
    // Focus sur le champ
    document.getElementById('categoryName').focus();
}

// Supprimer une catégorie (afficher le modal de confirmation)
function deleteCategory(categoryId, categoryName) {
    document.getElementById('deleteCategoryId').value = categoryId;
    document.getElementById('categoryToDelete').textContent = categoryName;
    
    // Afficher le modal
    const modal = new bootstrap.Modal(document.getElementById('deleteCategoryModal'));
    modal.show();
}

// Confirmer la suppression d'une catégorie
async function confirmDeleteCategory() {
    try {
        const categoryId = document.getElementById('deleteCategoryId').value;
        
        if (!categoryId) {
            showError('ID de catégorie manquant');
            return;
        }
        
        // Appel API via axios
        await axios.delete(`/api/products/categories/${categoryId}`);
        
        // Fermer le modal
        const modal = bootstrap.Modal.getInstance(document.getElementById('deleteCategoryModal'));
        modal.hide();
        
        // Purger le cache produits puis recharger
        try {
            await fetch('/api/products/cache', { method: 'DELETE', credentials: 'include' });
        } catch (e) { /* ignore */ }
        await loadCategories();
        
        showSuccess('Catégorie supprimée avec succès');
        
    } catch (error) {
        console.error('Erreur lors de la suppression de la catégorie:', error);
        showError(error.response?.data?.detail || error.message || 'Erreur lors de la suppression de la catégorie');
    }
}

// Fonction utilitaire pour échapper le HTML
function escapeHtml(text) {
    if (typeof text !== 'string') {
        return '';
    }
    return text
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

// Afficher/masquer le chargement
function showLoading(elementId) {
    const element = document.getElementById(elementId);
    if (element) {
        element.innerHTML = `
            <tr>
                <td colspan="3" class="text-center">
                    <div class="spinner-border" role="status">
                        <span class="visually-hidden">Chargement...</span>
                    </div>
                </td>
            </tr>
        `;
    }
}

function hideLoading(elementId) {
    // Cette fonction est vide car le contenu sera remplacé par displayCategories()
}

// ====== ATTRIBUTS DE CATÉGORIE ======

function updateSelectedCategoryLabel() {
    const el = document.getElementById('selectedCategoryLabel');
    if (!el) return;
    if (!selectedCategoryId) {
        el.textContent = 'Aucune catégorie sélectionnée';
    } else {
        el.textContent = `Catégorie sélectionnée: ${selectedCategoryName} (#${selectedCategoryId})`;
    }
}

function clearAttributesTable() {
    const tbody = document.getElementById('attributesTableBody');
    if (!tbody) return;
    tbody.innerHTML = `<tr><td colspan="5" class="text-center text-muted">${selectedCategoryId ? 'Aucun attribut' : 'Sélectionnez une catégorie pour gérer ses attributs'}</td></tr>`;
}

async function selectCategoryForAttributes(categoryId, categoryName) {
    if (!categoryId) {
        showError('Impossible de sélectionner cette catégorie (ID manquant)');
        return;
    }
    selectedCategoryId = Number(categoryId);
    selectedCategoryName = categoryName || '';
    updateSelectedCategoryLabel();

    // Mettre en surbrillance la ligne sélectionnée
    const tbody = document.getElementById('categoriesTableBody');
    if (tbody) {
        Array.from(tbody.querySelectorAll('tr')).forEach(tr => {
            if (tr.dataset.categoryId === String(selectedCategoryId)) {
                tr.classList.add('table-active');
            } else {
                tr.classList.remove('table-active');
            }
        });
    }

    await loadCategoryAttributes();
}

async function loadCategoryAttributes() {
    const tbody = document.getElementById('attributesTableBody');
    if (!tbody) return;
    if (!selectedCategoryId) {
        clearAttributesTable();
        return;
    }
    tbody.innerHTML = `
        <tr>
            <td colspan="5" class="text-center">
                <div class="spinner-border" role="status"><span class="visually-hidden">Chargement...</span></div>
            </td>
        </tr>`;
    try {
        const { data } = await axios.get(`/api/products/categories/${selectedCategoryId}/attributes`);
        if (!Array.isArray(data) || data.length === 0) {
            clearAttributesTable();
            return;
        }
        tbody.innerHTML = data.map(attr => `
            <tr>
                <td><strong>${escapeHtml(attr.name)}</strong></td>
                <td><span class="badge bg-light text-dark">${escapeHtml(attr.type)}</span></td>
                <td>${attr.required ? '<span class="badge bg-danger">Oui</span>' : '<span class="badge bg-secondary">Non</span>'}</td>
                <td>
                    ${(attr.values || []).map(v => `<span class="badge rounded-pill bg-info text-dark me-1 mb-1">${escapeHtml(v.value)} <button class="btn btn-sm btn-link p-0 ms-1" title="Supprimer" onclick="deleteAttributeValue(${attr.attribute_id}, ${v.value_id})"><i class=\"bi bi-x\"></i></button></span>`).join('') || '<span class="text-muted">—</span>'}
                </td>
                <td>
                    <div class="d-flex gap-1">
                        <button class="btn btn-sm btn-outline-success" title="Ajouter une valeur" onclick="promptAddAttributeValue(${attr.attribute_id})"><i class="bi bi-plus-circle"></i></button>
                        <button class="btn btn-sm btn-outline-danger" title="Supprimer l\'attribut" onclick="deleteCategoryAttribute(${attr.attribute_id})"><i class="bi bi-trash"></i></button>
                    </div>
                </td>
            </tr>
        `).join('');
    } catch (err) {
        console.error('Erreur chargement attributs:', err);
        showError(err.response?.data?.detail || 'Erreur lors du chargement des attributs');
        clearAttributesTable();
    }
}

async function saveCategoryAttribute() {
    try {
        if (!selectedCategoryId) {
            showError('Sélectionnez une catégorie avant d\'ajouter un attribut');
            return;
        }
        const name = document.getElementById('attrName').value.trim();
        const type = document.getElementById('attrType').value;
        const required = document.getElementById('attrRequired').checked;
        const valuesRaw = document.getElementById('attrValues').value;
        if (!name) {
            showError('Le nom de l\'attribut est obligatoire');
            return;
        }
        const values = (valuesRaw || '')
            .split(',')
            .map(v => v.trim())
            .filter(v => v.length)
            .map(v => ({ value: v }));
        await axios.post(`/api/products/categories/${selectedCategoryId}/attributes`, {
            name,
            type,
            required,
            multi_select: type === 'multiselect',
            values
        });
        // reset form
        document.getElementById('attrName').value = '';
        document.getElementById('attrType').value = 'select';
        document.getElementById('attrRequired').checked = false;
        document.getElementById('attrValues').value = '';
        await loadCategoryAttributes();
        showSuccess('Attribut ajouté');
    } catch (err) {
        console.error('Erreur ajout attribut:', err);
        showError(err.response?.data?.detail || 'Erreur lors de l\'ajout de l\'attribut');
    }
}

async function deleteCategoryAttribute(attributeId) {
    try {
        if (!selectedCategoryId) return;
        await axios.delete(`/api/products/categories/${selectedCategoryId}/attributes/${attributeId}`);
        await loadCategoryAttributes();
        showSuccess('Attribut supprimé');
    } catch (err) {
        console.error('Erreur suppression attribut:', err);
        showError(err.response?.data?.detail || 'Impossible de supprimer cet attribut');
    }
}

function promptAddAttributeValue(attributeId) {
    const value = prompt('Nouvelle valeur (ex: 128 GB)');
    if (!value || !value.trim()) return;
    addAttributeValue(attributeId, value.trim());
}

async function addAttributeValue(attributeId, value) {
    try {
        await axios.post(`/api/products/categories/${selectedCategoryId}/attributes/${attributeId}/values`, { value });
        await loadCategoryAttributes();
        showSuccess('Valeur ajoutée');
    } catch (err) {
        console.error('Erreur ajout valeur:', err);
        showError(err.response?.data?.detail || 'Erreur lors de l\'ajout de la valeur');
    }
}

async function deleteAttributeValue(attributeId, valueId) {
    try {
        await axios.delete(`/api/products/categories/${selectedCategoryId}/attributes/${attributeId}/values/${valueId}`);
        await loadCategoryAttributes();
        showSuccess('Valeur supprimée');
    } catch (err) {
        console.error('Erreur suppression valeur:', err);
        showError(err.response?.data?.detail || 'Impossible de supprimer cette valeur');
    }
}

// ===== Favicon (upload / reset) =====
async function uploadFavicon() {
    const fileInput = document.getElementById('faviconFile');
    if (!fileInput || !fileInput.files || !fileInput.files.length) {
        showError("Veuillez sélectionner une image à uploader");
        return;
    }
    const file = fileInput.files[0];

    // Validation du type de fichier
    const validTypes = ['image/x-icon', 'image/png', 'image/svg+xml', 'image/vnd.microsoft.icon'];
    if (!validTypes.includes(file.type)) {
        showError("Format de fichier non valide. Utilisez ICO, PNG ou SVG");
        return;
    }

    const fd = new FormData();
    fd.append('file', file);
    try {
        const resp = await fetch('/api/user-settings/upload/favicon', {
            method: 'POST',
            body: fd,
            credentials: 'include'
        });
        if (!resp.ok) {
            const err = await resp.json().catch(()=>({detail:'Erreur upload'}));
            throw new Error(err.detail || 'Erreur upload');
        }
        const data = await resp.json();
        const url = data.url;

        // Update preview
        const urlInput = document.getElementById('faviconUrl');
        const preview = document.getElementById('faviconPreview');
        if (urlInput) urlInput.value = url || '';
        if (preview && url) {
            preview.src = url;
            preview.style.display = '';
        }

        // Update actual favicon in page
        updatePageFavicon(url);

        // Save to settings
        await apiStorage.updateAppSetting('general', { faviconUrl: url });

        showSuccess("Favicon mis à jour");
    } catch (e) {
        console.error(e);
        showError(e.message || "Erreur lors de l'upload du favicon");
    }
}

async function resetFavicon() {
    try {
        const resp = await fetch('/api/user-settings/favicon/reset', {
            method: 'POST',
            credentials: 'include'
        });
        if (!resp.ok) {
            const err = await resp.json().catch(()=>({detail:'Erreur reset'}));
            throw new Error(err.detail || 'Erreur reset');
        }

        const urlInput = document.getElementById('faviconUrl');
        const preview = document.getElementById('faviconPreview');
        if (urlInput) urlInput.value = '';
        if (preview) {
            preview.removeAttribute('src');
            preview.style.display = 'none';
        }
        const fileInput = document.getElementById('faviconFile');
        if (fileInput) fileInput.value = '';

        // Reset to default favicon
        updatePageFavicon('/static/favicon.ico');

        // Remove from settings
        await apiStorage.updateAppSetting('general', { faviconUrl: null });

        showSuccess("Favicon réinitialisé");
    } catch (e) {
        console.error(e);
        showError(e.message || 'Erreur lors de la réinitialisation');
    }
}

// Live preview for favicon
document.addEventListener('change', function(e){
    const t = e.target;
    if (t && t.id === 'faviconFile' && t.files && t.files[0]) {
        const reader = new FileReader();
        reader.onload = () => {
            const preview = document.getElementById('faviconPreview');
            if (preview) {
                preview.src = String(reader.result || '');
                preview.style.display = '';
            }
        };
        reader.readAsDataURL(t.files[0]);
    }
});

// Helper function to update favicon in the page
function updatePageFavicon(url) {
    // Remove existing favicon links
    const existingFavicons = document.querySelectorAll('link[rel="icon"], link[rel="shortcut icon"]');
    existingFavicons.forEach(link => link.remove());

    // Add new favicon
    const link = document.createElement('link');
    link.rel = 'icon';
    link.href = url;
    document.head.appendChild(link);

    const shortcutLink = document.createElement('link');
    shortcutLink.rel = 'shortcut icon';
    shortcutLink.href = url;
    document.head.appendChild(shortcutLink);
}

// Rendre accessibles pour les gestionnaires inline dans le HTML
// (assure la compatibilité même si le script est chargé en mode différé)
window.saveCategoryAttribute = saveCategoryAttribute;
window.deleteCategoryAttribute = deleteCategoryAttribute;
window.promptAddAttributeValue = promptAddAttributeValue;
window.addAttributeValue = addAttributeValue;
window.deleteAttributeValue = deleteAttributeValue;
window.selectCategoryForAttributes = selectCategoryForAttributes;
window.saveCategory = saveCategory;
window.editCategory = editCategory;
window.deleteCategory = deleteCategory;
window.confirmDeleteCategory = confirmDeleteCategory;
window.startCreateCategoryFromName = startCreateCategoryFromName;
window.uploadFavicon = uploadFavicon;
window.resetFavicon = resetFavicon;
