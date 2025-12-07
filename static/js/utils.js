// Fonctions utilitaires globales
// Définir l'origine API pour éviter les mixed-contents entre HTTP/HTTPS
try {
    if (typeof window !== 'undefined') {
        window.API_ORIGIN = window.location.origin;
    }
} catch (e) {}

// Fonction globale de traduction des statuts
function translateStatus(status) {
    const s = String(status || '').trim();
    switch (s.toUpperCase()) {
        case 'PAID': return 'Payée';
        case 'SENT': return 'Envoyée';
        case 'DRAFT': return 'Brouillon';
        case 'OVERDUE': return 'En retard';
        case 'CANCELLED': return 'Annulée';
        case 'PARTIALLY_PAID': return 'Partiellement payée';
        default:
            // Garder les statuts français existants
            const sLower = s.toLowerCase();
            if (sLower === 'en attente') return 'En attente';
            if (sLower === 'payée') return 'Payée';
            if (sLower === 'partiellement payée') return 'Partiellement payée';
            if (sLower === 'accepté') return 'Accepté';
            if (sLower === 'refusé') return 'Refusé';
            if (sLower === 'expiré') return 'Expiré';
            return s || 'Inconnu';
    }
}

// Fonction globale pour obtenir la classe CSS du statut
function getStatusBadgeClass(status) {
    const translatedStatus = translateStatus(status);
    const statusClasses = {
        'En attente': 'warning',
        'Payée': 'success',
        'Partiellement payée': 'info',
        'En retard': 'danger',
        'Annulée': 'secondary',
        'Envoyée': 'primary',
        'Brouillon': 'secondary',
        'Accepté': 'success',
        'Refusé': 'danger',
        'Expiré': 'warning'
    };
    return statusClasses[translatedStatus] || 'secondary';
}

// Fonction utilitaire global pour charger des données avec timeout et gestion d'erreur
async function safeLoadData(apiCall, options = {}) {
    const {
        timeout = 15000, // 15 secondes par défaut (réduit de 20s)
        fallbackData = [],
        showSpinner = true,
        spinnerElement = null,
        errorMessage = 'Erreur lors du chargement des données',
        retryCount = 1 // Réduit de 2 à 1 retry
    } = options;

    let timeoutId;
    let attempt = 0;

    while (attempt <= retryCount) {
        try {
            // Indicateur visuel désactivé volontairement (aucun spinner)

            // Créer une promesse avec timeout
            const timeoutPromise = new Promise((_, reject) => {
                timeoutId = setTimeout(() => {
                    reject(new Error('Timeout: La requête a pris trop de temps'));
                }, timeout);
            });

            // Exécuter l'appel API avec timeout
            const result = await Promise.race([apiCall(), timeoutPromise]);

            // Nettoyer le timeout
            clearTimeout(timeoutId);

            // Vérifier si la réponse est valide
            if (result && result.data !== undefined) {
                return result;
            } else {
                // Réponse invalide, retourner données vides
                return { data: fallbackData };
            }

        } catch (error) {
            console.error(`safeLoadData error (attempt ${attempt + 1}):`, error);

            attempt++;

            // Si c'est le dernier essai, gérer l'erreur
            if (attempt > retryCount) {
                // Afficher l'erreur à l'utilisateur si demandé
                if (errorMessage && typeof showAlert === 'function') {
                    showAlert(errorMessage, 'warning');
                }

                // Aucun spinner à cacher

                return { data: fallbackData };
            }

            // Attendre un peu avant de réessayer (backoff réduit)
            const backoff = Math.min(500, 200 * Math.pow(2, attempt - 1)); // Réduit de 5000ms max à 500ms max
            await new Promise(resolve => setTimeout(resolve, backoff));
        } finally {
            // Nettoyer le timeout au cas où
            if (timeoutId) {
                clearTimeout(timeoutId);
            }
        }
    }
}

// Afficher une alerte Bootstrap
function showAlert(message, type = 'info', duration = 5000) {
    let alertsContainer = document.getElementById('alerts-container');
    if (!alertsContainer) {
        // Créer dynamiquement le conteneur si absent
        alertsContainer = document.createElement('div');
        alertsContainer.id = 'alerts-container';
        alertsContainer.className = 'position-fixed top-0 end-0 p-3';
        alertsContainer.style.zIndex = '20000';
        alertsContainer.style.width = '360px';
        document.body.appendChild(alertsContainer);
    }

    const alertId = 'alert-' + Date.now();
    const alertDiv = document.createElement('div');
    alertDiv.id = alertId;
    alertDiv.className = `alert alert-${type} alert-dismissible fade show`;
    alertDiv.innerHTML = `
        <i class="bi bi-${getAlertIcon(type)} me-2"></i>
        ${message}
        <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
    `;

    // Toujours attacher au body (au-dessus des modals)
    document.body.appendChild(alertsContainer);
    alertsContainer.appendChild(alertDiv);

    // Supprimer automatiquement après la durée spécifiée
    if (duration > 0) {
        setTimeout(() => {
            const alert = document.getElementById(alertId);
            if (alert) {
                alert.remove();
            }
        }, duration);
    }
}

function getAlertIcon(type) {
    const icons = {
        'success': 'check-circle-fill',
        'danger': 'exclamation-triangle-fill',
        'warning': 'exclamation-triangle-fill',
        'info': 'info-circle-fill',
        'primary': 'info-circle-fill',
        'secondary': 'info-circle-fill'
    };
    return icons[type] || 'info-circle-fill';
}

// Formater les montants en CFA
function formatCurrency(amount) {
    return new Intl.NumberFormat('fr-FR', {
        style: 'currency',
        currency: 'XOF',
        minimumFractionDigits: 0
    }).format(amount);
}

// Formater les dates
function formatDate(dateString) {
    return new Date(dateString).toLocaleDateString('fr-FR');
}

function formatDateTime(dateString) {
    return new Date(dateString).toLocaleString('fr-FR');
}

// Valider un email
function isValidEmail(email) {
    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    return emailRegex.test(email);
}

// Valider un numéro de téléphone sénégalais
function isValidPhone(phone) {
    const phoneRegex = /^(\+221|00221)?[0-9]{9}$/;
    return phoneRegex.test(phone.replace(/\s/g, ''));
}

// Générer un numéro automatique
function generateNumber(prefix, lastNumber = 0) {
    const nextNumber = lastNumber + 1;
    return `${prefix}${nextNumber.toString().padStart(4, '0')}`;
}

// Debounce pour les recherches
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

// Confirmer une action
function confirmAction(message, callback) {
    if (confirm(message)) {
        callback();
    }
}

// Loader pour les boutons
function setButtonLoading(button, loading = true) {
    if (!button) return;
    // Ne plus modifier le texte ou ajouter une icône; seulement (dé)désactiver
    button.disabled = !!loading;
}

// Vider un formulaire
function clearForm(formId) {
    const form = document.getElementById(formId);
    if (form) {
        form.reset();
        // Supprimer les classes de validation Bootstrap
        form.querySelectorAll('.is-valid, .is-invalid').forEach(el => {
            el.classList.remove('is-valid', 'is-invalid');
        });
    }
}

// Valider un formulaire Bootstrap
function validateForm(formId) {
    const form = document.getElementById(formId);
    if (!form) return false;

    let isValid = true;
    const inputs = form.querySelectorAll('input[required], select[required], textarea[required]');

    inputs.forEach(input => {
        if (!input.value.trim()) {
            input.classList.add('is-invalid');
            input.classList.remove('is-valid');
            isValid = false;
        } else {
            input.classList.add('is-valid');
            input.classList.remove('is-invalid');
        }
    });

    return isValid;
}

// Gérer les erreurs de validation du serveur
function handleValidationErrors(errors, formId) {
    const form = document.getElementById(formId);
    if (!form) return;

    // Réinitialiser les erreurs précédentes
    form.querySelectorAll('.is-invalid').forEach(el => {
        el.classList.remove('is-invalid');
    });
    form.querySelectorAll('.invalid-feedback').forEach(el => {
        el.remove();
    });

    // Afficher les nouvelles erreurs
    if (Array.isArray(errors)) {
        errors.forEach(error => {
            const field = form.querySelector(`[name="${error.field}"]`);
            if (field) {
                field.classList.add('is-invalid');
                const feedback = document.createElement('div');
                feedback.className = 'invalid-feedback';
                feedback.textContent = error.message;
                field.parentNode.appendChild(feedback);
            }
        });
    }
}

// Classe de pagination réutilisable
class UtilsPagination {
    constructor(containerId, onPageChange) {
        this.container = document.getElementById(containerId);
        this.onPageChange = onPageChange;
        this.currentPage = 1;
        this.totalPages = 1;
    }

    render(currentPage, totalPages, totalItems) {
        this.currentPage = currentPage;
        this.totalPages = totalPages;

        if (!this.container || totalPages <= 1) {
            if (this.container) this.container.innerHTML = '';
            return;
        }

        let html = '<nav><ul class="pagination justify-content-center">';

        // Bouton précédent
        html += `
            <li class="page-item ${currentPage === 1 ? 'disabled' : ''}">
                <a class="page-link" href="#" data-page="${currentPage - 1}">
                    <i class="bi bi-chevron-left"></i>
                </a>
            </li>
        `;

        // Pages
        const startPage = Math.max(1, currentPage - 2);
        const endPage = Math.min(totalPages, currentPage + 2);

        if (startPage > 1) {
            html += '<li class="page-item"><a class="page-link" href="#" data-page="1">1</a></li>';
            if (startPage > 2) {
                html += '<li class="page-item disabled"><span class="page-link">...</span></li>';
            }
        }

        for (let i = startPage; i <= endPage; i++) {
            html += `
                <li class="page-item ${i === currentPage ? 'active' : ''}">
                    <a class="page-link" href="#" data-page="${i}">${i}</a>
                </li>
            `;
        }

        if (endPage < totalPages) {
            if (endPage < totalPages - 1) {
                html += '<li class="page-item disabled"><span class="page-link">...</span></li>';
            }
            html += `<li class="page-item"><a class="page-link" href="#" data-page="${totalPages}">${totalPages}</a></li>`;
        }

        // Bouton suivant
        html += `
            <li class="page-item ${currentPage === totalPages ? 'disabled' : ''}">
                <a class="page-link" href="#" data-page="${currentPage + 1}">
                    <i class="bi bi-chevron-right"></i>
                </a>
            </li>
        `;

        html += '</ul></nav>';

        // Info sur les résultats
        const start = (currentPage - 1) * 20 + 1;
        const end = Math.min(currentPage * 20, totalItems);
        html += `<div class="text-center text-muted mt-2">
            Affichage de ${start} à ${end} sur ${totalItems} résultats
        </div>`;

        this.container.innerHTML = html;

        // Ajouter les événements
        this.container.querySelectorAll('a[data-page]').forEach(link => {
            link.addEventListener('click', (e) => {
                e.preventDefault();
                const page = parseInt(e.target.closest('a').dataset.page);
                if (page && page !== this.currentPage && page >= 1 && page <= this.totalPages) {
                    this.onPageChange(page);
                }
            });
        });
    }
}

// Recherche avec debounce
function setupSearch(inputId, callback, delay = 300) {
    const input = document.getElementById(inputId);
    if (!input) return;

    const debouncedCallback = debounce(callback, delay);
    input.addEventListener('input', (e) => {
        debouncedCallback(e.target.value);
    });
}

// Fonctions d'alerte simplifiées
function showSuccess(message) {
    showAlert(message, 'success');
}

function showError(message) {
    showAlert(message, 'danger');
}

function showInfo(message) {
    showAlert(message, 'info');
}

function showWarning(message) {
    showAlert(message, 'warning');
}

function getToken() {
    // Plus de token en mémoire - authentification via cookies HttpOnly
    return window.authManager && window.authManager.isAuthenticatedSync() ? 'cookie-based' : null;
}

// Compatibilité: alias utilisé ailleurs
function getAuthToken() {
    return getToken();
}

// Fonction pour échapper le HTML
function escapeHtml(text) {
    if (text === null || text === undefined) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Export pour utilisation dans d'autres modules
window.utils = {
    safeLoadData,
    showAlert,
    showSuccess,
    showError,
    showInfo,
    showWarning,
    getToken,
    getAuthToken,
    escapeHtml,
    formatCurrency,
    formatDate,
    formatDateTime,
    isValidEmail,
    isValidPhone,
    generateNumber,
    debounce,
    confirmAction,
    setButtonLoading,
    clearForm,
    validateForm,
    handleValidationErrors,
    UtilsPagination,
    setupSearch
};

// Rendre les fonctions globalement disponibles
window.safeLoadData = safeLoadData;
window.showSuccess = showSuccess;
window.showError = showError;
window.showInfo = showInfo;
window.showWarning = showWarning;
window.getToken = getToken;
window.getAuthToken = getAuthToken;
window.escapeHtml = escapeHtml;
