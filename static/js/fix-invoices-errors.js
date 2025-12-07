/**
 * Ce script contient les correctifs pour les erreurs JavaScript dans la page des factures.
 * Il corrige deux problèmes principaux:
 * 1. "Cannot read properties of undefined (reading 'backdrop')" dans les modaux Bootstrap
 * 2. "Cannot read properties of null (reading 'insertAdjacentHTML')" dans les notifications toast
 */

// Correctif pour les erreurs de modaux et notifications
document.addEventListener('DOMContentLoaded', function() {
  // 1. Vérification de l'existence du conteneur d'alertes
  if (!document.getElementById('alerts-container')) {
    console.log('Création du conteneur d\'alertes manquant');
    const alertsContainer = document.createElement('div');
    alertsContainer.id = 'alerts-container';
    document.querySelector('main').prepend(alertsContainer);
  }

  // 2. S'assurer que les modaux sont correctement initialisés
  const modals = document.querySelectorAll('.modal');
  modals.forEach(modal => {
    // Vérifier si le modal existe déjà dans le DOM
    if (modal) {
      try {
        // Essayer de récupérer l'instance existante ou en créer une nouvelle
        const modalInstance = bootstrap.Modal.getInstance(modal) || new bootstrap.Modal(modal);
        
        // S'assurer que le modal a tous les éléments nécessaires
        if (!modal.querySelector('.modal-dialog') || !modal.querySelector('.modal-content')) {
          console.warn(`Modal ${modal.id} structure incomplete - may cause errors`);
        }
      } catch (e) {
        console.warn(`Error initializing modal ${modal.id}:`, e);
      }
    }
  });

  // 3. Surcharger la fonction showAlert pour être plus robuste
  if (typeof window.showAlert === 'function') {
    const originalShowAlert = window.showAlert;
    
    window.showAlert = function(message, type = 'info', duration = 5000) {
      try {
        // Essayer d'utiliser la fonction d'origine
        return originalShowAlert(message, type, duration);
      } catch (err) {
        // Fallback en cas d'erreur
        console.warn('Error showing alert, using fallback:', err);
        
        // Récupérer ou créer le conteneur d'alertes
        let alertsContainer = document.getElementById('alerts-container');
        if (!alertsContainer) {
          alertsContainer = document.createElement('div');
          alertsContainer.id = 'alerts-container';
          document.querySelector('main')?.prepend(alertsContainer);
        }
        
        // Créer l'alerte manuellement
        const alertId = 'alert-' + Date.now();
        const alertDiv = document.createElement('div');
        alertDiv.id = alertId;
        alertDiv.className = `alert alert-${type} alert-dismissible fade show`;
        
        // Déterminer l'icône
        const icons = {
          'success': 'check-circle-fill',
          'danger': 'exclamation-triangle-fill',
          'warning': 'exclamation-triangle-fill',
          'info': 'info-circle-fill'
        };
        const icon = icons[type] || 'info-circle-fill';
        
        // Définir le contenu
        alertDiv.innerHTML = `
          <i class="bi bi-${icon} me-2"></i>
          ${message}
          <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
        `;
        
        // Ajouter l'alerte au conteneur
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
    };
  }

  // 4. Correction de la fonction d'ouverture du modal des factures
  if (typeof window.openInvoiceModal === 'function') {
    const originalOpenInvoiceModal = window.openInvoiceModal;
    
    window.openInvoiceModal = function() {
      try {
        // Vérifier si le modal existe
        const modalEl = document.getElementById('invoiceModal');
        if (!modalEl) {
          console.error('Modal #invoiceModal not found in DOM');
          return;
        }
        
        // Appeler la fonction d'origine
        return originalOpenInvoiceModal();
      } catch (err) {
        console.error('Error opening invoice modal:', err);
        window.showAlert('Erreur lors de l\'ouverture du formulaire de facture', 'danger');
      }
    };
  }
});
